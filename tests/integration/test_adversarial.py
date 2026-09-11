import pytest
from src.domain.entities import Organisation, AgentRecord, ActionProposal, PolicyDecision
from src.domain.enums import OrgState, AgentRole, AgentStatus, ActionType, PolicyResult
from src.domain.exceptions import UnauthorizedActionError, PolicyViolationError, InsufficientCreditsError
from src.governance.policy_engine import PolicyEngine
from src.governance.human_gate import HumanGate
from src.economy.ledger import DoubleEntryLedger, TREASURY, EXTERNAL_SINK
from src.execution.executor import SandboxExecutor

@pytest.fixture
def test_env():
    ledger = DoubleEntryLedger(initial_treasury=100)
    engine = PolicyEngine(signing_secret="super-secure-production-key-999")
    executor = SandboxExecutor(policy_engine=engine, ledger=ledger)
    human_gate = HumanGate()

    org = Organisation(id="org-test", mission="Adversarial Testing", treasury_balance=100, state=OrgState.EXECUTING)
    
    ceo = AgentRecord(
        id="agent-ceo",
        role=AgentRole.CEO,
        authority_ceiling=25,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION, ActionType.REPLAN]
    )
    researcher = AgentRecord(
        id="agent-research",
        role=AgentRole.RESEARCHER,
        authority_ceiling=15,
        allowed_action_types=[ActionType.DATA_FETCH, ActionType.INTERNAL_ANALYSIS]
    )
    suspended_agent = AgentRecord(
        id="agent-bad",
        role=AgentRole.STRATEGIST,
        authority_ceiling=0,
        status=AgentStatus.SUSPENDED,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS]
    )
    restricted_agent = AgentRecord(
        id="agent-probation",
        role=AgentRole.STRATEGIST,
        authority_ceiling=0,
        status=AgentStatus.RESTRICTED,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS]
    )

    org.agents["agent-ceo"] = ceo
    org.agents["agent-research"] = researcher
    org.agents["agent-bad"] = suspended_agent
    org.agents["agent-probation"] = restricted_agent

    return {
        "ledger": ledger,
        "engine": engine,
        "executor": executor,
        "human_gate": human_gate,
        "org": org,
        "ceo": ceo,
        "researcher": researcher,
        "suspended_agent": suspended_agent
    }

# 1. Forged authorization token
def test_forged_authorization_token_rejected(test_env):
    proposal = ActionProposal(
        id="prop-01",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=10,
        expected_value_score=0.8,
        risk_assessment="Low",
        rationale="Normal proposal"
    )
    # Attacker fabricates an approval with fake token
    fake_decision = PolicyDecision(
        id="fake-dec",
        proposal_id=proposal.id,
        result=PolicyResult.APPROVED,
        authorization_token="AUTH-prop-01-fakecounterfeithash12345678"
    )
    with pytest.raises(UnauthorizedActionError) as exc:
        test_env["executor"].execute(proposal, fake_decision, test_env["org"])
    assert "Invalid token" in str(exc.value)
    # Confirm ledger was not touched
    assert test_env["ledger"].get_balance(TREASURY) == 100

# 2. Modified proposal after approval (parameter tampering)
def test_modified_proposal_parameters_after_approval(test_env):
    proposal = ActionProposal(
        id="prop-02",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        parameters={"allocation_tier": "conservative"},
        requested_credits=10,
        expected_value_score=0.8,
        risk_assessment="Low",
        rationale="Approved proposal"
    )
    decision = test_env["engine"].evaluate(proposal, test_env["org"])
    assert decision.result == PolicyResult.APPROVED

    # Attacker silently tampers with parameters after approval
    proposal.parameters["allocation_tier"] = "ultra_aggressive_drain"

    with pytest.raises(UnauthorizedActionError) as exc:
        test_env["executor"].execute(proposal, decision, test_env["org"])
    assert "signature does not match proposal content" in str(exc.value)

# 3. Policy approval followed by proposal mutation (credit tampering)
def test_modified_proposal_credits_after_approval(test_env):
    proposal = ActionProposal(
        id="prop-03",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=10,
        expected_value_score=0.8,
        risk_assessment="Low",
        rationale="Modest 10 credits"
    )
    decision = test_env["engine"].evaluate(proposal, test_env["org"])
    assert decision.result == PolicyResult.APPROVED

    # Attacker changes requested_credits from 10 to 50
    proposal.requested_credits = 50

    with pytest.raises(UnauthorizedActionError) as exc:
        test_env["executor"].execute(proposal, decision, test_env["org"])
    assert "signature does not match proposal content" in str(exc.value)
    assert test_env["ledger"].get_balance(TREASURY) == 100

