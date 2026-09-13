"""Least-privilege checks for agent proposals.

The LLM is never the authorization boundary. This module is intentionally
small and deterministic so it can be tested independently of model output.
"""

from src.domain.entities import ActionProposal, Organisation
from src.domain.enums import AgentStatus
from src.domain.exceptions import UnauthorizedActionError


def enforce_agent_capability(proposal: ActionProposal, org: Organisation) -> None:
    agent = org.agents.get(proposal.proposing_agent_id)
    if agent is None:
        raise UnauthorizedActionError(
            f"Unknown proposing agent '{proposal.proposing_agent_id}'"
        )
    if agent.status != AgentStatus.ACTIVE:
        raise UnauthorizedActionError(
            f"Agent '{agent.id}' is not active (status={agent.status.value})"
        )
    if proposal.action_type not in agent.allowed_action_types:
        raise UnauthorizedActionError(
            f"Agent '{agent.id}' is not authorized for action type "
            f"'{proposal.action_type.value}'"
        )
    if proposal.requested_credits > agent.authority_ceiling:
        raise UnauthorizedActionError(
            f"Proposal requests {proposal.requested_credits} credits, above "
            f"agent '{agent.id}' authority ceiling of {agent.authority_ceiling}"
        )
