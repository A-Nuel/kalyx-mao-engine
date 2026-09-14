import pytest

from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, OperationState, PolicyResult
from src.domain.exceptions import ExternalExecutionError, UnauthorizedActionError
from src.economy.ledger import DoubleEntryLedger, ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.consequential import ConsequentialExecutionManager, ConsequentialOperationRepository
from src.governance.policy_engine import PolicyEngine
from src.persistence.database import Database
from src.settlement.reconciliation import ReconciliationService
from src.settlement.simulated_provider import SimulatedConsequentialProvider


@pytest.fixture
def reconciliation_env():
    db = Database(":memory:")
    ledger = DoubleEntryLedger(initial_treasury=100)
    policy = PolicyEngine(signing_secret="phase10-secret")
    provider = SimulatedConsequentialProvider()
    manager = ConsequentialExecutionManager(
        policy_engine=policy,
        ledger=ledger,
        provider=provider,
        db_conn=db.conn,
    )
    repo = ConsequentialOperationRepository(db.conn)
    service = ReconciliationService(
        repo=repo,
        ledger=ledger,
        provider=provider,
    )

    org = Organisation(
        id="org-test-1",
        tenant_id="tenant-demo",
        mission="Test reconciliation",
        treasury_balance=100,
    )
    analyst = AgentRecord(
        id="agent-fin",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=50,
        allowed_action_types=[ActionType.EXTERNAL_API_CALL],
    )
    org.agents["agent-fin"] = analyst

    return {
        "db": db,
        "ledger": ledger,
        "policy": policy,
        "provider": provider,
        "manager": manager,
        "repo": repo,
        "service": service,
        "org": org,
    }


def _proposal(cost=25, op_id="prop-rec-1") -> ActionProposal:
    return ActionProposal(
        id=op_id,
        task_id="t-rec-1",
        proposing_agent_id="agent-fin",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="sandbox://market_index_fund",
        parameters={"order_id": "ord-999"},
        requested_credits=cost,
        expected_value_score=0.95,
        risk_assessment="Low risk index buy",
        rationale="Reconciliation test proposal",
    )


def test_reconciliation_resolves_unknown_to_success(reconciliation_env):
    """When a provider timed out but executed in background, reconciliation commits escrow."""
    manager = reconciliation_env["manager"]
    ledger = reconciliation_env["ledger"]
    policy = reconciliation_env["policy"]
    provider = reconciliation_env["provider"]
    service = reconciliation_env["service"]
    org = reconciliation_env["org"]

    prop = _proposal(cost=30, op_id="prop-rec-success")
    key = f"{org.id}:{prop.id}"
    provider.set_timeout_rule(key, provider_executes_in_background=True)

    decision = policy.evaluate(prop, org, ledger=ledger)
    with pytest.raises(ExternalExecutionError):
        manager.execute_proposal(prop, decision, org)

    # Initial post-timeout check: escrow locked
    assert ledger.get_balance(TREASURY) == 70
    assert ledger.get_balance(ESCROW) == 30
    assert ledger.get_balance(EXTERNAL_SINK) == 0

    op = manager.repo.get_by_idempotency_key(key)
    assert op.state == OperationState.UNKNOWN

    # Reconcile operation
    reconciled_op = service.reconcile_operation(op.id, org)

    assert reconciled_op.state == OperationState.RECONCILED
    assert reconciled_op.provider_reference is not None

    # Economic settlement: ESCROW -> EXTERNAL_SINK
    assert ledger.get_balance(TREASURY) == 70
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 30
    assert ledger.verify_conservation()


def test_reconciliation_resolves_unknown_to_failure(reconciliation_env):
    """When a provider timed out and aborted, reconciliation safely refunds escrow."""
    manager = reconciliation_env["manager"]
    ledger = reconciliation_env["ledger"]
    policy = reconciliation_env["policy"]
    provider = reconciliation_env["provider"]
    service = reconciliation_env["service"]
    org = reconciliation_env["org"]

    prop = _proposal(cost=25, op_id="prop-rec-fail")
    key = f"{org.id}:{prop.id}"
    provider.set_timeout_rule(
        key,
        provider_fails_in_background=True,
        failure_reason="Downstream gateway rejected transaction",
    )

    decision = policy.evaluate(prop, org, ledger=ledger)
    with pytest.raises(ExternalExecutionError):
        manager.execute_proposal(prop, decision, org)

    assert ledger.get_balance(TREASURY) == 75
    assert ledger.get_balance(ESCROW) == 25
    assert ledger.get_balance(EXTERNAL_SINK) == 0

    op = manager.repo.get_by_idempotency_key(key)
    assert op.state == OperationState.UNKNOWN

    # Reconcile operation
    reconciled_op = service.reconcile_operation(op.id, org)

    assert reconciled_op.state == OperationState.RECONCILED
    assert reconciled_op.error_message is not None

    # Economic settlement: ESCROW -> TREASURY
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0
    assert ledger.verify_conservation()


