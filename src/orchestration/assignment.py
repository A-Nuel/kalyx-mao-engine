from typing import Optional, List, Tuple, Dict
from src.domain.entities import Task, Organisation, AgentRecord
from src.domain.enums import AgentRole, AgentStatus, TaskStatus
from src.domain.exceptions import NoEligibleAgentException
from src.economy.reputation import ReputationEngine

class AgentAssignmentEngine:
    """
    Capability- and reputation-based task routing and dynamic resource allocation.
    Matches tasks to the best eligible agent based on multi-factor fitness
    and scales credit allocations deterministically based on agent reputation tier.
    """

    FITNESS_W_REPUTATION = 0.35
    FITNESS_W_RELIABILITY = 0.25
    FITNESS_W_EFFICIENCY = 0.20
    FITNESS_W_PERFORMANCE = 0.20

    @classmethod
    def infer_required_role(cls, task_objective: str) -> Optional[AgentRole]:
        """Infer suitable agent role from task objective keywords if not explicitly specified."""
        obj_lower = task_objective.lower()
        if any(w in obj_lower for w in ["research", "data", "fetch", "gather", "survey"]):
            return AgentRole.RESEARCHER
        if any(w in obj_lower for w in ["strategy", "synthesize", "plan", "roadmap", "prioritize"]):
            return AgentRole.STRATEGIST
        if any(w in obj_lower for w in ["finance", "financial", "treasury", "liquidity", "yield", "budget", "cost"]):
            return AgentRole.FINANCIAL_ANALYST
        return None

    @classmethod
    def select_agent_for_task(
        cls,
        task: Task,
        org: Organisation,
        required_role: Optional[AgentRole] = None
    ) -> AgentRecord:
        """
        Select the best candidate agent for a task using role matching,
        lifecycle checks, and composite performance/reputation fitness.
        """
        target_role = required_role or cls.infer_required_role(task.objective)

        eligible_candidates: List[Tuple[float, AgentRecord]] = []

        for agent in org.agents.values():
            # Exclude suspended or retired agents
            if not ReputationEngine.can_assign_task(agent):
                continue

            # Role filter
            if target_role and agent.role != target_role:
                continue

            # Exclude CEO unless task explicitly requests CEO
            if agent.role == AgentRole.CEO and target_role != AgentRole.CEO:
                continue

            fitness = cls.calculate_fitness(agent)
            eligible_candidates.append((fitness, agent))

        if not eligible_candidates:
            role_desc = f" for role {target_role.value}" if target_role else ""
            raise NoEligibleAgentException(
                f"No eligible agents found{role_desc} to handle task '{task.id}' (objective: '{task.objective}')."
            )

        # Sort descending by fitness score, then by reputation score
        eligible_candidates.sort(key=lambda item: (item[0], item[1].reputation_score), reverse=True)
        return eligible_candidates[0][1]

    @classmethod
    def calculate_fitness(cls, agent: AgentRecord) -> float:
        """Calculate multi-factor fitness score in [0.0, 1.0]."""
        rep = agent.reputation_score / 100.0
        rel = agent.reliability_score / 100.0
        eff = min(1.0, max(0.0, agent.resource_efficiency))
        perf = agent.performance_score / 100.0

        fitness = (
            (cls.FITNESS_W_REPUTATION * rep) +
            (cls.FITNESS_W_RELIABILITY * rel) +
            (cls.FITNESS_W_EFFICIENCY * eff) +
            (cls.FITNESS_W_PERFORMANCE * perf)
        )

        # Status modifiers
        if agent.status == AgentStatus.PROBATION:
            fitness *= 0.8
        elif agent.status == AgentStatus.RESTRICTED:
            fitness *= 0.5

        return round(fitness, 4)

    @classmethod
    def determine_credit_allocation(
        cls,
        task: Task,
        agent: AgentRecord,
        org: Organisation,
        requested_credits: Optional[int] = None
    ) -> int:
        """
        Calculates dynamic credit allocation according to agent reputation tier,
        authority ceiling, and available organization treasury.
        """
        base_requested = requested_credits if requested_credits is not None else task.allocated_credits
        if base_requested <= 0:
            base_requested = min(agent.authority_ceiling, 20)

        # Cap by organisation treasury
        available_treasury = max(0, org.treasury_balance)
        if available_treasury == 0:
            return 0

        # Tier-based ceilings
        if agent.status == AgentStatus.ACTIVE:
            if agent.reputation_score >= 85.0:
                # Top tier: full requested within ceiling
                allocation = min(base_requested, agent.authority_ceiling, available_treasury)
            else:
                # Good standing: 80% ceiling limit
                capped_ceiling = max(5, int(agent.authority_ceiling * 0.8))
                allocation = min(base_requested, capped_ceiling, available_treasury)
        elif agent.status == AgentStatus.PROBATION:
            # Probation tier: maximum 10 credits or authority ceiling
            probation_cap = min(10, agent.authority_ceiling)
            allocation = min(base_requested, probation_cap, available_treasury)
        elif agent.status == AgentStatus.RESTRICTED:
            # Restricted tier: maximum 5 credits for internal analysis only
            restricted_cap = min(5, available_treasury)
            allocation = min(base_requested, restricted_cap)
        else:
            allocation = 0

        return max(0, allocation)

    @classmethod
    def assign_and_allocate(
        cls,
        task: Task,
        org: Organisation,
        required_role: Optional[AgentRole] = None,
        requested_credits: Optional[int] = None
    ) -> Tuple[AgentRecord, int]:
        """
        Routes the task to the optimal agent, calculates resource allocation,
        and transitions the task status to ASSIGNED.
        """
        agent = cls.select_agent_for_task(task, org, required_role=required_role)
        allocation = cls.determine_credit_allocation(task, agent, org, requested_credits=requested_credits)

        task.assigned_agent_id = agent.id
        task.allocated_credits = allocation
        task.status = TaskStatus.ASSIGNED

        return agent, allocation
