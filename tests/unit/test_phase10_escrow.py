import sqlite3
import pytest

from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, OperationState, PolicyResult
from src.domain.exceptions import ExternalExecutionError
from src.economy.ledger import DoubleEntryLedger, ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.consequential import ConsequentialExecutionManager
from src.governance.policy_engine import PolicyEngine
from src.persistence.database import Database
from src.settlement.simulated_provider import SimulatedConsequentialProvider


@pytest.fixture
def test_env():
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

    org = Organisation(
        id="org-test-1",
        tenant_id="tenant-demo",
        mission="Test consequential execution",
        treasury_balance=100,
    )
    analyst = AgentRecord(
        id="agent-fin",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=50,
        allowed_action_types=[ActionType.EXTERNAL_API_CALL, ActionType.DATA_FETCH],
    )
    org.agents["agent-fin"] = analyst

    return {
        "db": db,
        "ledger": ledger,
        "policy": policy,
        "provider": provider,
        "manager": manager,
        "org": org,
    }


def _proposal(cost=30, op_id="prop-1") -> ActionProposal:
    return ActionProposal(
        id=op_id,
        task_id="t-1",
        proposing_agent_id="agent-fin",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="sandbox://market_index_fund",
        parameters={"invoice_id": "inv-123"},
        requested_credits=cost,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Consequential escrow test",
    )


def test_successful_execution_commits_escrow(test_env):
    manager = test_env["manager"]
    ledger = test_env["ledger"]
    policy = test_env["policy"]
    org = test_env["org"]

    prop = _proposal(cost=30)
    decision = policy.evaluate(prop, org, ledger=ledger)
    assert decision.result == PolicyResult.APPROVED

    receipt = manager.execute_proposal(prop, decision, org)
    assert receipt.cost_credits == 30
    assert receipt.http_status == 200

    # Verify atomic settlement:
    # 70 in TREASURY, 0 in ESCROW, 30 in EXTERNAL_SINK
    assert ledger.get_balance(TREASURY) == 70
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 30
    assert ledger.verify_conservation()

    op = manager.repo.get_by_idempotency_key(f"{org.id}:{prop.id}")
    assert op.state == OperationState.SUCCEEDED
    assert op.provider_reference.startswith("ext-ref-")


def test_explicit_failure_rolls_back_escrow_to_treasury(test_env):
    manager = test_env["manager"]
    ledger = test_env["ledger"]
    policy = test_env["policy"]
    provider = test_env["provider"]
    org = test_env["org"]

    key = f"{org.id}:prop-fail"
    provider.set_failure_rule(key, "Insufficient counterparty funds")

    prop = _proposal(cost=25, op_id="prop-fail")
    decision = policy.evaluate(prop, org, ledger=ledger)

    with pytest.raises(ExternalExecutionError) as exc_info:
        manager.execute_proposal(prop, decision, org)
    assert "Insufficient counterparty funds" in str(exc_info.value)

    # Verify funds safely rolled back:
    # 100 in TREASURY, 0 in ESCROW, 0 in EXTERNAL_SINK
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0
    assert ledger.verify_conservation()

    op = manager.repo.get_by_idempotency_key(key)
    assert op.state == OperationState.FAILED


def test_timeout_preserves_escrow_in_unknown_state(test_env):
    manager = test_env["manager"]
    ledger = test_env["ledger"]
    policy = test_env["policy"]
    provider = test_env["provider"]
    org = test_env["org"]

    key = f"{org.id}:prop-timeout"
    provider.set_timeout_rule(key, provider_executes_in_background=False)

    prop = _proposal(cost=35, op_id="prop-timeout")
    decision = policy.evaluate(prop, org, ledger=ledger)

    with pytest.raises(ExternalExecutionError) as exc_info:
        manager.execute_proposal(prop, decision, org)
    assert "UNKNOWN" in str(exc_info.value)

    # CRITICAL INVARIANT: Escrow is NOT rolled back!
    # Credits must remain locked in ESCROW awaiting reconciliation.
    assert ledger.get_balance(TREASURY) == 65
    assert ledger.get_balance(ESCROW) == 35
    assert ledger.get_balance(EXTERNAL_SINK) == 0
    assert ledger.verify_conservation()

    op = manager.repo.get_by_idempotency_key(key)
    assert op.state == OperationState.UNKNOWN


def test_unknown_operation_blocks_blind_retry(test_env):
    manager = test_env["manager"]
    ledger = test_env["ledger"]
    policy = test_env["policy"]
    provider = test_env["provider"]
    org = test_env["org"]

    key = f"{org.id}:prop-retry"
    provider.set_timeout_rule(key, provider_executes_in_background=False)

    prop = _proposal(cost=10, op_id="prop-retry")
    decision = policy.evaluate(prop, org, ledger=ledger)

    # First attempt times out -> UNKNOWN
    with pytest.raises(ExternalExecutionError):
        manager.execute_proposal(prop, decision, org)

    # Blind re-execution attempt MUST be blocked!
    with pytest.raises(ExternalExecutionError) as exc_info:
        manager.execute_proposal(prop, decision, org)
    assert "must be reconciled before retry" in str(exc_info.value)

    # Escrow still firmly locked at 10
    assert ledger.get_balance(ESCROW) == 10
    assert ledger.get_balance(TREASURY) == 90
