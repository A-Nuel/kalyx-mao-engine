"""Phase 10 Adversarial Verification Gate.

Exhaustively attacks:
1. Every crash boundary:
   - before escrow
   - after escrow but before provider submission
   - after provider submission but before local state update
   - after provider success but before escrow commit
   - after provider failure but before escrow refund
   - during reconciliation
   - after settlement but before audit completion
2. Concurrent reconciliation of the same UNKNOWN operation.
3. Provider SUCCESS + Kalyx crash + restart.
4. Provider FAILURE + Kalyx crash + restart.
5. Provider PENDING across repeated reconciliation attempts.
6. Duplicate idempotency keys with identical and conflicting payloads.
7. Authorization token tampering:
   - amount
   - target
   - action
   - proposal content
   - policy version
   - organisation
   - operation binding
8. Cross-tenant operation read/reconcile attempts.
9. Verify that every terminal economic state has an unambiguous ledger result:
   - SUCCESS -> exactly one escrow commit
   - FAILURE -> exactly one escrow rollback
   - UNKNOWN/PENDING -> escrow remains preserved
10. Verify audit events remain consistent with the authoritative operation state.
"""

from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import pytest

from src.audit.auditor import Auditor, AuditVerificationError
from src.domain.entities import (
    ActionProposal,
    AgentRecord,
    AuthorizationTokenClaims,
    ConsequentialOperation,
    Organisation,
    PolicyDecision,
)
from src.domain.enums import ActionType, AgentRole, OperationState, PolicyResult, ProviderOutcome
from src.domain.exceptions import (
    ExternalExecutionError,
    IdempotencyConflict,
    ReconciliationError,
    UnauthorizedActionError,
)
from src.economy.ledger import ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.consequential import ConsequentialExecutionManager, ConsequentialOperationRepository
from src.governance.policy_engine import PolicyEngine
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore, SqliteLedger, SqliteRepository
from src.settlement.adapter import ProviderExecutionResult, ProviderStatusResult
from src.settlement.reconciliation import ReconciliationService
from src.settlement.simulated_provider import SimulatedConsequentialProvider


def _build_org(
    db: Database,
    org_id: str = "org-adv-1",
    tenant_id: str = "tenant-adv-1",
    treasury: int = 100,
) -> Organisation:
    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            (tenant_id, f"Workspace {tenant_id}", "active"),
        )
    org = Organisation(
        id=org_id,
        tenant_id=tenant_id,
        mission="Phase 10 Adversarial Boundary Verification",
        treasury_balance=treasury,
    )
    analyst = AgentRecord(
        id="agent-fin",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=50,
        allowed_action_types=[ActionType.EXTERNAL_API_CALL, ActionType.DATA_FETCH],
    )
    org.agents["agent-fin"] = analyst
    return org


def _proposal(
    cost: int = 25,
    prop_id: str = "prop-adv-1",
    action_type: ActionType = ActionType.EXTERNAL_API_CALL,
    target: str = "sandbox://market_index_fund",
) -> ActionProposal:
    return ActionProposal(
        id=prop_id,
        task_id="t-adv-1",
        proposing_agent_id="agent-fin",
        action_type=action_type,
        target=target,
        parameters={"asset": "BTC", "order_type": "LIMIT", "price": 50000},
        requested_credits=cost,
        expected_value_score=0.95,
        risk_assessment="Low",
        rationale="Adversarial testing of consequential boundary",
    )


# ==============================================================================
# ATTACK 1: CRASH BOUNDARY MATRIX
# ==============================================================================

def test_adversarial_crash_before_escrow(tmp_path):
    """Crash Boundary: Process dies after AUTHORIZED but BEFORE escrow_operation.
    Invariant: Treasury intact, escrow is 0. On restart, re-execution completes cleanly without double spend.
    """
    db_file = str(tmp_path / "crash_before_escrow.db")
    secret = "adv-secret"
    db_1 = Database(db_file)
    ledger_1 = SqliteLedger(db_1, initial_treasury=100)
    policy_1 = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo_1 = SqliteRepository(db_1)

    org_1 = _build_org(db_1, "org-cbe", "tenant-cbe", treasury=100)
    repo_1.save_organisation(org_1)
    repo_1.save_agent(org_1.agents["agent-fin"], org_1.id)

    manager_1 = ConsequentialExecutionManager(policy_1, ledger_1, provider, db_conn=db_1.conn)
    prop = _proposal(cost=20, prop_id="p-cbe-1")
    dec = policy_1.evaluate(prop, org_1, ledger=ledger_1)

    # Step 1: Create and authorize
    op = manager_1.create_operation(prop, dec, org_1)
    manager_1.authorize_operation(op, prop, dec, org_1)

    # Pre-crash state
    assert op.state == OperationState.AUTHORIZED
    assert ledger_1.get_balance(TREASURY) == 100
    assert ledger_1.get_balance(ESCROW) == 0

    # SIMULATE CRASH
    db_1.close()
    del db_1, ledger_1, manager_1, org_1

    # RESTART
    db_2 = Database(db_file)
    ledger_2 = SqliteLedger(db_2, initial_treasury=0)
    policy_2 = PolicyEngine(signing_secret=secret)
    repo_2 = SqliteRepository(db_2)
    org_2 = repo_2.load_organisation("org-cbe", ledger=ledger_2)

    manager_2 = ConsequentialExecutionManager(policy_2, ledger_2, provider, db_conn=db_2.conn)
    # Resume execution of the same proposal/decision
    receipt = manager_2.execute_proposal(prop, dec, org_2)
    assert receipt is not None
    assert ledger_2.get_balance(TREASURY) == 80
    assert ledger_2.get_balance(ESCROW) == 0
    assert ledger_2.get_balance(EXTERNAL_SINK) == 20
    assert ledger_2.verify_conservation()
    db_2.close()


