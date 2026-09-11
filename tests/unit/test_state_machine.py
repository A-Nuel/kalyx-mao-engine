import pytest
from src.domain.entities import Organisation, Task
from src.domain.enums import OrgState, TaskStatus
from src.domain.exceptions import InvalidStateTransitionError
from src.orchestration.state_machine import StateMachine
from src.economy.reputation import ReputationEngine
from src.domain.enums import AgentStatus
from src.domain.entities import AgentRecord, AgentRole

def test_org_valid_state_transitions():
    org = Organisation(id="org-01", mission="Mission")
    assert org.state == OrgState.INITIALIZING
    
    StateMachine.transition_org(org, OrgState.PLANNING)
    assert org.state == OrgState.PLANNING

    StateMachine.transition_org(org, OrgState.EXECUTING)
    assert org.state == OrgState.EXECUTING

    StateMachine.transition_org(org, OrgState.PAUSED)
    assert org.state == OrgState.PAUSED

    StateMachine.transition_org(org, OrgState.EXECUTING)
    assert org.state == OrgState.EXECUTING

    StateMachine.transition_org(org, OrgState.COMPLETED)
    assert org.state == OrgState.COMPLETED

def test_org_illegal_transition_raises():
    org = Organisation(id="org-01", mission="Mission")
    # Illegal transition: INITIALIZING directly to COMPLETED
    with pytest.raises(InvalidStateTransitionError):
        StateMachine.transition_org(org, OrgState.COMPLETED)

def test_task_replan_cycle():
    task = Task(id="task-01", mission_id="m-01", assigned_agent_id="ceo", objective="Do work", allocated_credits=25)
    StateMachine.transition_task(task, TaskStatus.ASSIGNED)
    StateMachine.transition_task(task, TaskStatus.IN_PROGRESS)
    StateMachine.transition_task(task, TaskStatus.SUBMITTED)
    
    # Proposal rejected by policy
    StateMachine.transition_task(task, TaskStatus.REJECTED)
    assert task.status == TaskStatus.REJECTED

    # Replanning transition back to IN_PROGRESS
    StateMachine.transition_task(task, TaskStatus.IN_PROGRESS)
    assert task.status == TaskStatus.IN_PROGRESS

def test_reputation_lifecycle_transitions():
    agent = AgentRecord(id="agent-01", role=AgentRole.RESEARCHER, authority_ceiling=25)
    assert agent.status == AgentStatus.ACTIVE
    assert agent.reputation_score == 100.0

    # 3 consecutive policy violations -> score drops 30 points to 70.0
    ReputationEngine.record_policy_violation(agent)
    ReputationEngine.record_policy_violation(agent)
    ReputationEngine.record_policy_violation(agent)
    assert agent.reputation_score == 70.0
    assert agent.status == AgentStatus.PROBATION
    assert agent.authority_ceiling == 12  # Halved on probation

    # Further drop below 50 -> RESTRICTED
    for _ in range(5):
        ReputationEngine.record_task_failure(agent)
    assert agent.reputation_score == 45.0
    assert agent.status == AgentStatus.RESTRICTED
    assert agent.authority_ceiling == 0
