import pytest
from src.domain.entities import AgentRecord
from src.domain.enums import AgentRole, AgentStatus, ActionType
from src.economy.reputation import ReputationEngine


def make_agent(reputation_score=100.0, status=AgentStatus.ACTIVE, authority_ceiling=25):
    return AgentRecord(
        id="agent-lifecycle-test",
        role=AgentRole.RESEARCHER,
        authority_ceiling=authority_ceiling,
        status=status,
        reputation_score=reputation_score,
    )


def test_active_agent_no_failures_stays_active():
    agent = make_agent(reputation_score=100.0)
    for _ in range(3):
        ReputationEngine.record_task_success(agent, credits_allocated=10, credits_used=8, value_score=1.0, task_id="t-ok")
    assert agent.status == AgentStatus.ACTIVE
    assert ReputationEngine.can_assign_task(agent) is True


def test_consecutive_failures_cause_probation():
    agent = make_agent(reputation_score=80.0)
    # 3 consecutive failures will push reputation below 70 (80 - 3*5 = 65)
    for i in range(3):
        ReputationEngine.record_task_failure(agent, credits_allocated=10, credits_used=5, reason="Timeout", task_id=f"t-fail-{i}")
    assert agent.reputation_score == 65.0
    assert agent.status in (AgentStatus.PROBATION, AgentStatus.RESTRICTED)
    assert ReputationEngine.can_assign_task(agent) is True


def test_deep_failure_causes_restricted_then_suspended():
    agent = make_agent(reputation_score=60.0)
    # 6 consecutive failures: 60 - 6*5 = 30 -> SUSPENDED territory (< 30)
    for i in range(6):
        ReputationEngine.record_task_failure(agent, credits_allocated=5, credits_used=5, reason="Error", task_id=f"t-fail-{i}")
    assert agent.status in (AgentStatus.SUSPENDED, AgentStatus.RESTRICTED)
    assert agent.authority_ceiling <= 5


def test_policy_violation_severe_penalty():
    agent = make_agent(reputation_score=90.0)
    ReputationEngine.record_policy_violation(agent, details="Unauthorized external call", task_id="t-v1")
    assert agent.reputation_score == 80.0
    assert agent.risk_score >= 15.0


def test_recovery_from_probation_with_5_successes():
    agent = make_agent(reputation_score=65.0, status=AgentStatus.PROBATION, authority_ceiling=12)
    # Recover with 5 consecutive successes
    for i in range(5):
        ReputationEngine.record_task_success(agent, credits_allocated=10, credits_used=8, value_score=1.0, task_id=f"t-rec-{i}")
    # After 5 successes: 65 + 5*3 = 80.0 -> above threshold
    assert agent.reputation_score == 80.0
    assert agent.status == AgentStatus.ACTIVE
    assert agent.authority_ceiling >= 25


def test_retired_agent_permanently_disabled():
    agent = make_agent(reputation_score=5.0)
    # Force into retirement (score < 15)
    ReputationEngine.record_task_failure(agent, credits_allocated=10, credits_used=10, reason="Critical failure", task_id="t-final")
    assert agent.status == AgentStatus.RETIRED
    assert agent.authority_ceiling == 0
    assert agent.allowed_action_types == []
    assert ReputationEngine.can_assign_task(agent) is False


def test_can_propose_action_authority_ceiling_enforced():
    agent = make_agent(reputation_score=100.0, authority_ceiling=25)
    allowed, reason = ReputationEngine.can_propose_action(agent, ActionType.INTERNAL_ANALYSIS, 25)
    assert allowed is True

    not_allowed, reason = ReputationEngine.can_propose_action(agent, ActionType.INTERNAL_ANALYSIS, 26)
    assert not_allowed is False
    assert "authority ceiling" in reason.lower()


def test_suspended_agent_cannot_propose():
    agent = make_agent(reputation_score=100.0, status=AgentStatus.SUSPENDED)
    allowed, reason = ReputationEngine.can_propose_action(agent, ActionType.INTERNAL_ANALYSIS, 5)
    assert allowed is False
    assert "suspended" in reason.lower()


def test_restricted_agent_cannot_propose_external_actions():
    agent = make_agent(reputation_score=100.0, status=AgentStatus.RESTRICTED)
    allowed, reason = ReputationEngine.can_propose_action(agent, ActionType.EXTERNAL_API_CALL, 3)
    assert allowed is False
    assert "restricted" in reason.lower()

    # Internal analysis is still permitted at low credits
    allowed_internal, _ = ReputationEngine.can_propose_action(agent, ActionType.INTERNAL_ANALYSIS, 4)
    assert allowed_internal is True


def test_restricted_agent_low_credit_cap():
    agent = make_agent(reputation_score=100.0, status=AgentStatus.RESTRICTED)
    not_allowed, reason = ReputationEngine.can_propose_action(agent, ActionType.INTERNAL_ANALYSIS, 6)
    assert not_allowed is False
    assert "restricted" in reason.lower()
