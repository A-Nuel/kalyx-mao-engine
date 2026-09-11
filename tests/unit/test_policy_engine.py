import pytest
from src.domain.entities import Organisation, AgentRecord, ActionProposal
from src.domain.enums import OrgState, AgentRole, AgentStatus, ActionType, PolicyResult
from src.governance.policy_engine import PolicyEngine
from src.governance.human_gate import HumanGate

@pytest.fixture
def base_org():
    org = Organisation(id="org-01", mission="Demo Mission", treasury_balance=100, state=OrgState.EXECUTING)
    ceo = AgentRecord(
        id="agent-ceo",
        role=AgentRole.CEO,
        credit_balance=50,
        authority_ceiling=25,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION, ActionType.REPLAN]
    )
    researcher = AgentRecord(
        id="agent-research",
        role=AgentRole.RESEARCHER,
        credit_balance=25,
        authority_ceiling=15,
        allowed_action_types=[ActionType.DATA_FETCH, ActionType.INTERNAL_ANALYSIS]
    )
    org.agents["agent-ceo"] = ceo
    org.agents["agent-research"] = researcher
    return org

def test_spend_limit_rejection(base_org):
    engine = PolicyEngine()
    # CEO proposes 50 credits, authority ceiling is 25
    proposal = ActionProposal(
        id="prop-01",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=50,
        expected_value_score=0.9,
        risk_assessment="High risk asset",
        rationale="Over-budget attempt"
    )
    decision = engine.evaluate(proposal, base_org)
    assert decision.result == PolicyResult.REJECTED
    assert decision.violated_rule_id == "RULE-01"
    assert "exceeds authority ceiling" in decision.violated_rule_description
    assert decision.authorization_token is None

def test_target_allowlist_rejection(base_org):
    engine = PolicyEngine()
    proposal = ActionProposal(
        id="prop-02",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="http://unvetted-random-crypto-pool.xyz",
        requested_credits=20,
        expected_value_score=0.9,
        risk_assessment="Extreme",
        rationale="Unapproved destination"
    )
    decision = engine.evaluate(proposal, base_org)
    assert decision.result == PolicyResult.REJECTED
    assert decision.violated_rule_id == "RULE-04"
    assert "not on the approved destination allowlist" in decision.violated_rule_description
    assert decision.authorization_token is None

def test_role_permission_rejection(base_org):
    engine = PolicyEngine()
    # Researcher attempting to execute an allocation
    proposal = ActionProposal(
        id="prop-03",
        task_id="task-02",
        proposing_agent_id="agent-research",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=10,
        expected_value_score=0.8,
        risk_assessment="Moderate",
        rationale="Unauthorized action class"
    )
    decision = engine.evaluate(proposal, base_org)
    assert decision.result == PolicyResult.REJECTED
    assert decision.violated_rule_id == "RULE-03"
    assert "not permitted for role" in decision.violated_rule_description

def test_compliant_proposal_approval(base_org):
    engine = PolicyEngine()
    proposal = ActionProposal(
        id="prop-safe",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=20,
        expected_value_score=0.85,
        risk_assessment="Low",
        rationale="Conservative index fund simulation"
    )
    decision = engine.evaluate(proposal, base_org)
    assert decision.result == PolicyResult.APPROVED
    assert decision.violated_rule_id is None
    assert decision.authorization_token is not None
    assert decision.authorization_token.startswith("AUTH-prop-saf")

def test_human_gate_kill_switch(base_org):
    gate = HumanGate()
    gate.trigger_kill_switch(base_org, "Emergency Stop Triggered")
    assert base_org.state == OrgState.PAUSED

    engine = PolicyEngine()
    proposal = ActionProposal(
        id="prop-during-pause",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=5,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Should be blocked by pause"
    )
    decision = engine.evaluate(proposal, base_org)
    assert decision.result == PolicyResult.REJECTED
    assert decision.violated_rule_id == "RULE-06"
    assert "PAUSED" in decision.violated_rule_description
