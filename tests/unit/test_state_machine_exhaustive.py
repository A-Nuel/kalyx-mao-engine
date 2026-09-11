import pytest
from itertools import product
from src.domain.entities import Organisation, Task
from src.domain.enums import OrgState, TaskStatus
from src.domain.exceptions import InvalidStateTransitionError
from src.orchestration.state_machine import StateMachine, ORG_TRANSITIONS, TASK_TRANSITIONS

def test_all_organisation_transitions():
    all_states = list(OrgState)
    for from_state, to_state in product(all_states, all_states):
        org = Organisation(id="test-org", mission="Testing", state=from_state)
        allowed = to_state in ORG_TRANSITIONS.get(from_state, set())
        
        if allowed:
            StateMachine.transition_org(org, to_state)
            assert org.state == to_state
        else:
            with pytest.raises(InvalidStateTransitionError):
                StateMachine.transition_org(org, to_state)

def test_all_task_transitions():
    all_statuses = list(TaskStatus)
    for from_status, to_status in product(all_statuses, all_statuses):
        task = Task(
            id="test-task",
            mission_id="m-01",
            assigned_agent_id="agent-01",
            objective="Testing transitions",
            allocated_credits=10,
            status=from_status
        )
        allowed = to_status in TASK_TRANSITIONS.get(from_status, set())
        
        if allowed:
            StateMachine.transition_task(task, to_status)
            assert task.status == to_status
        else:
            with pytest.raises(InvalidStateTransitionError):
                StateMachine.transition_task(task, to_status)

def test_replanning_cycle_governance():
    task = Task(
        id="task-replan",
        mission_id="m-01",
        assigned_agent_id="agent-ceo",
        objective="Replan test",
        allocated_credits=20
    )
    # 1. PENDING -> ASSIGNED
    StateMachine.transition_task(task, TaskStatus.ASSIGNED)
    # 2. ASSIGNED -> IN_PROGRESS
    StateMachine.transition_task(task, TaskStatus.IN_PROGRESS)
    # 3. IN_PROGRESS -> SUBMITTED
    StateMachine.transition_task(task, TaskStatus.SUBMITTED)
    # 4. SUBMITTED -> REJECTED (Policy rejection)
    StateMachine.transition_task(task, TaskStatus.REJECTED)
    assert task.status == TaskStatus.REJECTED

    # Invariant check: Cannot bypass from REJECTED straight to APPROVED
    with pytest.raises(InvalidStateTransitionError):
        StateMachine.transition_task(task, TaskStatus.APPROVED)

    # Invariant check: Cannot bypass from REJECTED straight to COMPLETED
    with pytest.raises(InvalidStateTransitionError):
        StateMachine.transition_task(task, TaskStatus.COMPLETED)

    # Must go REJECTED -> IN_PROGRESS (replanning)
    StateMachine.transition_task(task, TaskStatus.IN_PROGRESS)
    assert task.status == TaskStatus.IN_PROGRESS
