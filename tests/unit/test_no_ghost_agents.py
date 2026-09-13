from src.domain.entities import Organisation, Task, AgentRecord
from src.domain.enums import AgentRole, AgentStatus, TaskStatus
from src.domain.exceptions import NoEligibleAgentException
from src.orchestration.assignment import AgentAssignmentEngine


def test_select_agent_raises_when_only_suspended():
    org = Organisation(id="org-x", mission="m", tenant_id="t1")
    org.agents["a1"] = AgentRecord(
        id="a1", role=AgentRole.RESEARCHER, status=AgentStatus.SUSPENDED,
    )
    task = Task(id="t1", mission_id="org-x", objective="research markets", allocated_credits=5)
    try:
        AgentAssignmentEngine.select_agent_for_task(task, org, required_role=AgentRole.RESEARCHER)
        assert False, "expected NoEligibleAgentException"
    except NoEligibleAgentException:
        pass


def test_select_agent_raises_when_retired():
    org = Organisation(id="org-x", mission="m", tenant_id="t1")
    org.agents["a1"] = AgentRecord(
        id="a1", role=AgentRole.STRATEGIST, status=AgentStatus.RETIRED,
    )
    task = Task(id="t1", mission_id="org-x", objective="strategy plan", allocated_credits=5)
    try:
        AgentAssignmentEngine.select_agent_for_task(task, org, required_role=AgentRole.STRATEGIST)
        assert False, "expected NoEligibleAgentException"
    except NoEligibleAgentException:
        pass


def test_assign_never_sets_synthetic_agent_id():
    org = Organisation(id="org-x", mission="m", tenant_id="t1")
    # No agents at all
    task = Task(id="t1", mission_id="org-x", objective="research markets", allocated_credits=5)
    try:
        AgentAssignmentEngine.assign_and_allocate(task, org, required_role=AgentRole.RESEARCHER)
        assert False, "expected NoEligibleAgentException"
    except NoEligibleAgentException:
        assert task.assigned_agent_id is None or not str(task.assigned_agent_id).startswith("agent-")
