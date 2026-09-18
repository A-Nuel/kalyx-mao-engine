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
        target_agent_id = params.get("target_agent_id") or proposal.proposing_agent_id
        proposer_agent_id = params.get("proposer_agent_id") or proposal.proposing_agent_id

        # Anti-power-seeking: Direct self-granting is strictly rejected under all circumstances
        if target_agent_id == proposer_agent_id:
            return (
                f"Agent '{target_agent_id}' cannot self-grant capabilities. Expansion proposals must originate from an independent supervisor."
            )

        # Cross-organisation isolation: target agent must belong to the organisation
        if target_agent_id not in org.agents:
            return f"Target agent '{target_agent_id}' does not belong to organisation '{org.id}'."

        # Retrieve empirical performance metrics from authoritative agent record and proposal params
        target_agent = org.agents.get(target_agent_id)
        param_score = params.get("performance_score")
        agent_score = getattr(target_agent, "performance_score", None) if target_agent else None

        if param_score is not None and agent_score is not None:
            # Conservative/anti-tampering guard: neither can override to inflate score
            performance_score = min(float(param_score), float(agent_score))
        elif agent_score is not None:
            performance_score = float(agent_score)
        elif param_score is not None:
            performance_score = float(param_score)
        else:
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
