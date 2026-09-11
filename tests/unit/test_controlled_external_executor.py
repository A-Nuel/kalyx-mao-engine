import pytest
from src.domain.entities import ActionProposal, PolicyDecision, Organisation, AgentRecord, Task
from src.domain.enums import OrgState, AgentRole, ActionType, PolicyResult, TaskStatus
from src.domain.exceptions import UnauthorizedActionError, PolicyViolationError
from src.governance.policy_engine import PolicyEngine
from src.economy.ledger import DoubleEntryLedger, TREASURY, EXTERNAL_SINK
from src.execution.executor import ControlledExternalExecutor
from src.audit.auditor import Auditor
from src.audit.event_store import AppendOnlyEventStore

@pytest.fixture
def ext_exec_env():
    ledger = DoubleEntryLedger(initial_treasury=100)
    engine = PolicyEngine(signing_secret="ext-secret-1234")
    auditor = Auditor(verification_secret="ext-secret-1234")
    event_store = AppendOnlyEventStore()

    def mock_transport(target: str, params: dict):
        if "error" in target:
            return 500, {"error": "Simulated upstream failure"}
        return 200, {"target": target, "data": "verified_response_data", "params": params}

    executor = ControlledExternalExecutor(
        policy_engine=engine,
        ledger=ledger,
        allowlist={"api://market_data/v1/summary", "https://api.github.com/repos/", "https://httpbin.org/get"},
        mock_handler=mock_transport
    )

    org = Organisation(id="org-ext", mission="External Exec Test", treasury_balance=100, state=OrgState.EXECUTING)
    analyst = AgentRecord(
        id="agent-analyst",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=30,
        allowed_action_types=[ActionType.DATA_FETCH, ActionType.EXTERNAL_API_CALL]
    )
    org.agents["agent-analyst"] = analyst

    return {
        "ledger": ledger,
        "engine": engine,
        "auditor": auditor,
        "event_store": event_store,
        "executor": executor,
        "org": org,
        "analyst": analyst
    }

def test_controlled_external_execution_success(ext_exec_env):
    engine = ext_exec_env["engine"]
    executor = ext_exec_env["executor"]
    auditor = ext_exec_env["auditor"]
    org = ext_exec_env["org"]
    ledger = ext_exec_env["ledger"]
    event_store = ext_exec_env["event_store"]

    proposal = ActionProposal(
        id="prop-ext-01",
        task_id="t-ext-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.DATA_FETCH,
        target="api://market_data/v1/summary",
        parameters={"pair": "ETH-USD"},
        requested_credits=10,
        expected_value_score=0.95,
        risk_assessment="Low",
        rationale="Fetch authorized market prices"
    )

    decision = engine.evaluate(proposal, org)
    assert decision.result == PolicyResult.APPROVED

    receipt = executor.execute(proposal, decision, org)

    assert receipt.http_status == 200
    assert receipt.cost_credits == 10
    assert receipt.action_type == ActionType.DATA_FETCH
    assert receipt.target == "api://market_data/v1/summary"
    assert len(receipt.raw_response_hash) == 64
    assert ledger.get_balance(TREASURY) == 90
    assert ledger.get_balance(EXTERNAL_SINK) == 10

    # Independent Auditor verifies execution
    task = Task(id="t-ext-01", mission_id="org-ext", assigned_agent_id="agent-analyst", objective="Fetch data", allocated_credits=10, status=TaskStatus.APPROVED)
    verification = auditor.verify_execution(
        proposal=proposal,
        decision=decision,
        receipt=receipt,
        org=org,
        task=task,
        ledger=ledger,
        event_store=event_store,
        policy_engine=engine
    )
    assert verification.verified is True
    assert len(verification.failures) == 0

def test_disallowed_external_target_rejected(ext_exec_env):
    engine = ext_exec_env["engine"]
    executor = ext_exec_env["executor"]
    org = ext_exec_env["org"]

    proposal = ActionProposal(
        id="prop-ext-bad",
        task_id="t-ext-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="https://unauthorized-malicious-domain.io/drain",
        requested_credits=5,
        expected_value_score=0.5,
        risk_assessment="High",
        rationale="Rogue target"
    )

    # Allowlist rule in engine rejects or if custom allowlist in executor blocks it
    decision = engine.evaluate(proposal, org)
    # Even if an attacker bypassed policy or forged a decision, executor blocks it:
    fake_token = engine.generate_token(proposal, org, decision_id="dec-fake")
    fake_decision = PolicyDecision(id="dec-fake", proposal_id=proposal.id, result=PolicyResult.APPROVED, authorization_token=fake_token)

    with pytest.raises(UnauthorizedActionError) as exc:
        executor.execute(proposal, fake_decision, org)
    assert "not in the outbound allowlist" in str(exc.value)

def test_ssrf_attempt_blocked_by_executor(ext_exec_env):
    engine = ext_exec_env["engine"]
    executor = ext_exec_env["executor"]
    org = ext_exec_env["org"]

    # Even if an admin mistakenly put localhost in allowlist, SSRF protection blocks it
    executor.allowlist.add("http://127.0.0.1:8080/admin/delete")

    proposal = ActionProposal(
        id="prop-ssrf",
        task_id="t-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="http://127.0.0.1:8080/admin/delete",
        requested_credits=0,
        expected_value_score=0.1,
        risk_assessment="Extreme",
        rationale="Attempted internal pivot"
    )
    decision = engine.evaluate(proposal, org)
    token = engine.generate_token(proposal, org, decision_id=decision.id)
    decision.authorization_token = token
    decision.result = PolicyResult.APPROVED

    with pytest.raises(UnauthorizedActionError) as exc:
        executor.execute(proposal, decision, org)
    assert "forbidden loopback" in str(exc.value) or "SSRF blocked" in str(exc.value)

def test_external_executor_replay_protection(ext_exec_env):
    engine = ext_exec_env["engine"]
    executor = ext_exec_env["executor"]
    org = ext_exec_env["org"]

    proposal = ActionProposal(
        id="prop-ext-replay",
        task_id="t-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.DATA_FETCH,
        target="api://market_data/v1/summary",
        requested_credits=5,
        expected_value_score=0.8,
        risk_assessment="Low",
        rationale="Replay test"
    )

    decision = engine.evaluate(proposal, org)
    receipt1 = executor.execute(proposal, decision, org)
    assert receipt1 is not None

    with pytest.raises(UnauthorizedActionError) as exc:
        executor.execute(proposal, decision, org)
    assert "replay attack detected" in str(exc.value)