def test_adversarial_crash_after_escrow_before_provider_submission(tmp_path):
    """Crash Boundary: Process dies in ESCROWED state before provider dispatch.
    Invariant: Escrow is locked (not lost, not in treasury). On restart, resume executes without double escrow.
    """
    db_file = str(tmp_path / "crash_after_escrow.db")
    secret = "adv-secret"
    db_1 = Database(db_file)
    ledger_1 = SqliteLedger(db_1, initial_treasury=100)
    policy_1 = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo_1 = SqliteRepository(db_1)

    org_1 = _build_org(db_1, "org-cae", "tenant-cae", treasury=100)
    repo_1.save_organisation(org_1)
    repo_1.save_agent(org_1.agents["agent-fin"], org_1.id)

    manager_1 = ConsequentialExecutionManager(policy_1, ledger_1, provider, db_conn=db_1.conn)
    prop = _proposal(cost=25, prop_id="p-cae-1")
    dec = policy_1.evaluate(prop, org_1, ledger=ledger_1)

    op = manager_1.create_operation(prop, dec, org_1)
    manager_1.authorize_operation(op, prop, dec, org_1)
    manager_1.escrow_operation(op, org_1)

    assert op.state == OperationState.ESCROWED
    assert ledger_1.get_balance(TREASURY) == 75
    assert ledger_1.get_balance(ESCROW) == 25

    # SIMULATE CRASH
    db_1.close()
    del db_1, ledger_1, manager_1, org_1

    # RESTART
    db_2 = Database(db_file)
    ledger_2 = SqliteLedger(db_2, initial_treasury=0)
    policy_2 = PolicyEngine(signing_secret=secret)
    repo_2 = SqliteRepository(db_2)
    org_2 = repo_2.load_organisation("org-cae", ledger=ledger_2)

    manager_2 = ConsequentialExecutionManager(policy_2, ledger_2, provider, db_conn=db_2.conn)
    # Resume execution
    receipt = manager_2.execute_proposal(prop, dec, org_2)
    assert receipt is not None

    # Invariant: exactly 25 was settled, no double debit
    assert ledger_2.get_balance(TREASURY) == 75
    assert ledger_2.get_balance(ESCROW) == 0
    assert ledger_2.get_balance(EXTERNAL_SINK) == 25
    assert ledger_2.verify_conservation()
    db_2.close()


