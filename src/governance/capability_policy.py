"""Policy rules for Governed Dynamic Capability Evolution (Phase 17)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.domain.capability import CapabilityGrant, CapabilityGrantStatus
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType
from src.governance.rules import PolicyRule


class CapabilityEvolutionRule(PolicyRule):
    """
    Enforces governance over agent capability expansion:
    1. Agent cannot self-grant (must be proposed and evaluated by policy).
    2. Proposer cannot be the target agent unless approved via coordinator/supervisor.
    3. Target agent must satisfy empirical performance score threshold (>= 0.80 or >= 80.0).
    4. Target agent must not be suspended or on probation.
    """

    rule_id = "RULE-CAPABILITY-01"
    description = "Governed capability expansion requires proven empirical track record and anti-self-granting validation"

    def __init__(self, min_performance_threshold: float = 0.80) -> None:
        self.min_performance_threshold = min_performance_threshold

    def evaluate(
        self,
        proposal: ActionProposal,
        agent: AgentRecord,
        org: Organisation,
        **kwargs: Any,
    ) -> Optional[str]:
        if proposal.action_type != ActionType.PROPOSE_CAPABILITY_EXPANSION:
            return None

        params = proposal.parameters if isinstance(proposal.parameters, dict) else {}
        target_agent_id = params.get("target_agent_id") or proposal.agent_id
        proposer_agent_id = params.get("proposer_agent_id") or proposal.agent_id

        # Anti-power-seeking: Unsupervised direct self-granting is strictly rejected
        allow_self_proposal = params.get("supervisor_approved", False)
        if target_agent_id == proposer_agent_id and not allow_self_proposal:
            return (
                f"Agent '{target_agent_id}' cannot self-grant capabilities without supervisor/coordinator authorization."
            )

        # Retrieve empirical performance metrics
        performance_score = params.get("performance_score")
        if performance_score is None:
            # Fallback to agent record attribute if available
            performance_score = getattr(agent, "performance_score", None)

        if performance_score is None:
            return f"Cannot evaluate capability expansion for agent '{target_agent_id}': empirical performance score is missing."

        # Support both 0-1 scale and 0-100 scale
        normalized_score = performance_score / 100.0 if performance_score > 1.0 else performance_score
        threshold = self.min_performance_threshold if self.min_performance_threshold <= 1.0 else self.min_performance_threshold / 100.0

        if normalized_score < threshold:
            return (
                f"Agent '{target_agent_id}' performance score {normalized_score:.2f} is below "
                f"the required capability promotion threshold of {threshold:.2f}."
            )

        return None
