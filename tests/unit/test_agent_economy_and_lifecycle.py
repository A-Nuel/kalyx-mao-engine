import pytest
from src.domain.entities import AgentRecord
from src.domain.enums import AgentRole, AgentStatus, ActionType
from src.economy.reputation import ReputationEngine

def test_initial_agent_state():
    agent = AgentRecord(
        id="agent-test-01",
        role=AgentRole.RESEARCHER,
        authority_ceiling=25,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.DATA_FETCH]
    )
    assert agent.status == AgentStatus.ACTIVE
    assert agent.reputation_score == 100.0
    assert agent.reliability_score == 100.0
    assert agent.resource_efficiency == 1.0
    assert ReputationEngine.can_assign_task(agent) is True

def test_task_success_boost_and_efficiency():
    agent = AgentRecord(
        id="agent-test-02",
        role=AgentRole.STRATEGIST,
        authority_ceiling=25,
        reputation_score=80.0
    )
    # Success with high efficiency (budget 20, spent 10, value 1.0)
    ReputationEngine.record_task_success(
        agent,
        credits_allocated=20,
        credits_used=10,
        value_score=1.0,
        task_id="t-succ-1"
    )
    assert agent.successful_tasks == 1
    assert agent.reputation_score == 83.0
    assert agent.resource_efficiency > 1.0
    assert agent.reliability_score == 100.0
    assert "SUCCESS:t-succ-1" in agent.task_history

def test_task_failure_and_efficiency_degradation():
    agent = AgentRecord(
        id="agent-test-03",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=25,
        reputation_score=90.0,
        resource_efficiency=1.0
    )
    ReputationEngine.record_task_failure(
        agent,
        credits_allocated=15,
        credits_used=10,
        reason="Upstream timeout",
        task_id="t-fail-1"
    )
    assert agent.failed_tasks == 1
    assert agent.reputation_score == 85.0
    assert agent.resource_efficiency < 1.0
    assert agent.reliability_score == 0.0  # 0 succ / 1 total
    assert "FAILURE:t-fail-1:Upstream timeout" in agent.task_history

def test_lifecycle_progression_active_to_probation():
    agent = AgentRecord(id="agent-lifecycle-1", role=AgentRole.RESEARCHER, authority_ceiling=25)
    
    # 3 policy violations drops score by 30 to 70.0 -> PROBATION
    for i in range(3):
        ReputationEngine.record_policy_violation(agent, details=f"Attempt {i+1}", task_id=f"t-v-{i}")
    
    assert agent.policy_violations == 3
    assert agent.reputation_score == 70.0
    assert agent.status == AgentStatus.PROBATION
    assert agent.authority_ceiling == 12
    assert agent.risk_score == 45.0
    assert ReputationEngine.can_assign_task(agent) is True

def test_lifecycle_progression_probation_to_restricted():
    agent = AgentRecord(
        id="agent-lifecycle-2",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=25,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.EXTERNAL_API_CALL]
    )
    # 3 violations drops score to 70 (probation), then 5 task failures drops score to 45 (restricted)
    for i in range(3):
        ReputationEngine.record_policy_violation(agent, task_id=f"t-pv-{i}")
    assert agent.status == AgentStatus.PROBATION

    for i in range(5):
        ReputationEngine.record_task_failure(agent, task_id=f"t-fail-{i}")
    
    assert agent.reputation_score == 45.0
    assert agent.status == AgentStatus.RESTRICTED
    assert agent.authority_ceiling == 0
    assert ActionType.EXTERNAL_API_CALL not in agent.allowed_action_types
    assert ReputationEngine.can_assign_task(agent) is True
    
    # Check proposal restrictions
    can_prop, reason = ReputationEngine.can_propose_action(agent, ActionType.EXTERNAL_API_CALL, 5)
    assert can_prop is False
    assert "RESTRICTED" in reason

def test_lifecycle_progression_to_suspended_and_retired():
    agent = AgentRecord(id="agent-lifecycle-3", role=AgentRole.RESEARCHER, authority_ceiling=25)
    
    # 5 policy violations (score drops to 50) + 5 failures (score drops to 25) -> SUSPENDED
    for i in range(5):
        ReputationEngine.record_policy_violation(agent, task_id=f"t-pv-{i}")
    for i in range(5):
        ReputationEngine.record_task_failure(agent, task_id=f"t-sus-{i}")
    
    assert agent.reputation_score == 25.0
    assert agent.status == AgentStatus.SUSPENDED
    assert agent.authority_ceiling == 0
    assert ReputationEngine.can_assign_task(agent) is False
    can_prop, reason = ReputationEngine.can_propose_action(agent, ActionType.INTERNAL_ANALYSIS, 1)
    assert can_prop is False
    assert "SUSPENDED" in reason

    # 4 more failures drops score to 5.0 -> RETIRED
    for i in range(4):
        ReputationEngine.record_task_failure(agent, task_id=f"t-ret-{i}")
    
    assert agent.reputation_score == 5.0
    assert agent.status == AgentStatus.RETIRED
    assert ReputationEngine.can_assign_task(agent) is False
    can_prop, reason = ReputationEngine.can_propose_action(agent, ActionType.INTERNAL_ANALYSIS, 1)
    assert can_prop is False
    assert "RETIRED" in reason

def test_reputation_recovery():
    agent = AgentRecord(id="agent-recover", role=AgentRole.STRATEGIST, authority_ceiling=25)
    # Drop to probation
    for _ in range(3):
        ReputationEngine.record_policy_violation(agent)
    assert agent.status == AgentStatus.PROBATION
    assert agent.reputation_score == 70.0
    assert agent.authority_ceiling == 12

    # Succeed twice: +6.0 points -> 76.0 -> Recovers to ACTIVE
    ReputationEngine.record_task_success(agent, credits_allocated=10, credits_used=5)
    ReputationEngine.record_task_success(agent, credits_allocated=10, credits_used=5)
    assert agent.reputation_score == 76.0
    assert agent.status == AgentStatus.ACTIVE
    assert agent.authority_ceiling == 25