def test_adversarial_crash_after_provider_submission_before_local_update(tmp_path):
    """Crash Boundary: Provider executed, but process died before local state transitioned.
    Invariant: Operation is in SUBMITTED locally. Re-attempting execute_proposal is rejected as unresolved.
    Reconciliation queries provider, finds SUCCESS, and commits escrow.
    """
    db_file = str(tmp_path / "crash_after_submission.db")
    secret = "adv-secret"
    db = Database(db_file)
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org(db, "org-sub-crash", "tenant-sub", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(policy, ledger, provider, db_conn=db.conn)
    prop = _proposal(cost=30, prop_id="p-sub-1")
    dec = policy.evaluate(prop, org, ledger=ledger)

    op = manager.create_operation(prop, dec, org)
    manager.authorize_operation(op, prop, dec, org)
    manager.escrow_operation(op, org)

    # Transition to SUBMITTED and save
    op.transition_to(OperationState.SUBMITTED)
    manager.repo.save(op)

    # Provider executed externally
    provider.record_external_execution(
        idempotency_key=op.idempotency_key,
        operation_id=op.id,
        outcome=ProviderOutcome.SUCCESS,
        provider_reference="ext-ref-sub-crash",
    )

    # Invariant: execute_proposal must reject retry on SUBMITTED operation
    with pytest.raises(ExternalExecutionError) as exc_info:
        manager.execute_proposal(prop, dec, org)
    assert "must be reconciled before retry" in str(exc_info.value)

    # Reconcile
    reconciliation = ReconciliationService(manager.repo, ledger, provider)
    reconciled = reconciliation.reconcile_operation(op.id, org)
    assert reconciled.state == OperationState.RECONCILED
    assert ledger.get_balance(TREASURY) == 70
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 30
    assert ledger.verify_conservation()
    db.close()


def test_adversarial_crash_after_provider_success_before_escrow_commit(tmp_path):
    """Crash Boundary: Provider returned SUCCESS, but process crashed before escrow was committed.
    Invariant: Reconciliation finds SUCCESS, executes escrow commit. Total commit transactions = 1.
    """
    db_file = str(tmp_path / "crash_before_commit.db")
    secret = "adv-secret"
    db = Database(db_file)
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org(db, "org-succ-commit", "tenant-commit", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(policy, ledger, provider, db_conn=db.conn)
    prop = _proposal(cost=20, prop_id="p-sbc-1")
    dec = policy.evaluate(prop, org, ledger=ledger)

    op = manager.create_operation(prop, dec, org)
    manager.authorize_operation(op, prop, dec, org)
    manager.escrow_operation(op, org)
    op.transition_to(OperationState.SUBMITTED)
    manager.repo.save(op)

    # External provider succeeded
    provider.record_external_execution(
        idempotency_key=op.idempotency_key,
        operation_id=op.id,
        outcome=ProviderOutcome.SUCCESS,
        provider_reference="ref-sbc",
    )

    reconciliation = ReconciliationService(manager.repo, ledger, provider)
    reconciled = reconciliation.reconcile_operation(op.id, org)
    assert reconciled.state == OperationState.RECONCILED
    assert ledger.get_balance(EXTERNAL_SINK) == 20
    assert ledger.get_balance(ESCROW) == 0

    # Verify exactly one commit transaction in ledger
    sink_entries = [e for e in ledger.get_entries() if e.to_account == EXTERNAL_SINK]
    assert len(sink_entries) == 1
    db.close()


def test_adversarial_crash_after_provider_failure_before_escrow_refund(tmp_path):
    """Crash Boundary: Provider returned FAILURE, but process crashed before escrow refund.
    Invariant: Reconciliation finds FAILURE, executes escrow rollback. Treasury is fully restored.
    """
    db_file = str(tmp_path / "crash_before_refund.db")
    secret = "adv-secret"
    db = Database(db_file)
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org(db, "org-fail-refund", "tenant-refund", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(policy, ledger, provider, db_conn=db.conn)
    prop = _proposal(cost=35, prop_id="p-sbr-1")
    dec = policy.evaluate(prop, org, ledger=ledger)

    op = manager.create_operation(prop, dec, org)
    manager.authorize_operation(op, prop, dec, org)
    manager.escrow_operation(op, org)
    op.transition_to(OperationState.SUBMITTED)
    manager.repo.save(op)

    # External provider explicitly failed
    provider.record_external_execution(
        idempotency_key=op.idempotency_key,
        operation_id=op.id,
        outcome=ProviderOutcome.FAILURE,
        provider_reference="ref-fail",
    )

    reconciliation = ReconciliationService(manager.repo, ledger, provider)
    reconciled = reconciliation.reconcile_operation(op.id, org)
    assert reconciled.state == OperationState.RECONCILED
    assert "failure" in (reconciled.error_message or "").lower() or "500" in (reconciled.error_message or "")
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0
    assert ledger.verify_conservation()
    db.close()


def test_adversarial_crash_during_reconciliation(tmp_path):
    """Crash Boundary: Process dies while in RECONCILING state.
    Invariant: On restart, subsequent reconciliation continues cleanly and finalizes.
    """
    db_file = str(tmp_path / "crash_reconciling.db")
    secret = "adv-secret"
    db_1 = Database(db_file)
    ledger_1 = SqliteLedger(db_1, initial_treasury=100)
    policy_1 = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo_1 = SqliteRepository(db_1)

    org_1 = _build_org(db_1, "org-cdr", "tenant-cdr", treasury=100)
    repo_1.save_organisation(org_1)
    repo_1.save_agent(org_1.agents["agent-fin"], org_1.id)

    manager_1 = ConsequentialExecutionManager(policy_1, ledger_1, provider, db_conn=db_1.conn)
    prop = _proposal(cost=20, prop_id="p-cdr-1")
    dec = policy_1.evaluate(prop, org_1, ledger=ledger_1)

    op = manager_1.create_operation(prop, dec, org_1)
    manager_1.authorize_operation(op, prop, dec, org_1)
    manager_1.escrow_operation(op, org_1)
    op.transition_to(OperationState.SUBMITTED)
    op.transition_to(OperationState.UNKNOWN, error_message="Timeout")
    op.transition_to(OperationState.RECONCILING)
    manager_1.repo.save(op)

    # External provider succeeded
    provider.record_external_execution(
        idempotency_key=op.idempotency_key,
        operation_id=op.id,
        outcome=ProviderOutcome.SUCCESS,
        provider_reference="ref-cdr",
    )

    # SIMULATE CRASH WHILE RECONCILING
    db_1.close()
    del db_1, ledger_1, manager_1, org_1

    # RESTART
    db_2 = Database(db_file)
    ledger_2 = SqliteLedger(db_2, initial_treasury=0)
    repo_2 = SqliteRepository(db_2)
    org_2 = repo_2.load_organisation("org-cdr", ledger=ledger_2)
    op_repo_2 = ConsequentialOperationRepository(db_2.conn)

    reconciliation_2 = ReconciliationService(op_repo_2, ledger_2, provider)
    reconciled = reconciliation_2.reconcile_operation(op.id, org_2)
    assert reconciled.state == OperationState.RECONCILED
    assert ledger_2.get_balance(TREASURY) == 80
    assert ledger_2.get_balance(EXTERNAL_SINK) == 20
    assert ledger_2.verify_conservation()
    db_2.close()


def test_adversarial_crash_after_settlement_before_state_save(tmp_path):
    """Crash Boundary: Ledger was settled, but process crashed before saving op state.
    Invariant: Subsequent reconciliation does NOT double-settle ledger credits.
    """
    db_file = str(tmp_path / "crash_after_settle.db")
    secret = "adv-secret"
    db = Database(db_file)
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org(db, "org-cas", "tenant-cas", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(policy, ledger, provider, db_conn=db.conn)
    prop = _proposal(cost=20, prop_id="p-cas-1")
    dec = policy.evaluate(prop, org, ledger=ledger)

    op = manager.create_operation(prop, dec, org)
    manager.authorize_operation(op, prop, dec, org)
    manager.escrow_operation(op, org)
    op.transition_to(OperationState.SUBMITTED)
    op.transition_to(OperationState.UNKNOWN)
    manager.repo.save(op)

    # Provider succeeded
    provider.record_external_execution(
        idempotency_key=op.idempotency_key,
        operation_id=op.id,
        outcome=ProviderOutcome.SUCCESS,
        provider_reference="ref-cas",
    )

    # Manually execute ledger settlement (simulating crash right after transfer, before op.save)
    tx_id = f"tx-rec-{hashlib.sha256(op.id.encode('utf-8')).hexdigest()[:16]}"
    ledger.transfer(
        from_account=ESCROW,
        to_account=EXTERNAL_SINK,
        amount=op.amount,
        memo=f"Reconciliation settlement for operation {op.id}",
        transaction_id=tx_id,
    )

    # Reconcile again
    reconciliation = ReconciliationService(manager.repo, ledger, provider)
    reconciled = reconciliation.reconcile_operation(op.id, org)
    assert reconciled.state == OperationState.RECONCILED

    # Crucial Invariant: EXTERNAL_SINK must still have exactly 20, NOT 40!
    assert ledger.get_balance(EXTERNAL_SINK) == 20
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(TREASURY) == 80
    assert ledger.verify_conservation()
    db.close()


# ==============================================================================
# ATTACK 2: CONCURRENT RECONCILIATION OF SAME UNKNOWN OPERATION
# ==============================================================================

def test_adversarial_concurrent_reconciliation(tmp_path):
    """Attack: Two concurrent threads attempt to reconcile the exact same UNKNOWN operation.
    Invariant: Exactly one ledger settlement occurs; both threads cleanly return RECONCILED; no double-spend.
    """
    db_file = str(tmp_path / "concurrent_rec.db")
    secret = "adv-secret"
    db = Database(db_file)
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org(db, "org-concurrent", "tenant-concurrent", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(policy, ledger, provider, db_conn=db.conn)
    prop = _proposal(cost=25, prop_id="p-conc-1")
    dec = policy.evaluate(prop, org, ledger=ledger)

    op = manager.create_operation(prop, dec, org)
    manager.authorize_operation(op, prop, dec, org)
    manager.escrow_operation(op, org)
    op.transition_to(OperationState.SUBMITTED)
    op.transition_to(OperationState.UNKNOWN, error_message="Timeout")
    manager.repo.save(op)

    # Provider succeeds in background
    provider.record_external_execution(
        idempotency_key=op.idempotency_key,
        operation_id=op.id,
        outcome=ProviderOutcome.SUCCESS,
        provider_reference="ref-concurrent",
    )

    reconciliation = ReconciliationService(manager.repo, ledger, provider)

    # Run concurrent reconciliations
    results = []
    errors = []

    def reconcile_worker():
        try:
            return reconciliation.reconcile_operation(op.id, org)
        except Exception as e:
            return e

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(reconcile_worker) for _ in range(4)]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if isinstance(res, Exception):
                errors.append(res)
            else:
                results.append(res)

    assert len(errors) == 0, f"Concurrent reconciliation failed with errors: {errors}"
    assert len(results) == 4
    for res in results:
        assert res.state == OperationState.RECONCILED

    # INVARIANT: Only ONE commit occurred in ledger!
    assert ledger.get_balance(EXTERNAL_SINK) == 25
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(TREASURY) == 75
    assert ledger.verify_conservation()
    db.close()


# ==============================================================================
# ATTACKS 3 & 4: PROVIDER SUCCESS / FAILURE + CRASH + RESTART
# ==============================================================================

def test_adversarial_provider_success_crash_restart(tmp_path):
    """Attack 3: Provider SUCCESS + Kalyx crash + restart.
    Verifies full lifecycle persistence, recovery, and auditor verification.
    """
    db_file = str(tmp_path / "prov_succ_restart.db")
    secret = "p10-succ-secret"
    db_1 = Database(db_file)
    ledger_1 = SqliteLedger(db_1, initial_treasury=100)
    event_store_1 = SqliteEventStore(db_1, verify_on_startup=True)
    policy_1 = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo_1 = SqliteRepository(db_1)

    org_1 = _build_org(db_1, "org-psr", "tenant-psr", treasury=100)
    repo_1.save_organisation(org_1)
    repo_1.save_agent(org_1.agents["agent-fin"], org_1.id)

    manager_1 = ConsequentialExecutionManager(
        policy_1, ledger_1, provider, db_conn=db_1.conn, event_store=event_store_1
    )
    prop = _proposal(cost=30, prop_id="p-psr-1")
    dec_1 = policy_1.evaluate(prop, org_1, ledger=ledger_1)

    provider.set_timeout_rule(f"{org_1.id}:{prop.id}", provider_executes_in_background=True)

    with pytest.raises(ExternalExecutionError):
        manager_1.execute_proposal(prop, dec_1, org_1)

    # CRASH
    db_1.close()
    del db_1, ledger_1, manager_1, org_1

    # RESTART
    db_2 = Database(db_file)
    event_store_2 = SqliteEventStore(db_2, verify_on_startup=True)
    ledger_2 = SqliteLedger(db_2, initial_treasury=0)
    repo_2 = SqliteRepository(db_2)
    policy_2 = PolicyEngine(signing_secret=secret)
    org_2 = repo_2.load_organisation("org-psr", ledger=ledger_2)

    op_repo_2 = ConsequentialOperationRepository(db_2.conn)
    reconciliation_2 = ReconciliationService(op_repo_2, ledger_2, provider, event_store=event_store_2)
    ops = op_repo_2.list_for_org(org_2.id)
    reconciled = reconciliation_2.reconcile_operation(ops[0].id, org_2)

    assert reconciled.state == OperationState.RECONCILED
    assert ledger_2.get_balance(EXTERNAL_SINK) == 30
    assert ledger_2.get_balance(ESCROW) == 0
    assert ledger_2.get_balance(TREASURY) == 70

    # Independent audit
    auditor = Auditor(verification_secret=secret)
    verification = auditor.verify_consequential_operation(
        operation=reconciled,
        proposal=prop,
        decision=dec_1,
        org=org_2,
        ledger=ledger_2,
        event_store=event_store_2,
        policy_engine=policy_2,
    )
    assert verification.verified is True
    db_2.close()


def test_adversarial_provider_failure_crash_restart(tmp_path):
    """Attack 4: Provider FAILURE + Kalyx crash + restart.
    Verifies full lifecycle persistence, recovery, escrow refund, and auditor verification.
    """
    db_file = str(tmp_path / "prov_fail_restart.db")
    secret = "p10-fail-secret"
    db_1 = Database(db_file)
    ledger_1 = SqliteLedger(db_1, initial_treasury=100)
    event_store_1 = SqliteEventStore(db_1, verify_on_startup=True)
    policy_1 = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo_1 = SqliteRepository(db_1)

    org_1 = _build_org(db_1, "org-pfr", "tenant-pfr", treasury=100)
    repo_1.save_organisation(org_1)
    repo_1.save_agent(org_1.agents["agent-fin"], org_1.id)

    manager_1 = ConsequentialExecutionManager(
        policy_1, ledger_1, provider, db_conn=db_1.conn, event_store=event_store_1
    )
    prop = _proposal(cost=30, prop_id="p-pfr-1")
    dec_1 = policy_1.evaluate(prop, org_1, ledger=ledger_1)

    # Provider fails during timeout
    key = f"{org_1.id}:{prop.id}"
    provider.set_timeout_rule(
        key,
        provider_fails_in_background=True,
        failure_reason="Order rejected: insufficient liquidity",
    )

    with pytest.raises(ExternalExecutionError):
        manager_1.execute_proposal(prop, dec_1, org_1)

    # CRASH
    db_1.close()
    del db_1, ledger_1, manager_1, org_1

    # RESTART
    db_2 = Database(db_file)
    event_store_2 = SqliteEventStore(db_2, verify_on_startup=True)
    ledger_2 = SqliteLedger(db_2, initial_treasury=0)
    repo_2 = SqliteRepository(db_2)
    policy_2 = PolicyEngine(signing_secret=secret)
    org_2 = repo_2.load_organisation("org-pfr", ledger=ledger_2)

    op_repo_2 = ConsequentialOperationRepository(db_2.conn)
    reconciliation_2 = ReconciliationService(op_repo_2, ledger_2, provider, event_store=event_store_2)
    ops = op_repo_2.list_for_org(org_2.id)
    reconciled = reconciliation_2.reconcile_operation(ops[0].id, org_2)

    assert reconciled.state == OperationState.RECONCILED
    assert ledger_2.get_balance(TREASURY) == 100
    assert ledger_2.get_balance(ESCROW) == 0
    assert ledger_2.get_balance(EXTERNAL_SINK) == 0
    assert ledger_2.verify_conservation()

    auditor = Auditor(verification_secret=secret)
    verification = auditor.verify_consequential_operation(
        operation=reconciled,
        proposal=prop,
        decision=dec_1,
        org=org_2,
        ledger=ledger_2,
        event_store=event_store_2,
        policy_engine=policy_2,
    )
    assert verification.verified is True
    db_2.close()


# ==============================================================================
# ATTACK 5: PROVIDER PENDING ACROSS REPEATED RECONCILIATIONS
# ==============================================================================

def test_adversarial_provider_pending_repeated_reconciliations(tmp_path):
    """Attack 5: Provider returns PENDING across repeated reconciliations.
    Invariant: Escrow is preserved across all attempts; no credit loss; settles only when provider turns SUCCESS.
    """
    db = Database(str(tmp_path / "pending_repeated.db"))
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret="pending-secret")
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org(db, "org-pending", "tenant-pending", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(policy, ledger, provider, db_conn=db.conn)
    prop = _proposal(cost=20, prop_id="p-pend-1")
    dec = policy.evaluate(prop, org, ledger=ledger)

    key = f"{org.id}:{prop.id}"
    # Configure provider to report PENDING (neither success nor failure)
    provider.set_timeout_rule(key, provider_executes_in_background=False)

    with pytest.raises(ExternalExecutionError):
        manager.execute_proposal(prop, dec, org)

    op_repo = ConsequentialOperationRepository(db.conn)
    ops = op_repo.list_for_org(org.id)
    op = ops[0]

    reconciliation = ReconciliationService(op_repo, ledger, provider)

    # 3 repeated reconciliations while provider is PENDING
    for i in range(3):
        res = reconciliation.reconcile_operation(op.id, org)
        assert res.state == OperationState.UNKNOWN
        # Escrow remains locked every time
        assert ledger.get_balance(ESCROW) == 20
        assert ledger.get_balance(TREASURY) == 80
        assert ledger.get_balance(EXTERNAL_SINK) == 0

    # Now external provider finishes processing
    provider.record_external_execution(
        idempotency_key=op.idempotency_key,
        operation_id=op.id,
        outcome=ProviderOutcome.SUCCESS,
        target=op.target,
        amount=op.amount,
        provider_reference="ref-finally-done",
    )

    # 4th reconciliation resolves to SUCCESS
    res_final = reconciliation.reconcile_operation(op.id, org)
    assert res_final.state == OperationState.RECONCILED
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(TREASURY) == 80
    assert ledger.get_balance(EXTERNAL_SINK) == 20
    assert ledger.verify_conservation()
    db.close()


# ==============================================================================
# ATTACK 6: DUPLICATE IDEMPOTENCY KEYS (IDENTICAL VS CONFLICTING)
# ==============================================================================

def test_adversarial_idempotency_identical_and_conflicting_payloads(tmp_path):
    """Attack 6: Idempotency keys.
    Invariant:
    - Identical payload on same key returns existing receipt (no duplicate debit).
    - Conflicting payload on same key raises IdempotencyConflict.
    """
    db = Database(str(tmp_path / "idempotency_adv.db"))
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret="idem-secret")
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org(db, "org-idem", "tenant-idem", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(policy, ledger, provider, db_conn=db.conn)
    prop = _proposal(cost=20, prop_id="p-idem-1")
    dec = policy.evaluate(prop, org, ledger=ledger)

    key = "idem-key-fixed"

    # Execution 1: Success
    receipt_1 = manager.execute_proposal(prop, dec, org, idempotency_key=key)
    assert receipt_1 is not None
    assert ledger.get_balance(EXTERNAL_SINK) == 20

    # Execution 2: Same key, identical payload
    receipt_2 = manager.execute_proposal(prop, dec, org, idempotency_key=key)
    assert receipt_2 is not None
    assert receipt_2.cost_credits == 20
    # Invariant: zero additional debits
    assert ledger.get_balance(EXTERNAL_SINK) == 20

    # Execution 3: Same key, conflicting payload (different amount)
    conflicting_prop = copy.deepcopy(prop)
    conflicting_prop.requested_credits = 25
    dec_conflict = policy.evaluate(conflicting_prop, org, ledger=ledger)

    with pytest.raises(IdempotencyConflict):
        manager.execute_proposal(conflicting_prop, dec_conflict, org, idempotency_key=key)

    # Execution 4: Same key, conflicting target
    conflicting_target_prop = copy.deepcopy(prop)
    conflicting_target_prop.target = "sandbox://verified_bonds"
    dec_target_conflict = policy.evaluate(conflicting_target_prop, org, ledger=ledger)

    with pytest.raises(IdempotencyConflict):
        manager.execute_proposal(conflicting_target_prop, dec_target_conflict, org, idempotency_key=key)

    assert ledger.get_balance(EXTERNAL_SINK) == 20
    db.close()


# ==============================================================================
# ATTACK 7: AUTHORIZATION TOKEN TAMPERING
# ==============================================================================

def test_adversarial_token_tampering_matrix(tmp_path):
    """Attack 7: Comprehensive token tampering matrix.
    Tests tampering with: amount, target, action, proposal content, policy version, organisation.
    Invariant: ConsequentialExecutionManager rejects every tampered token with UnauthorizedActionError.
    """
    db = Database(str(tmp_path / "token_tamper.db"))
    ledger = SqliteLedger(db, initial_treasury=100)
    secret = "adv-tamper-secret"
    policy = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org(db, "org-tamper", "tenant-tamper", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(policy, ledger, provider, db_conn=db.conn)
    prop = _proposal(cost=20, prop_id="p-tamp-orig")
    dec = policy.evaluate(prop, org, ledger=ledger)
    original_token = dec.authorization_token

    # 1. Tamper amount in proposal
    tampered_prop_amount = copy.deepcopy(prop)
    tampered_prop_amount.requested_credits = 30
    with pytest.raises(UnauthorizedActionError):
        manager.execute_proposal(tampered_prop_amount, dec, org)

    # 2. Tamper target in proposal
    tampered_prop_target = copy.deepcopy(prop)
    tampered_prop_target.target = "sandbox://attacker_sink"
    with pytest.raises(UnauthorizedActionError):
        manager.execute_proposal(tampered_prop_target, dec, org)

    # 3. Tamper action type
    tampered_prop_action = copy.deepcopy(prop)
    tampered_prop_action.action_type = ActionType.DATA_FETCH
    with pytest.raises(UnauthorizedActionError):
        manager.execute_proposal(tampered_prop_action, dec, org)

    # 4. Tamper parameters (proposal content hash mismatch)
    tampered_prop_params = copy.deepcopy(prop)
    tampered_prop_params.parameters = {"asset": "ATTACK_TOKEN"}
    with pytest.raises(UnauthorizedActionError):
        manager.execute_proposal(tampered_prop_params, dec, org)

    # 5. Tamper organisation (token presented to different organisation)
    org_victim = _build_org(db, "org-victim", "tenant-tamper", treasury=100)
    repo.save_organisation(org_victim)
    repo.save_agent(org_victim.agents["agent-fin"], org_victim.id)
    with pytest.raises(UnauthorizedActionError):
        manager.execute_proposal(prop, dec, org_victim)

    # 6. Tamper token claims (modify policy version in claim)
    parts = original_token.split(".")
    claims = json.loads(AuthorizationTokenClaims.from_b64(parts[1]).model_dump_json())
    claims["policy_version_hash"] = "tampered-version-hash"
    import base64
    from src.domain.events import canonical_json
    tampered_b64 = base64.urlsafe_b64encode(canonical_json(claims).encode("utf-8")).decode("utf-8")
    tampered_token = f"{parts[0]}.{tampered_b64}.{parts[2]}"

    dec_tampered_token = copy.deepcopy(dec)
    dec_tampered_token.authorization_token = tampered_token

    with pytest.raises(UnauthorizedActionError):
        manager.execute_proposal(prop, dec_tampered_token, org)

    # Invariant: Not a single credit was escrowed or lost
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0
    db.close()


# ==============================================================================
# ATTACK 8: CROSS-TENANT OPERATION ACCESS & RECONCILIATION
# ==============================================================================

def test_adversarial_cross_tenant_isolation(tmp_path):
    """Attack 8: Tenant B attempts to read or reconcile Tenant A's consequential operation.
    Invariant: ReconciliationService rejects cross-tenant reconcile with UnauthorizedActionError.
    """
    db = Database(str(tmp_path / "cross_tenant.db"))
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret="cross-tenant-secret")
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    # Tenant A
    org_a = _build_org(db, "org-tenant-a", "tenant-alpha", treasury=100)
    repo.save_organisation(org_a)
    repo.save_agent(org_a.agents["agent-fin"], org_a.id)

    # Tenant B
    org_b = _build_org(db, "org-tenant-b", "tenant-beta", treasury=100)
    repo.save_organisation(org_b)
    repo.save_agent(org_b.agents["agent-fin"], org_b.id)

    manager = ConsequentialExecutionManager(policy, ledger, provider, db_conn=db.conn)
    prop_a = _proposal(cost=20, prop_id="p-alpha-1")
    dec_a = policy.evaluate(prop_a, org_a, ledger=ledger)

    # Operation created by Tenant A
    op_a = manager.create_operation(prop_a, dec_a, org_a)
    manager.authorize_operation(op_a, prop_a, dec_a, org_a)
    manager.escrow_operation(op_a, org_a)
    op_a.transition_to(OperationState.SUBMITTED)
    op_a.transition_to(OperationState.UNKNOWN)
    manager.repo.save(op_a)

    reconciliation = ReconciliationService(manager.repo, ledger, provider)

    # Attacker: Tenant B attempts to reconcile Tenant A's operation
    with pytest.raises(UnauthorizedActionError) as exc_info:
        reconciliation.reconcile_operation(op_a.id, org_b)
    assert "Isolation violation" in str(exc_info.value)

    # Attacker: Tenant B presents Tenant A's authorization token
    with pytest.raises(UnauthorizedActionError) as exc_info_token:
        manager.create_operation(prop_a, dec_a, org_b)
    assert "organisation mismatch" in str(exc_info_token.value).lower()

    # Attacker: Tenant B attempts to create an operation with its own valid token but Tenant A's idempotency key
    prop_b = _proposal(cost=20, prop_id="p-beta-1")
    dec_b = policy.evaluate(prop_b, org_b, ledger=ledger)
    with pytest.raises(UnauthorizedActionError) as exc_info_key:
        manager.create_operation(prop_b, dec_b, org_b, idempotency_key=op_a.idempotency_key)
    assert "Cross-tenant" in str(exc_info_key.value)

    db.close()


# ==============================================================================
# ATTACK 9: UNAMBIGUOUS LEDGER RESULTS FOR TERMINAL ECONOMIC STATES
# ==============================================================================

def test_adversarial_terminal_economic_states_unambiguous(tmp_path):
    """Attack 9: Exhaustively verify that every terminal state has an exact, unambiguous ledger result:
    - SUCCESS -> exactly one escrow commit (ESCROW -> EXTERNAL_SINK)
    - FAILURE -> exactly one escrow rollback (ESCROW -> TREASURY)
    - UNKNOWN/PENDING -> escrow preserved in ESCROW
    """
    db = Database(str(tmp_path / "ledger_unambiguous.db"))
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret="unambig-secret")
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org(db, "org-unambig", "tenant-unambig", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(policy, ledger, provider, db_conn=db.conn)

    # 1. SUCCESS
    prop_succ = _proposal(cost=20, prop_id="p-succ-term")
    dec_succ = policy.evaluate(prop_succ, org, ledger=ledger)
    manager.execute_proposal(prop_succ, dec_succ, org)

    # Invariant: Treasury=80, Escrow=0, ExternalSink=20
    assert ledger.get_balance(TREASURY) == 80
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 20
    assert ledger.verify_conservation()

    # 2. FAILURE
    prop_fail = _proposal(cost=25, prop_id="p-fail-term")
    dec_fail = policy.evaluate(prop_fail, org, ledger=ledger)
    provider.set_failure_rule(f"{org.id}:{prop_fail.id}", "Hard network failure")

    with pytest.raises(ExternalExecutionError):
        manager.execute_proposal(prop_fail, dec_fail, org)

    # Invariant: Treasury=80 (refunded), Escrow=0, ExternalSink=20
    assert ledger.get_balance(TREASURY) == 80
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 20
    assert ledger.verify_conservation()

    # 3. UNKNOWN / TIMEOUT
    prop_unk = _proposal(cost=15, prop_id="p-unk-term")
    dec_unk = policy.evaluate(prop_unk, org, ledger=ledger)
    provider.set_timeout_rule(f"{org.id}:{prop_unk.id}", provider_executes_in_background=False)

    with pytest.raises(ExternalExecutionError):
        manager.execute_proposal(prop_unk, dec_unk, org)

    # Invariant: Treasury=65, Escrow=15 (LOCKED, never lost or refunded), ExternalSink=20
    assert ledger.get_balance(TREASURY) == 65
    assert ledger.get_balance(ESCROW) == 15
    assert ledger.get_balance(EXTERNAL_SINK) == 20
    assert ledger.verify_conservation()
    db.close()


# ==============================================================================
# ATTACK 10: AUDIT EVENTS CONSISTENT WITH AUTHORITATIVE OPERATION STATE
# ==============================================================================

def test_adversarial_audit_events_lifecycle_consistency(tmp_path):
    """Attack 10: Verify append-only audit event chain consistency across full lifecycle.
    Tampering with audit events fails cryptographic integrity verification.
    """
    db = Database(str(tmp_path / "audit_events.db"))
    ledger = SqliteLedger(db, initial_treasury=100)
    event_store = SqliteEventStore(db, verify_on_startup=True)
    secret = "audit-events-secret"
    policy = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org(db, "org-audit-cons", "tenant-cons", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(
        policy, ledger, provider, db_conn=db.conn, event_store=event_store
    )
    prop = _proposal(cost=20, prop_id="p-cons-1")
    dec = policy.evaluate(prop, org, ledger=ledger)

    manager.execute_proposal(prop, dec, org)

    # Verify event types logged for consequential execution
    op_id = manager.repo.list_for_org(org.id)[0].id
    events = [e for e in event_store.get_events() if e.entity_id == op_id]
    event_types = [e.event_type for e in events]
    assert "CONSEQUENTIAL_OPERATION_CREATED" in event_types
    assert "CONSEQUENTIAL_OPERATION_AUTHORIZED" in event_types
    assert "CONSEQUENTIAL_OPERATION_ESCROWED" in event_types
    assert "CONSEQUENTIAL_OPERATION_SUBMITTED" in event_types
    assert "CONSEQUENTIAL_OPERATION_SUCCEEDED" in event_types

    # Verify audit chain integrity
    valid, err = event_store.verify_integrity()
    assert valid is True
    assert err is None

    # Tamper with an audit event directly in the database
    with db.conn:
        db.conn.execute("UPDATE audit_events SET payload = '{\"tampered\": true}' WHERE sequence_id = 1")

    # Cryptographic audit verification must catch the tamper
    valid_after_tamper, err_after_tamper = event_store.verify_integrity()
    assert valid_after_tamper is False
    assert "corrupted" in str(err_after_tamper).lower() or "mismatch" in str(err_after_tamper).lower()
    db.close()