def test_reconciliation_keeps_unknown_when_provider_remains_unresolved(reconciliation_env):
    """When provider has no definitive outcome, operation remains UNKNOWN with escrow locked."""
    manager = reconciliation_env["manager"]
    ledger = reconciliation_env["ledger"]
    policy = reconciliation_env["policy"]
    provider = reconciliation_env["provider"]
    service = reconciliation_env["service"]
    org = reconciliation_env["org"]

    prop = _proposal(cost=25, op_id="prop-rec-still-unknown")
    key = f"{org.id}:{prop.id}"
    # Timeout with neither success nor failure in background: provider has no record
    provider.set_timeout_rule(key, provider_executes_in_background=False, provider_fails_in_background=False)

    decision = policy.evaluate(prop, org, ledger=ledger)
    with pytest.raises(ExternalExecutionError):
        manager.execute_proposal(prop, decision, org)

    op = manager.repo.get_by_idempotency_key(key)
    assert op.state == OperationState.UNKNOWN

    # Reconcile operation
    reconciled_op = service.reconcile_operation(op.id, org)

    # Invariant: Must remain UNKNOWN and escrow must stay locked!
    assert reconciled_op.state == OperationState.UNKNOWN
    assert ledger.get_balance(TREASURY) == 75
    assert ledger.get_balance(ESCROW) == 25
    assert ledger.get_balance(EXTERNAL_SINK) == 0
    assert ledger.verify_conservation()


def test_reconciliation_duplicate_call_is_idempotent(reconciliation_env):
    """Duplicate reconciliation does not duplicate settlement or mutate balances twice."""
    manager = reconciliation_env["manager"]
    ledger = reconciliation_env["ledger"]
    policy = reconciliation_env["policy"]
    provider = reconciliation_env["provider"]
    service = reconciliation_env["service"]
    org = reconciliation_env["org"]

    prop = _proposal(cost=20, op_id="prop-rec-dup")
    key = f"{org.id}:{prop.id}"
    provider.set_timeout_rule(key, provider_executes_in_background=True)

    decision = policy.evaluate(prop, org, ledger=ledger)
    with pytest.raises(ExternalExecutionError):
        manager.execute_proposal(prop, decision, org)

    op = manager.repo.get_by_idempotency_key(key)

    # First reconciliation
    res1 = service.reconcile_operation(op.id, org)
    assert res1.state == OperationState.RECONCILED
    assert ledger.get_balance(TREASURY) == 80
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 20

    initial_entries_count = len(ledger.get_entries())

    # Second reconciliation (duplicate call)
    res2 = service.reconcile_operation(op.id, org)
    assert res2.state == OperationState.RECONCILED
    assert ledger.get_balance(TREASURY) == 80
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 20
    assert len(ledger.get_entries()) == initial_entries_count
    assert ledger.verify_conservation()


def test_reconciliation_enforces_tenant_and_org_isolation(reconciliation_env):
    """Reconciliation cannot be performed by a different organisation or tenant."""
    manager = reconciliation_env["manager"]
    ledger = reconciliation_env["ledger"]
    policy = reconciliation_env["policy"]
    provider = reconciliation_env["provider"]
    service = reconciliation_env["service"]
    org = reconciliation_env["org"]

    prop = _proposal(cost=15, op_id="prop-rec-isolation")
    key = f"{org.id}:{prop.id}"
    provider.set_timeout_rule(key, provider_executes_in_background=False)

    decision = policy.evaluate(prop, org, ledger=ledger)
    with pytest.raises(ExternalExecutionError):
        manager.execute_proposal(prop, decision, org)

    op = manager.repo.get_by_idempotency_key(key)

    # Attack: foreign org from same tenant attempts reconciliation
    alien_org = Organisation(
        id="org-alien",
        tenant_id="tenant-demo",
        mission="Adversary org",
    )
    with pytest.raises(UnauthorizedActionError):
        service.reconcile_operation(op.id, alien_org)

    # Attack: foreign org from foreign tenant attempts reconciliation
    foreign_tenant_org = Organisation(
        id="org-test-1",
        tenant_id="tenant-foreign",
        mission="Foreign tenant",
    )
    with pytest.raises(UnauthorizedActionError):
        service.reconcile_operation(op.id, foreign_tenant_org)

    # Balances remain untouched
    assert ledger.get_balance(ESCROW) == 15


def test_reconciliation_from_crashed_submitted_state(reconciliation_env):
    """Reconciliation recovers an operation left in SUBMITTED state from an abrupt crash."""
    manager = reconciliation_env["manager"]
    ledger = reconciliation_env["ledger"]
    policy = reconciliation_env["policy"]
    provider = reconciliation_env["provider"]
    service = reconciliation_env["service"]
    org = reconciliation_env["org"]

    prop = _proposal(cost=22, op_id="prop-rec-crash")
    key = f"{org.id}:{prop.id}"

    # Setup simulated provider to record as executed
    provider.record_external_execution(
        idempotency_key=key,
        operation_id="cop-crash-1",
        target=prop.target,
        amount=prop.requested_credits,
        provider_reference="ext-ref-crash-recovered",
    )

    decision = policy.evaluate(prop, org, ledger=ledger)
    op = manager.create_operation(prop, decision, org, idempotency_key=key)
    manager.authorize_operation(op, prop, decision, org)
    manager.escrow_operation(op, org)

    # Simulate crash right at SUBMITTED state (before manager completes)
    op.transition_to(OperationState.SUBMITTED)
    manager.repo.save(op)

    assert ledger.get_balance(ESCROW) == 22

    # Reconciliation runs after crash
    recovered_op = service.reconcile_operation(op.id, org)

    assert recovered_op.state == OperationState.RECONCILED
    assert recovered_op.provider_reference == "ext-ref-crash-recovered"
    assert ledger.get_balance(TREASURY) == 78
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 22
    assert ledger.verify_conservation()
