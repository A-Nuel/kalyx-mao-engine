from typing import Dict, Set
from src.domain.enums import OrgState, TaskStatus
from src.domain.entities import Organisation, Task
from src.domain.exceptions import InvalidStateTransitionError

ORG_TRANSITIONS: Dict[OrgState, Set[OrgState]] = {
    OrgState.INITIALIZING: {OrgState.PLANNING, OrgState.FAILED},
    OrgState.PLANNING: {OrgState.EXECUTING, OrgState.PAUSED, OrgState.FAILED},
    OrgState.EXECUTING: {OrgState.PLANNING, OrgState.PAUSED, OrgState.COMPLETED, OrgState.FAILED},
    OrgState.PAUSED: {OrgState.EXECUTING, OrgState.FAILED},
    OrgState.COMPLETED: {OrgState.PLANNING},
    OrgState.FAILED: set()
}

TASK_TRANSITIONS: Dict[TaskStatus, Set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.ASSIGNED, TaskStatus.FAILED},
    TaskStatus.ASSIGNED: {TaskStatus.IN_PROGRESS, TaskStatus.FAILED},
    TaskStatus.IN_PROGRESS: {TaskStatus.SUBMITTED, TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.SUBMITTED: {TaskStatus.APPROVED, TaskStatus.REJECTED, TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.APPROVED: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.REJECTED: {TaskStatus.IN_PROGRESS, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set()
}

class StateMachine:
    @staticmethod
    def transition_org(org: Organisation, to_state: OrgState) -> None:
        allowed = ORG_TRANSITIONS.get(org.state, set())
        if to_state not in allowed:
            raise InvalidStateTransitionError(
                f"Illegal organisation transition from {org.state.value} to {to_state.value}"
            )
        org.state = to_state

    @staticmethod
    def transition_task(task: Task, to_status: TaskStatus) -> None:
        allowed = TASK_TRANSITIONS.get(task.status, set())
        if to_status not in allowed:
            raise InvalidStateTransitionError(
                f"Illegal task transition from {task.status.value} to {to_status.value}"
            )
        task.status = to_status
