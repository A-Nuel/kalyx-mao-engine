"""Repeatable Disaster Recovery Drill for Kalyx MAO Engine.

Verifies that:
1. Mission and organisation exist.
2. Treasury has resources.
3. Consequential operation exists in UNKNOWN state with escrowed funds.
4. Process crashes completely (all in-memory objects and connections destroyed).
5. Fresh application instance restarts from the authoritative persistent store.
6. State is restored without corruption:
   - UNKNOWN remains UNKNOWN (no blind retry).
   - Escrow remains intact.
   - Audit chain passes cryptographic hash verification across restart.
7. Post-recovery reconciliation successfully and atomically settles or refunds funds.
8. Audit chain remains completely intact post-reconciliation.
"""

import os
import uuid
from datetime import datetime

import pytest

from src.audit.auditor import Auditor
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, OperationState, OrgState
from src.domain.exceptions import ExternalExecutionError
from src.economy.ledger import ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.consequential import (
    ConsequentialExecutionManager,
    ConsequentialOperationRepository,
)
from src.governance.policy_engine import PolicyEngine
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore, SqliteLedger, SqliteRepository
from src.settlement.reconciliation import ReconciliationService
from src.settlement.simulated_provider import SimulatedConsequentialProvider


def _ensure_tenant(db: Database, tenant_id: str = "tenant-dr") -> None:
    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            (tenant_id, f"DR Tenant {tenant_id}", "active"),
        )


def _build_org(org_id: str, tenant_id: str = "tenant-dr", treasury: int = 100) -> Organisation:
    org = Organisation(
        id=org_id,
        tenant_id=tenant_id,
        mission="Disaster Recovery Mission",
        treasury_balance=treasury,
        state=OrgState.EXECUTING,
    )
    analyst = AgentRecord(
        id=f"{org_id}-agent-fin",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=50,
        allowed_action_types=[ActionType.EXTERNAL_API_CALL, ActionType.DATA_FETCH],
    )
    org.agents[analyst.id] = analyst
    return org


def _proposal(org_id: str, cost: int, op_id: str) -> ActionProposal:
    return ActionProposal(
        id=op_id,
        task_id=f"{org_id}-task-1",
        proposing_agent_id=f"{org_id}-agent-fin",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="api://market_data/v1/summary",
        parameters={"endpoint": "/settle", "payload": {"amount": cost}},
        requested_credits=cost,
        expected_value_score=0.9,
        risk_assessment="LOW",
        rationale="Consequential test action",
    )


def test_disaster_recovery_drill_with_successful_remote_settlement(tmp_path):
    db_file = str(tmp_path / "kalyx_dr_settle.db")
    secret = "dr-drill-policy-secret"
    org_id = f"mao-dr-{uuid.uuid4().hex[:8]}"

    # External provider state lives outside the local process memory
    external_provider = SimulatedConsequentialProvider()

    # =========================================================================
    # STEP 1: PRE-CRASH APPLICATION EXECUTION
    # =========================================================================
    db_a = Database(db_file)
    repo_a = SqliteRepository(db_a)
    event_store_a = SqliteEventStore(db_a, verify_on_startup=True)
    ledger_a = SqliteLedger(db_a, initial_treasury=100)
    policy_a = PolicyEngine(signing_secret=secret)

    _ensure_tenant(db_a, "tenant-dr")
    org_a = _build_org(org_id, treasury=100)
    repo_a.save_organisation(org_a)
    repo_a.save_agent(org_a.agents[f"{org_id}-agent-fin"], org_a.id)

    manager_a = ConsequentialExecutionManager(
        policy_engine=policy_a,
        ledger=ledger_a,
        provider=external_provider,
        db_conn=db_a.conn,
        event_store=event_store_a,
    )

    prop = _proposal(org_id, cost=25, op_id="prop-dr-1")
    decision_a = policy_a.evaluate(prop, org_a, ledger=ledger_a)
    assert decision_a.authorization_token is not None

    # Simulate provider timeout: external provider received it and settled in background,
    # but the network connection to Kalyx dropped before response returned.
    key = f"{org_a.id}:{prop.id}"
    external_provider.set_timeout_rule(key, provider_executes_in_background=True)

    with pytest.raises(ExternalExecutionError) as exc_info:
        manager_a.execute_proposal(prop, decision_a, org_a)
    assert "UNKNOWN" in str(exc_info.value)

    # Invariant: Escrow locked, balance conserved
    assert ledger_a.get_balance(TREASURY) == 75
    assert ledger_a.get_balance(ESCROW) == 25
    assert ledger_a.get_balance(EXTERNAL_SINK) == 0
    assert ledger_a.verify_conservation()

    # Audit chain valid pre-crash
    valid, err = event_store_a.verify_integrity()
    assert valid is True

    # =========================================================================
    # STEP 2: COMPLETE PROCESS TERMINATION (CRASH)
    # =========================================================================
    db_a.close()
    del db_a, repo_a, event_store_a, ledger_a, policy_a, org_a, manager_a

    # =========================================================================
    # STEP 3: RESTART APPLICATION ON PERSISTED DATABASE
    # =========================================================================
    db_b = Database(db_file)
    event_store_b = SqliteEventStore(db_b, verify_on_startup=True)
    ledger_b = SqliteLedger(db_b, initial_treasury=0)
    repo_b = SqliteRepository(db_b)
    op_repo_b = ConsequentialOperationRepository(db_b.conn)

    org_b = repo_b.load_organisation(org_id, ledger=ledger_b)
    assert org_b is not None

    # 1. Authoritative balances survived restart exactly
    assert ledger_b.get_balance(TREASURY) == 75
    assert ledger_b.get_balance(ESCROW) == 25
    assert ledger_b.get_balance(EXTERNAL_SINK) == 0
    assert ledger_b.verify_conservation()

    # 2. Consequential operation survived in UNKNOWN state
    recovered_op = op_repo_b.get_by_idempotency_key(key)
    assert recovered_op is not None
    assert recovered_op.state == OperationState.UNKNOWN
    assert recovered_op.amount == 25

    # 3. Cryptographic audit chain verified across process restart
    valid, err = event_store_b.verify_integrity()
    assert valid is True, f"Audit chain broken after restart: {err}"

    # =========================================================================
    # STEP 4: OPERATIONAL RECONCILIATION POST-RECOVERY
    # =========================================================================
    reconciliation_service = ReconciliationService(
        repo=op_repo_b,
        ledger=ledger_b,
        provider=external_provider,
        event_store=event_store_b,
    )

    reconciled = reconciliation_service.reconcile_operation(recovered_op.id, org_b)
    assert reconciled.state == OperationState.RECONCILED

    # 4. Economic atomicity: exactly 25 moved from ESCROW to EXTERNAL_SINK
    assert ledger_b.get_balance(TREASURY) == 75
    assert ledger_b.get_balance(ESCROW) == 0
    assert ledger_b.get_balance(EXTERNAL_SINK) == 25
    assert ledger_b.verify_conservation()

    # 5. Idempotent repeated reconciliation is a safe no-op
    reconciled_again = reconciliation_service.reconcile_operation(recovered_op.id, org_b)
    assert reconciled_again.state == OperationState.RECONCILED
    assert ledger_b.get_balance(EXTERNAL_SINK) == 25

    # 6. Audit chain remains completely intact including reconciliation event
    valid, err = event_store_b.verify_integrity()
    assert valid is True, f"Audit chain broken after reconciliation: {err}"

    db_b.close()


