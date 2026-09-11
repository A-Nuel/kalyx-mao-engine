import pytest
from src.domain.entities import ActionProposal, Organisation, AgentRecord
from src.domain.enums import OrgState, AgentRole, ActionType, PolicyResult
from src.domain.exceptions import UnauthorizedActionError, ExternalExecutionError
from src.governance.policy_engine import PolicyEngine
from src.economy.ledger import DoubleEntryLedger, TREASURY, ESCROW, EXTERNAL_SINK
from src.execution.executor import ControlledExternalExecutor

@pytest.fixture
def atomicity_env():
    ledger = DoubleEntryLedger(initial_treasury=100)
    engine = PolicyEngine(signing_secret="atomicity-secret-1234")
    org = Organisation(id="org-atom", mission="Atomicity Test", treasury_balance=100, state=OrgState.EXECUTING)
    analyst = AgentRecord(
        id="agent-analyst",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=30,
        allowed_action_types=[ActionType.DATA_FETCH, ActionType.EXTERNAL_API_CALL]
    )
    org.agents["agent-analyst"] = analyst

    return {"ledger": ledger, "engine": engine, "org": org, "analyst": analyst}

def make_proposal(target="api://market_data/v1/summary", cost=20):
    return ActionProposal(
        id="prop-atom-01",
        task_id="t-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.DATA_FETCH,
        target=target,
        parameters={"pair": "BTC-USD"},
        requested_credits=cost,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Test atomicity"
    )

def test_successful_execution_commits_escrow(atomicity_env):
    ledger = atomicity_env["ledger"]
    engine = atomicity_env["engine"]
    org = atomicity_env["org"]

    executor = ControlledExternalExecutor(
        policy_engine=engine,
        ledger=ledger,
        allowlist={"api://market_data/v1/summary"},
        mock_handler=lambda target, params: (200, {"data": "ok"})
    )

    prop = make_proposal(cost=20)
    decision = engine.evaluate(prop, org, ledger=ledger)
    assert decision.result == PolicyResult.APPROVED

    receipt = executor.execute(prop, decision, org)
    assert receipt.cost_credits == 20
    assert receipt.http_status == 200

    # Verify settlement balances: 80 in Treasury, 0 in Escrow, 20 in External Sink
    assert ledger.get_balance(TREASURY) == 80
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 20
    assert ledger.verify_conservation() is True

def test_timeout_rolls_back_escrow_to_treasury(atomicity_env):
    ledger = atomicity_env["ledger"]
    engine = atomicity_env["engine"]
    org = atomicity_env["org"]

    def timeout_mock(target, params):
        raise TimeoutError("Simulated socket timeout after 5.0s")

    executor = ControlledExternalExecutor(
        policy_engine=engine,
        ledger=ledger,
        allowlist={"api://market_data/v1/summary"},
        mock_handler=timeout_mock
    )

    prop = make_proposal(cost=25)
    decision = engine.evaluate(prop, org, ledger=ledger)

    with pytest.raises(ExternalExecutionError) as exc:
        executor.execute(prop, decision, org)
    assert "dispatch failed" in str(exc.value)

    # Invariant: Treasury completely restored! Escrow empty! Zero credits lost!
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0
    assert ledger.verify_conservation() is True

def test_http_500_rolls_back_escrow_to_treasury(atomicity_env):
    ledger = atomicity_env["ledger"]
    engine = atomicity_env["engine"]
    org = atomicity_env["org"]

    executor = ControlledExternalExecutor(
        policy_engine=engine,
        ledger=ledger,
        allowlist={"api://market_data/v1/summary"},
        mock_handler=lambda target, params: (500, {"error": "Internal Server Error"})
    )

    prop = make_proposal(cost=15)
    decision = engine.evaluate(prop, org, ledger=ledger)

    with pytest.raises(ExternalExecutionError) as exc:
        executor.execute(prop, decision, org)
    assert "HTTP status 500" in str(exc.value)

    # Treasury restored
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0

def test_connection_failure_rolls_back_escrow(atomicity_env):
    ledger = atomicity_env["ledger"]
    engine = atomicity_env["engine"]
    org = atomicity_env["org"]

    def conn_fail_mock(target, params):
        raise ConnectionResetError("Connection abruptly closed by peer")

    executor = ControlledExternalExecutor(
        policy_engine=engine,
        ledger=ledger,
        allowlist={"api://market_data/v1/summary"},
        mock_handler=conn_fail_mock
    )

    prop = make_proposal(cost=30)
    decision = engine.evaluate(prop, org, ledger=ledger)

    with pytest.raises(ExternalExecutionError):
        executor.execute(prop, decision, org)

    assert ledger.get_balance(TREASURY) == 100
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0

def test_no_double_settlement_on_replay(atomicity_env):
    ledger = atomicity_env["ledger"]
    engine = atomicity_env["engine"]
    org = atomicity_env["org"]

    executor = ControlledExternalExecutor(
        policy_engine=engine,
        ledger=ledger,
        allowlist={"api://market_data/v1/summary"},
        mock_handler=lambda target, params: (200, {"ok": True})
    )

    prop = make_proposal(cost=10)
    decision = engine.evaluate(prop, org, ledger=ledger)

    receipt = executor.execute(prop, decision, org)
    assert receipt is not None
    assert ledger.get_balance(TREASURY) == 90
    assert ledger.get_balance(EXTERNAL_SINK) == 10

    # Repeated call with the exact same approved decision/token
    with pytest.raises(UnauthorizedActionError) as exc:
        executor.execute(prop, decision, org)
    assert "replay attack detected" in str(exc.value)

    # Invariant: Balances must NOT be debited twice
    assert ledger.get_balance(TREASURY) == 90
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 10

def test_no_credit_loss_on_failed_external_execution(atomicity_env):
    ledger = atomicity_env["ledger"]
    engine = atomicity_env["engine"]
    org = atomicity_env["org"]

    fail_count = 0
    def intermittent_mock(target, params):
        nonlocal fail_count
        fail_count += 1
        return 503, {"error": "Service Unavailable"}

    executor = ControlledExternalExecutor(
        policy_engine=engine,
        ledger=ledger,
        allowlist={"api://market_data/v1/summary"},
        mock_handler=intermittent_mock
    )

    # 3 consecutive failed external attempts
    for i in range(3):
        prop = ActionProposal(
            id=f"prop-fail-{i}",
            task_id=f"t-{i}",
            proposing_agent_id="agent-analyst",
            action_type=ActionType.DATA_FETCH,
            target="api://market_data/v1/summary",
            requested_credits=20,
            expected_value_score=0.8,
            risk_assessment="Low",
            rationale="Intermittent failure"
        )
        decision = engine.evaluate(prop, org, ledger=ledger)
        with pytest.raises(ExternalExecutionError):
            executor.execute(prop, decision, org)

    # Even after 3 failures, zero credits are lost!
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0
    assert fail_count == 3