# 4. Replayed authorization token (Token reuse)
def test_replayed_authorization_token(test_env):
    proposal = ActionProposal(
        id="prop-04",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=10,
        expected_value_score=0.8,
        risk_assessment="Low",
        rationale="Single execution"
    )
    decision = test_env["engine"].evaluate(proposal, test_env["org"])
    assert decision.result == PolicyResult.APPROVED

    # First execution succeeds
    receipt1 = test_env["executor"].execute(proposal, decision, test_env["org"])
    assert receipt1.cost_credits == 10
    assert test_env["ledger"].get_balance(TREASURY) == 90

    # Attacker tries to replay the exact same token with the same proposal
    with pytest.raises(UnauthorizedActionError) as exc:
        test_env["executor"].execute(proposal, decision, test_env["org"])
    assert "already been consumed (replay attack detected)" in str(exc.value)
    # Confirm treasury was NOT double-debited
    assert test_env["ledger"].get_balance(TREASURY) == 90

# 5. Double execution prevention
def test_double_execution_prevention(test_env):
    proposal = ActionProposal(
        id="prop-05",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=15,
        expected_value_score=0.8,
        risk_assessment="Low",
        rationale="Test double execution"
    )
    decision = test_env["engine"].evaluate(proposal, test_env["org"])
    
    # Run 1
    test_env["executor"].execute(proposal, decision, test_env["org"])
    # Run 2 immediately
    with pytest.raises(UnauthorizedActionError):
        test_env["executor"].execute(proposal, decision, test_env["org"])

# 6. Insufficient treasury
def test_insufficient_treasury_rejection(test_env):
    proposal = ActionProposal(
        id="prop-06",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=20,
        expected_value_score=0.8,
        risk_assessment="Low",
        rationale="Drain treasury"
    )
    # Artificially set org treasury to 5
    test_env["org"].treasury_balance = 5

    decision = test_env["engine"].evaluate(proposal, test_env["org"])
    assert decision.result == PolicyResult.REJECTED
    assert decision.violated_rule_id == "RULE-02"
    assert "exceeds available treasury" in decision.violated_rule_description

# 7. Suspended agent attempting execution
def test_suspended_agent_attempt_rejected(test_env):
    proposal = ActionProposal(
        id="prop-07",
        task_id="task-01",
        proposing_agent_id="agent-bad",
        action_type=ActionType.INTERNAL_ANALYSIS,
        target="internal://research_synthesis",
        requested_credits=0,
        expected_value_score=0.5,
        risk_assessment="None",
        rationale="Suspended agent work"
    )
    decision = test_env["engine"].evaluate(proposal, test_env["org"])
    assert decision.result == PolicyResult.REJECTED
    assert decision.violated_rule_id == "RULE-07"
    assert "SUSPENDED" in decision.violated_rule_description

# 8. Paused organisation attempting execution (Emergency Kill Switch)
def test_paused_organisation_blocks_execution(test_env):
    proposal = ActionProposal(
        id="prop-08",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=10,
        expected_value_score=0.8,
        risk_assessment="Low",
        rationale="Valid proposal before pause"
    )
    decision = test_env["engine"].evaluate(proposal, test_env["org"])
    assert decision.result == PolicyResult.APPROVED

    # Operator triggers emergency kill switch BEFORE execution happens
    test_env["human_gate"].trigger_kill_switch(test_env["org"], "Suspicious market volatility detected")
    assert test_env["org"].state == OrgState.PAUSED

    # Execution MUST be blocked even though token was issued when ACTIVE!
    with pytest.raises(UnauthorizedActionError) as exc:
        test_env["executor"].execute(proposal, decision, test_env["org"])
    assert "PAUSED by emergency kill switch" in str(exc.value)
    # Treasury balance untouched
    assert test_env["ledger"].get_balance(TREASURY) == 100

# 9. Unauthorized role / action
def test_unauthorized_role_action(test_env):
    # Researcher attempting to execute capital allocation
    proposal = ActionProposal(
        id="prop-09",
        task_id="task-02",
        proposing_agent_id="agent-research",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=10,
        expected_value_score=0.7,
        risk_assessment="Moderate",
        rationale="Role violation attempt"
    )
    decision = test_env["engine"].evaluate(proposal, test_env["org"])
    assert decision.result == PolicyResult.REJECTED
    assert decision.violated_rule_id == "RULE-03"
    assert "not permitted for role 'RESEARCHER'" in decision.violated_rule_description

# 10. Allowlist bypass attempt
def test_allowlist_bypass_attempt(test_env):
    proposal = ActionProposal(
        id="prop-10",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="https://attacker-stealth-drain.internal/sink",
        requested_credits=10,
        expected_value_score=0.9,
        risk_assessment="High",
        rationale="Destination allowlist bypass"
    )
    decision = test_env["engine"].evaluate(proposal, test_env["org"])
    assert decision.result == PolicyResult.REJECTED
    assert decision.violated_rule_id == "RULE-04"
    assert "not on the approved destination allowlist" in decision.violated_rule_description
