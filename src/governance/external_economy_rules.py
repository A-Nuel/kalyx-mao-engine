"""Policy rules for external economic resource operations (Phase 14A)."""
from __future__ import annotations

from typing import Any, Optional, Set

from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType
from src.governance.rules import PolicyRule


class ExternalInferenceTargetRule(PolicyRule):
    rule_id = "RULE-ORBIO-01"
    description = "EXTERNAL_INFERENCE targets must be on the Orbio gateway allowlist"

    def __init__(self, approved_targets: Optional[Set[str]] = None):
        self.approved_targets = approved_targets or {
            "orbio://inference",
            "orbio://gateway/chat",
            "https://www.orbio.so/api/v1/chat/completions",
            "https://api.orbio.so/api/v1/chat/completions",
        }

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if proposal.action_type != ActionType.EXTERNAL_INFERENCE:
            return None
        if proposal.target not in self.approved_targets:
            return f"EXTERNAL_INFERENCE target '{proposal.target}' is not on the Orbio allowlist"
        return None


class OrbioKeyLifecycleAuthorityRule(PolicyRule):
    rule_id = "RULE-ORBIO-02"
    description = "ORBIO_KEY_LIFECYCLE requires explicit credential-management authority; reputation alone is insufficient"

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if proposal.action_type != ActionType.ORBIO_KEY_LIFECYCLE:
            return None
        # Authority is role/capability based, not reputation-based.
        # Only agents that explicitly list ORBIO_KEY_LIFECYCLE may propose it.
        if ActionType.ORBIO_KEY_LIFECYCLE not in agent.allowed_action_types:
            return (
                f"Agent '{agent.id}' lacks ORBIO_KEY_LIFECYCLE authority "
                f"(reputation={agent.reputation_score} does not grant credential management)"
            )
        return None


class ExternalInferenceAuthorityRule(PolicyRule):
    rule_id = "RULE-ORBIO-03"
    description = "EXTERNAL_INFERENCE requires the action type to be explicitly allowed for the agent"

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if proposal.action_type != ActionType.EXTERNAL_INFERENCE:
            return None
        if ActionType.EXTERNAL_INFERENCE not in agent.allowed_action_types:
            return f"Agent '{agent.id}' is not permitted to propose EXTERNAL_INFERENCE"
        return None