def test_disaster_recovery_drill_with_remote_failure_and_escrow_refund(tmp_path):
    db_file = str(tmp_path / "kalyx_dr_refund.db")
    secret = "dr-drill-policy-secret"
    org_id = f"mao-dr-{uuid.uuid4().hex[:8]}"

    external_provider = SimulatedConsequentialProvider()

    # Process A
    db_a = Database(db_file)
    repo_a = SqliteRepository(db_a)
    event_store_a = SqliteEventStore(db_a, verify_on_startup=True)
    ledger_a = SqliteLedger(db_a, initial_treasury=100)
    policy_a = PolicyEngine(signing_secret=secret)

    _ensure_tenant(db_a, "tenant-dr")
    org_a = _build_org(org_id, treasury=100)
    repo_a.save_organisation(org_a)
    repo_a.save_agent(org_a.agents[f"{org_id}-agent-fin"], org_a.id)

    manager_a = ConsequentialExecutionManager(
        policy_engine=policy_a,
        ledger=ledger_a,
        provider=external_provider,
        db_conn=db_a.conn,
        event_store=event_store_a,
    )

    prop = _proposal(org_id, cost=25, op_id="prop-dr-2")
    decision_a = policy_a.evaluate(prop, org_a, ledger=ledger_a)
    assert decision_a.authorization_token is not None

    key = f"{org_a.id}:{prop.id}"
    # Configure provider to timeout, but remotely fail
    external_provider.set_timeout_rule(key, provider_fails_in_background=True, failure_reason="Remote exchange rejection")

    with pytest.raises(ExternalExecutionError):
        manager_a.execute_proposal(prop, decision_a, org_a)

    assert ledger_a.get_balance(TREASURY) == 75
    assert ledger_a.get_balance(ESCROW) == 25
    assert ledger_a.verify_conservation()

    # Crash
    db_a.close()
    del db_a, repo_a, event_store_a, ledger_a, policy_a, org_a, manager_a

    # Process B (Restart)
    db_b = Database(db_file)
    event_store_b = SqliteEventStore(db_b, verify_on_startup=True)
    ledger_b = SqliteLedger(db_b, initial_treasury=0)
    repo_b = SqliteRepository(db_b)
    op_repo_b = ConsequentialOperationRepository(db_b.conn)

    org_b = repo_b.load_organisation(org_id, ledger=ledger_b)
    assert org_b is not None

    recovered_op = op_repo_b.get_by_idempotency_key(key)
    assert recovered_op is not None
    assert recovered_op.state == OperationState.UNKNOWN

    reconciliation_service = ReconciliationService(
        repo=op_repo_b,
        ledger=ledger_b,
        provider=external_provider,
        event_store=event_store_b,
    )

    reconciled = reconciliation_service.reconcile_operation(recovered_op.id, org_b)
    assert reconciled.state == OperationState.RECONCILED

    # Full refund back to TREASURY: 65 + 35 = 100
    assert ledger_b.get_balance(ESCROW) == 0
    assert ledger_b.get_balance(TREASURY) == 100
    assert ledger_b.get_balance(EXTERNAL_SINK) == 0
    assert ledger_b.verify_conservation()

    # Audit chain verified
    valid, err = event_store_b.verify_integrity()
    assert valid is True

    db_b.close()
