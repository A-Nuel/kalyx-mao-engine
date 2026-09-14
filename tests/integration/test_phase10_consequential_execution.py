import os
import pytest

from src.audit.auditor import Auditor
from src.domain.entities import ActionProposal, AgentRecord, Organisation, PolicyDecision
from src.domain.enums import ActionType, AgentRole, OperationState
from src.domain.exceptions import ExternalExecutionError, IdempotencyConflict, UnauthorizedActionError
from src.economy.ledger import ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.consequential import ConsequentialExecutionManager, ConsequentialOperationRepository
from src.governance.policy_engine import PolicyEngine
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore, SqliteLedger, SqliteRepository
from src.settlement.reconciliation import ReconciliationService
from src.settlement.simulated_provider import SimulatedConsequentialProvider


def _build_org(org_id: str = "org-p10-adv", tenant_id: str = "tenant-demo", treasury: int = 100) -> Organisation:
    org = Organisation(
        id=org_id,
        tenant_id=tenant_id,
        mission="Adversarial and Crash Recovery Testing",
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


def _proposal(cost: int = 25, op_id: str = "prop-adv-1") -> ActionProposal:
    return ActionProposal(
        id=op_id,
        task_id="t-adv-1",
        proposing_agent_id="agent-fin",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="sandbox://market_index_fund",
        parameters={"asset": "ETH", "order_type": "MARKET"},
        requested_credits=cost,
        expected_value_score=0.92,
        risk_assessment="Low",
        rationale="Adversarial consequential integration test",
    )


def test_crash_recovery_preserves_external_boundary_and_settles_atomically(tmp_path):
    """Real Crash Recovery Boundary Test.

    Flow:
    1. Kalyx instance A initiates execution of an operation.
    2. Simulated provider executes the operation in background, but network transport drops (or Kalyx crashes right after dispatch).
    3. Kalyx process dies (all in-memory objects destroyed, db connection closed).
    4. Kalyx instance B starts up on the persisted database.
    5. The operation is loaded from disk in UNKNOWN state with escrow still locked.
    6. Blind retry is blocked.
    7. ReconciliationService queries provider status.
    8. Provider confirms success with cryptographic evidence.
    9. Exactly-once settlement commits ESCROW -> EXTERNAL_SINK.
    10. Auditor independently verifies the complete recovered operation.
    """
    db_file = str(tmp_path / "crash_recovery.db")
    secret = "crash-recovery-secret"

    # Simulated provider runs externally (independent state, lives outside Kalyx process memory)
    external_provider = SimulatedConsequentialProvider()

    # --- PROCESS A: STARTUP & INITIATE OPERATION ---
    db_a = Database(db_file)
    repo_a = SqliteRepository(db_a)
    event_store_a = SqliteEventStore(db_a, verify_on_startup=True)
    ledger_a = SqliteLedger(db_a, initial_treasury=100)
    policy_a = PolicyEngine(signing_secret=secret)

    org_a = _build_org("org-crash-test", "tenant-demo", treasury=100)
    repo_a.save_organisation(org_a)
    repo_a.save_agent(org_a.agents["agent-fin"], org_a.id)

    manager_a = ConsequentialExecutionManager(
        policy_engine=policy_a,
        ledger=ledger_a,
        provider=external_provider,
        db_conn=db_a.conn,
        event_store=event_store_a,
    )

    prop = _proposal(cost=30, op_id="prop-crash-1")
    decision_a = policy_a.evaluate(prop, org_a, ledger=ledger_a)

    key = f"{org_a.id}:{prop.id}"
    # Configure provider to simulate: provider executed in background, but connection to Kalyx dropped
    external_provider.set_timeout_rule(key, provider_executes_in_background=True)

    with pytest.raises(ExternalExecutionError) as exc_info:
        manager_a.execute_proposal(prop, decision_a, org_a)
    assert "UNKNOWN" in str(exc_info.value)

    # Invariant check before crash: Escrow is locked, NOT refunded!
    assert ledger_a.get_balance(TREASURY) == 70
    assert ledger_a.get_balance(ESCROW) == 30
    assert ledger_a.get_balance(EXTERNAL_SINK) == 0
    assert ledger_a.verify_conservation()

    # --- SIMULATE PROCESS CRASH ---
    # Close connection, delete all in-memory references to simulate process termination
    db_a.close()
    del db_a, repo_a, event_store_a, ledger_a, policy_a, org_a, manager_a

    # --- PROCESS B: RESTART ON PERSISTED DATABASE ---
    db_b = Database(db_file)
    event_store_b = SqliteEventStore(db_b, verify_on_startup=True)
    ledger_b = SqliteLedger(db_b, initial_treasury=0)
    repo_b = SqliteRepository(db_b)
    policy_b = PolicyEngine(signing_secret=secret)

    org_b = repo_b.load_organisation("org-crash-test", ledger=ledger_b)
    assert org_b is not None

    # Verify balances survived crash intact
    assert ledger_b.get_balance(TREASURY) == 70
    assert ledger_b.get_balance(ESCROW) == 30
    assert ledger_b.get_balance(EXTERNAL_SINK) == 0
    assert ledger_b.verify_conservation()

    op_repo_b = ConsequentialOperationRepository(db_b.conn)
    pending_ops = op_repo_b.list_for_org(org_b.id, state="unknown")
    assert len(pending_ops) == 1
    unresolved_op = pending_ops[0]
    assert unresolved_op.state == OperationState.UNKNOWN
    assert unresolved_op.amount == 30

    manager_b = ConsequentialExecutionManager(
        policy_engine=policy_b,
        ledger=ledger_b,
        provider=external_provider,
        db_conn=db_b.conn,
        event_store=event_store_b,
    )

    # Invariant: Blind retry with original decision must be blocked pending reconciliation!
    with pytest.raises(ExternalExecutionError) as retry_exc:
        manager_b.execute_proposal(prop, decision_a, org_b)
    assert "must be reconciled before retry" in str(retry_exc.value)

    # Invariant: Blind retry with newly minted decision on same key triggers idempotency conflict!
    decision_b_retry = policy_b.evaluate(prop, org_b, ledger=ledger_b)
    with pytest.raises(IdempotencyConflict):
        manager_b.execute_proposal(prop, decision_b_retry, org_b)

    # Reconciliation Service resolves against provider status
    reconciliation_b = ReconciliationService(
        repo=op_repo_b,
        ledger=ledger_b,
        provider=external_provider,
        event_store=event_store_b,
    )

    reconciled_op = reconciliation_b.reconcile_operation(unresolved_op.id, org_b)

    assert reconciled_op.state == OperationState.RECONCILED
    assert reconciled_op.provider_reference is not None

    # Economic settlement: exactly once
    assert ledger_b.get_balance(TREASURY) == 70
    assert ledger_b.get_balance(ESCROW) == 0
    assert ledger_b.get_balance(EXTERNAL_SINK) == 30
    assert ledger_b.verify_conservation()

    # Independent Auditor Verification
    auditor = Auditor(verification_secret=secret)
    verification = auditor.verify_consequential_operation(
        operation=reconciled_op,
        proposal=prop,
        decision=decision_a,
        org=org_b,
        ledger=ledger_b,
        event_store=event_store_b,
        policy_engine=policy_b,
    )
    assert verification.verified is True
    assert len(verification.failures) == 0

    db_b.close()


def test_authorization_token_replay_rejected_at_consequential_boundary(tmp_path):
    """Replaying an authorization token for a second consequential operation fails closed."""
    db = Database(str(tmp_path / "replay.db"))
    ledger = SqliteLedger(db, initial_treasury=100)
    secret = "replay-secret"
    policy = PolicyEngine(signing_secret=secret)
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org("org-replay-test", "tenant-demo", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(
        policy_engine=policy,
        ledger=ledger,
        provider=provider,
        db_conn=db.conn,
    )

    prop = _proposal(cost=20, op_id="prop-replay-1")
    decision = policy.evaluate(prop, org, ledger=ledger)

    # First execution succeeds
    receipt = manager.execute_proposal(prop, decision, org)
    assert receipt is not None
    assert ledger.get_balance(EXTERNAL_SINK) == 20

    # Attempt to reuse the token in a different proposal (Attacker replaying signed token)
    tampered_prop = _proposal(cost=20, op_id="prop-replay-malicious")
    tampered_decision = PolicyDecision(
        id="dec-tampered",
        proposal_id=tampered_prop.id,
        result=decision.result,
        evaluated_rules=decision.evaluated_rules,
        authorization_token=decision.authorization_token,  # Replayed token!
    )

    with pytest.raises(UnauthorizedActionError):
        manager.execute_proposal(tampered_prop, tampered_decision, org)

    # Attempt to execute again with same proposal but fresh operation id: Token already consumed!
    with pytest.raises(UnauthorizedActionError) as exc_consumed:
        # Create a second operation with a distinct idempotency key using the consumed token
        op2 = manager.create_operation(prop, decision, org, idempotency_key="key-replay-attempt")
        manager.authorize_operation(op2, prop, decision, org)
    assert "consumed" in str(exc_consumed.value).lower()

    # Balances strictly conserved
    assert ledger.get_balance(TREASURY) == 80
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 20
    assert ledger.verify_conservation()

    db.close()


def test_idempotency_conflict_detection(tmp_path):
    """Reusing the same idempotency key with different operation parameters is rejected."""
    db = Database(str(tmp_path / "idempotency_conflict.db"))
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret="idem-secret")
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    org = _build_org("org-idem-test", "tenant-demo", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(
        policy_engine=policy,
        ledger=ledger,
        provider=provider,
        db_conn=db.conn,
    )

    prop1 = _proposal(cost=20, op_id="prop-idem-1")
    decision1 = policy.evaluate(prop1, org, ledger=ledger)

    key = "stable-idem-key-1"
    manager.execute_proposal(prop1, decision1, org, idempotency_key=key)

    # Conflicting proposal with same key but different target/amount
    prop2 = ActionProposal(
        id="prop-idem-2",
        task_id="t-adv-1",
        proposing_agent_id="agent-fin",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="sandbox://verified_bonds",  # Different target!
        parameters={"asset": "BOND_US"},
        requested_credits=20,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Conflicting parameters",
    )
    decision2 = policy.evaluate(prop2, org, ledger=ledger)

    with pytest.raises(IdempotencyConflict):
        manager.execute_proposal(prop2, decision2, org, idempotency_key=key)

    db.close()


def test_cross_tenant_isolation_at_consequential_manager(tmp_path):
    """Operations belonging to one tenant cannot be accessed or manipulated by another."""
    db = Database(str(tmp_path / "tenant_isolation.db"))
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret="iso-secret")
    provider = SimulatedConsequentialProvider()
    repo = SqliteRepository(db)

    # Seed tenants
    db.conn.execute("INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)", ("tenant-1", "Tenant 1", "2026-09-14T00:00:00"))
    db.conn.execute("INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)", ("tenant-2", "Tenant 2", "2026-09-14T00:00:00"))

    org1 = _build_org("org-tenant-1", "tenant-1", treasury=100)
    org2 = _build_org("org-tenant-2", "tenant-2", treasury=100)
    repo.save_organisation(org1)
    repo.save_organisation(org2)
    repo.save_agent(org1.agents["agent-fin"], org1.id)
    repo.save_agent(org2.agents["agent-fin"], org2.id)

    manager = ConsequentialExecutionManager(
        policy_engine=policy,
        ledger=ledger,
        provider=provider,
        db_conn=db.conn,
    )

    prop = _proposal(cost=15, op_id="prop-iso-1")
    decision1 = policy.evaluate(prop, org1, ledger=ledger)

    # Execute under org1
    key = "shared-key-test"
    manager.execute_proposal(prop, decision1, org1, idempotency_key=key)

    # Org2 attempts to hijack or execute with the same key
    decision2 = policy.evaluate(prop, org2, ledger=ledger)
    with pytest.raises(UnauthorizedActionError) as exc_info:
        manager.execute_proposal(prop, decision2, org2, idempotency_key=key)
    assert "Cross-tenant or cross-organisation" in str(exc_info.value)

    db.close()
