import pytest
from src.domain.enums import OrgState, AgentRole, AgentStatus, ActionType, PolicyResult
from src.domain.entities import Organisation, AgentRecord, ActionProposal, PolicyDecision
from src.domain.value_objects import Credits, Reputation

def test_organisation_creation():
    org = Organisation(id="org-01", mission="Test Mission", treasury_balance=100)
    assert org.id == "org-01"
    assert org.treasury_balance == 100
    assert org.state == OrgState.INITIALIZING
    assert len(org.agents) == 0

def test_agent_record_defaults():
    agent = AgentRecord(
        id="agent-ceo",
        role=AgentRole.CEO,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.REPLAN]
    )
    assert agent.status == AgentStatus.ACTIVE
    assert agent.reputation_score == 100.0
    assert agent.authority_ceiling == 25

def test_credits_value_object():
    c1 = Credits(amount=50)
    c2 = Credits(amount=25)
    assert (c1 + c2).amount == 75
    assert (c1 - c2).amount == 25
    with pytest.raises(ValueError):
        _ = c2 - c1

def test_reputation_clamping():
    rep = Reputation(score=150.0)
    assert rep.score == 100.0
    rep_low = Reputation(score=-10.0)
    assert rep_low.score == 0.0
