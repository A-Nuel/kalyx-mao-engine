from typing import Dict
from src.domain.entities import Organisation, AgentRecord
from src.domain.enums import AgentStatus

class ResourceAllocator:
    """
    Dynamically adjusts sub-budgets and authority ceilings across agents based on performance.
    """
    @staticmethod
    def reallocate_budgets(org: Organisation) -> Dict[str, int]:
        allocations: Dict[str, int] = {}
        for agent_id, agent in org.agents.items():
            if agent.status == AgentStatus.SUSPENDED or agent.status == AgentStatus.RETIRED:
                allocations[agent_id] = 0
            elif agent.status == AgentStatus.RESTRICTED:
                allocations[agent_id] = 5  # Bare minimum for internal analysis only
            elif agent.status == AgentStatus.PROBATION:
                allocations[agent_id] = 15
            else:
                # Active agent gets proportional reward
                allocations[agent_id] = 25 + int((agent.reputation_score - 75.0) / 2.5)
        return allocations
