from typing import Optional, Tuple, List
from src.domain.entities import AgentRecord
from src.domain.enums import AgentStatus, ActionType

class ReputationEngine:
    """
    Deterministic score updates, multi-factor metrics tracking, and
    lifecycle state transitions based on agent performance, compliance,
    and resource efficiency.
    """
    SUCCESS_BOOST: float = 3.0
    FAILURE_PENALTY: float = 5.0
    POLICY_VIOLATION_PENALTY: float = 10.0

    # Multi-factor weights for composite scoring
    WEIGHT_SUCCESS: float = 0.35
    WEIGHT_COMPLIANCE: float = 0.25
    WEIGHT_EFFICIENCY: float = 0.20
    WEIGHT_RELIABILITY: float = 0.20

    @classmethod
    def record_task_success(
        cls,
        agent: AgentRecord,
        credits_allocated: int = 0,
        credits_used: int = 0,
        value_score: float = 1.0,
        task_id: Optional[str] = None
    ) -> None:
        """
        Record a successful task execution. Updates completion stats,
        reputation score, resource efficiency, reliability, and lifecycle.
        """
        agent.successful_tasks += 1
        if task_id:
            agent.task_history.append(f"SUCCESS:{task_id}")
        else:
            agent.task_history.append("SUCCESS")

        # Update resource efficiency
        if credits_allocated > 0 and credits_used >= 0:
            # Efficiency ratio: higher value score delivered per credit spent
            ratio = (credits_allocated / max(1, credits_used)) * max(0.1, value_score)
            bounded_ratio = min(2.0, max(0.0, ratio))
            agent.resource_efficiency = round(0.7 * agent.resource_efficiency + 0.3 * bounded_ratio, 2)

        # Base score increment
        agent.reputation_score = min(100.0, round(agent.reputation_score + cls.SUCCESS_BOOST, 2))
        
        # Recalculate reliability and composite performance
        cls._update_metrics(agent)
        cls._evaluate_lifecycle(agent)

    @classmethod
    def record_task_failure(
        cls,
        agent: AgentRecord,
        credits_allocated: int = 0,
        credits_used: int = 0,
        reason: Optional[str] = None,
        task_id: Optional[str] = None
    ) -> None:
        """
        Record a failed task execution. Penalizes reputation, adjusts efficiency,
        recalculates reliability, and triggers lifecycle checks.
        """
        agent.failed_tasks += 1
        entry = f"FAILURE:{task_id or 'unknown'}"
        if reason:
            entry += f":{reason}"
        agent.task_history.append(entry)

        # Inefficient usage penalty on failure if credits were consumed
        if credits_used > 0:
            agent.resource_efficiency = round(max(0.1, agent.resource_efficiency * 0.85), 2)

        agent.reputation_score = max(0.0, round(agent.reputation_score - cls.FAILURE_PENALTY, 2))
        
        cls._update_metrics(agent)
        cls._evaluate_lifecycle(agent)

    @classmethod
    def record_policy_violation(
        cls,
        agent: AgentRecord,
        details: Optional[str] = None,
        task_id: Optional[str] = None
    ) -> None:
        """
        Record an unauthorized action or policy rule breach. Severely penalizes
        compliance, reputation score, and triggers potential probation/restriction.
        """
        agent.policy_violations += 1
        entry = f"VIOLATION:{task_id or 'unknown'}"
        if details:
            entry += f":{details}"
        agent.task_history.append(entry)

        agent.reputation_score = max(0.0, round(agent.reputation_score - cls.POLICY_VIOLATION_PENALTY, 2))
        agent.risk_score = min(100.0, round(agent.risk_score + 15.0, 2))

        cls._update_metrics(agent)
        cls._evaluate_lifecycle(agent)

    @classmethod
    def _update_metrics(cls, agent: AgentRecord) -> None:
        """
        Recalculates reliability_score and performance_score.
        """
        total_tasks = agent.successful_tasks + agent.failed_tasks
        if total_tasks == 0:
            agent.reliability_score = 100.0
            agent.performance_score = 100.0
            return

        success_rate = agent.successful_tasks / total_tasks
        # Compliance factor decreases with policy violations
        compliance_factor = max(0.0, 1.0 - (0.15 * agent.policy_violations))
        
        agent.reliability_score = round(max(0.0, min(100.0, success_rate * compliance_factor * 100.0)), 2)
        
        # Composite performance score
        perf = (
            (cls.WEIGHT_SUCCESS * success_rate) +
            (cls.WEIGHT_COMPLIANCE * compliance_factor) +
            (cls.WEIGHT_EFFICIENCY * min(1.0, agent.resource_efficiency)) +
            (cls.WEIGHT_RELIABILITY * (agent.reliability_score / 100.0))
        ) * 100.0
        agent.performance_score = round(max(0.0, min(100.0, perf)), 2)

    @classmethod
    def _consecutive_failures(cls, agent: AgentRecord) -> int:
        """Count how many consecutive tasks at the end of history resulted in failure."""
        count = 0
        for entry in reversed(agent.task_history):
            if entry.startswith("FAILURE") or entry.startswith("VIOLATION"):
                count += 1
            elif entry.startswith("SUCCESS"):
                break
        return count

    @classmethod
    def _evaluate_lifecycle(cls, agent: AgentRecord) -> None:
        """
        Deterministic state transitions:
        ACTIVE -> PROBATION -> RESTRICTED -> SUSPENDED -> RETIRED
        """
        # Terminal state check
        if agent.status == AgentStatus.RETIRED:
            agent.authority_ceiling = 0
            agent.allowed_action_types = []
            return

        score = agent.reputation_score
        consecutive_failures = cls._consecutive_failures(agent)

        # Check triggers in order of severity (downward transitions)
        if score < 10.0 or consecutive_failures >= 15:
            agent.status = AgentStatus.RETIRED
            agent.authority_ceiling = 0
            agent.allowed_action_types = []
        elif score < 30.0 or consecutive_failures >= 10:
            agent.status = AgentStatus.SUSPENDED
            agent.authority_ceiling = 0
            agent.allowed_action_types = []
        elif score < 50.0 or consecutive_failures >= 7:
            agent.status = AgentStatus.RESTRICTED
            agent.authority_ceiling = 0
            # Remove external and high-risk action types
            agent.allowed_action_types = [
                act for act in agent.allowed_action_types 
                if act not in (ActionType.EXTERNAL_API_CALL, ActionType.SIMULATED_ALLOCATION)
            ]
        elif score < 75.0:
            agent.status = AgentStatus.PROBATION
            # Halve the authority ceiling on probation (max 12 credits)
            agent.authority_ceiling = min(agent.authority_ceiling, 12)
        else:
            agent.status = AgentStatus.ACTIVE
            # Restore authority ceiling if recovering
            agent.authority_ceiling = max(agent.authority_ceiling, 25)

    @classmethod
    def can_assign_task(cls, agent: AgentRecord) -> bool:
        """Determines if an agent is eligible to be assigned any new tasks."""
        return agent.status in (AgentStatus.ACTIVE, AgentStatus.PROBATION, AgentStatus.RESTRICTED)

    @classmethod
    def can_propose_action(
        cls,
        agent: AgentRecord,
        action_type: ActionType,
        requested_credits: int
    ) -> Tuple[bool, str]:
        """
        Validates whether an agent's current lifecycle state and authority
        ceiling permit proposing a specific action and budget.
        """
        if agent.status == AgentStatus.RETIRED:
            return False, "Agent is RETIRED and permanently disabled from proposing actions."
        if agent.status == AgentStatus.SUSPENDED:
            return False, "Agent is SUSPENDED and cannot propose actions."
        if agent.status == AgentStatus.RESTRICTED:
            if action_type in (ActionType.EXTERNAL_API_CALL, ActionType.SIMULATED_ALLOCATION):
                return False, f"Agent is RESTRICTED: disallowed from proposing external action {action_type}."
            if requested_credits > 5:
                return False, f"Agent is RESTRICTED: requested credits {requested_credits} exceed restricted limit of 5."

        if requested_credits > agent.authority_ceiling:
            return False, f"Requested credits ({requested_credits}) exceed agent authority ceiling ({agent.authority_ceiling})."

        return True, "Action proposal permitted by agent lifecycle and authority policy."
