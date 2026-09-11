from src.domain.entities import AgentRecord
from src.domain.enums import AgentStatus

class ReputationEngine:
    """
    Deterministic score updates and lifecycle transitions based on performance.
    """
    SUCCESS_BOOST = 3.0
    FAILURE_PENALTY = 5.0
    POLICY_VIOLATION_PENALTY = 10.0

    @classmethod
    def record_task_success(cls, agent: AgentRecord) -> None:
        agent.successful_tasks += 1
        agent.reputation_score = min(100.0, agent.reputation_score + cls.SUCCESS_BOOST)
        cls._evaluate_lifecycle(agent)

    @classmethod
    def record_task_failure(cls, agent: AgentRecord) -> None:
        agent.failed_tasks += 1
        agent.reputation_score = max(0.0, agent.reputation_score - cls.FAILURE_PENALTY)
        cls._evaluate_lifecycle(agent)

    @classmethod
    def record_policy_violation(cls, agent: AgentRecord) -> None:
        agent.policy_violations += 1
        agent.reputation_score = max(0.0, agent.reputation_score - cls.POLICY_VIOLATION_PENALTY)
        cls._evaluate_lifecycle(agent)

    @classmethod
    def _evaluate_lifecycle(cls, agent: AgentRecord) -> None:
        score = agent.reputation_score
        if score >= 75.0:
            agent.status = AgentStatus.ACTIVE
            # Reset authority ceiling to full default if recovered
            agent.authority_ceiling = max(agent.authority_ceiling, 25)
        elif 50.0 <= score < 75.0:
            agent.status = AgentStatus.PROBATION
            # Half the authority ceiling on probation
            agent.authority_ceiling = min(agent.authority_ceiling, 12)
        elif 30.0 <= score < 50.0:
            agent.status = AgentStatus.RESTRICTED
            # Restricted agents have 0 authority for external actions
            agent.authority_ceiling = 0
        else:
            agent.status = AgentStatus.SUSPENDED
            agent.authority_ceiling = 0
