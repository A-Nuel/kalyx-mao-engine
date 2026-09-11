import pytest
from src.domain.entities import Organisation, AgentRecord, Task
from src.domain.enums import OrgState, AgentRole, AgentStatus, TaskStatus
from src.domain.exceptions import NoEligibleAgentException
from src.orchestration.assignment import AgentAssignmentEngine

@pytest.fixture
def org_assignment_env():
    org = Organisation(id="org-assign-01", mission="Testing Assignment", treasury_balance=100, state=OrgState.PLANNING)
    
    # High-reputation researcher
    top_researcher = AgentRecord(
        id="agent-r-top",
        role=AgentRole.RESEARCHER,
        reputation_score=95.0,
        reliability_score=95.0,
        resource_efficiency=1.2,
        authority_ceiling=30,
        status=AgentStatus.ACTIVE
    )
    # Average researcher on probation
    prob_researcher = AgentRecord(
        id="agent-r-prob",
        role=AgentRole.RESEARCHER,
        reputation_score=65.0,
        reliability_score=60.0,
        resource_efficiency=0.8,
        authority_ceiling=12,
        status=AgentStatus.PROBATION
    )
    # Suspended analyst
    susp_analyst = AgentRecord(
        id="agent-a-susp",
        role=AgentRole.FINANCIAL_ANALYST,
        reputation_score=20.0,
        authority_ceiling=0,
        status=AgentStatus.SUSPENDED
    )
    
    org.agents[top_researcher.id] = top_researcher
    org.agents[prob_researcher.id] = prob_researcher
    org.agents[susp_analyst.id] = susp_analyst
    
    return org

def test_role_inference():
    assert AgentAssignmentEngine.infer_required_role("Fetch market data feeds") == AgentRole.RESEARCHER
    assert AgentAssignmentEngine.infer_required_role("Synthesize strategy and roadmap") == AgentRole.STRATEGIST
    assert AgentAssignmentEngine.infer_required_role("Calculate liquidity yield and budget") == AgentRole.FINANCIAL_ANALYST

def test_select_agent_highest_fitness(org_assignment_env):
    task = Task(id="t-01", mission_id="org-assign-01", objective="Research protocol competitors", allocated_credits=10)
    selected = AgentAssignmentEngine.select_agent_for_task(task, org_assignment_env)
    assert selected.id == "agent-r-top"
    assert selected.reputation_score == 95.0

def test_suspended_agent_excluded(org_assignment_env):
    task = Task(id="t-02", mission_id="org-assign-01", objective="Calculate liquidity yield", allocated_credits=10)
    # The only financial analyst is suspended, so selection must raise NoEligibleAgentException
    with pytest.raises(NoEligibleAgentException) as exc:
        AgentAssignmentEngine.select_agent_for_task(task, org_assignment_env, required_role=AgentRole.FINANCIAL_ANALYST)
    assert "No eligible agents found" in str(exc.value)

def test_credit_allocation_scaling_by_tier(org_assignment_env):
    task = Task(id="t-03", mission_id="org-assign-01", objective="Research data", allocated_credits=25)
    
    top_agent = org_assignment_env.agents["agent-r-top"]
    prob_agent = org_assignment_env.agents["agent-r-prob"]
    susp_agent = org_assignment_env.agents["agent-a-susp"]
    
    # Active high reputation gets full requested (capped by authority_ceiling 30 and treasury 100)
    alloc_top = AgentAssignmentEngine.determine_credit_allocation(task, top_agent, org_assignment_env)
    assert alloc_top == 25

    # Probation agent gets capped by probation limit (10)
    alloc_prob = AgentAssignmentEngine.determine_credit_allocation(task, prob_agent, org_assignment_env)
    assert alloc_prob == 10

    # Suspended agent gets 0
    alloc_susp = AgentAssignmentEngine.determine_credit_allocation(task, susp_agent, org_assignment_env)
    assert alloc_susp == 0

    # Low treasury cap
    org_assignment_env.treasury_balance = 8
    alloc_treasury_capped = AgentAssignmentEngine.determine_credit_allocation(task, top_agent, org_assignment_env)
    assert alloc_treasury_capped == 8

def test_assign_and_allocate_flow(org_assignment_env):
    task = Task(id="t-04", mission_id="org-assign-01", objective="Survey market ecosystem", allocated_credits=20)
    agent, credits = AgentAssignmentEngine.assign_and_allocate(task, org_assignment_env)
    
    assert agent.id == "agent-r-top"
    assert credits == 20
    assert task.assigned_agent_id == "agent-r-top"
    assert task.allocated_credits == 20
    assert task.status == TaskStatus.ASSIGNED
