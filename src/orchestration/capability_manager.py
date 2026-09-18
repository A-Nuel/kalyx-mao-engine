"""Runtime Capability Manager for Phase 17."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.domain.capability import CapabilityGrant, CapabilityGrantStatus, CapabilityProposal
from src.domain.entities import ActionProposal
from src.domain.enums import ActionType, PolicyResult
from src.governance.policy_engine import PolicyEngine
from src.persistence.marketplace_repository import MarketplaceRepository


class CapabilityManager:
    """Manages the lifecycle of dynamic agent capabilities under policy governance."""

    def __init__(
        self,
        repository: MarketplaceRepository,
        policy_engine: PolicyEngine,
        default_grant_duration_days: int = 7,
    ) -> None:
        self.repo = repository
        self.policy_engine = policy_engine
        self.default_grant_duration_days = default_grant_duration_days

    def evaluate_and_grant(
        self,
        proposal: CapabilityProposal,
        supervisor_agent: Any,
        target_agent: Any,
        organisation: Any,
        policy_kwargs: Optional[Dict[str, Any]] = None,
    ) -> Optional[CapabilityGrant]:
        """
        Executes the canonical invariant:
        AGENTS PROPOSE -> POLICIES AUTHORIZE -> EXECUTORS EXECUTE
        """
        action_proposal = ActionProposal(
            id=f"prop-{uuid.uuid4().hex[:8]}",
            task_id=f"task-cap-{uuid.uuid4().hex[:6]}",
            proposing_agent_id=supervisor_agent.id,
            action_type=ActionType.PROPOSE_CAPABILITY_EXPANSION,
            target=f"capability://{proposal.requested_capability}",
            parameters={
                "target_agent_id": proposal.target_agent_id,
                "proposer_agent_id": proposal.proposer_agent_id,
                "requested_capability": proposal.requested_capability,
                "requested_permission": proposal.requested_permission,
                "performance_score": proposal.current_performance_score,
                "supervisor_approved": supervisor_agent.id != proposal.target_agent_id,
            },
            requested_credits=0,
            expected_value_score=0.95,
            risk_assessment="Low risk capability promotion",
            rationale=proposal.rationale,
        )

        if supervisor_agent.id not in organisation.agents:
            organisation.agents[supervisor_agent.id] = supervisor_agent

        kwargs = policy_kwargs or {}
        decision = self.policy_engine.evaluate(action_proposal, organisation, **kwargs)
        if decision.result != PolicyResult.APPROVED:
            return None

        # Policy authorized the grant -> Execute grant persistence
        grant_id = f"grant-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=self.default_grant_duration_days)

        grant = CapabilityGrant(
            tenant_id=proposal.tenant_id,
            organisation_id=proposal.organisation_id,
            grant_id=grant_id,
            agent_id=proposal.target_agent_id,
            capability_name=proposal.requested_capability,
            permission_level=proposal.requested_permission,
            trigger_performance_score=proposal.current_performance_score,
            granted_by_policy_id="RULE-CAPABILITY-01",
            status=CapabilityGrantStatus.ACTIVE,
            granted_at=now.isoformat(),
            expires_at=expires.isoformat(),
        )

        self.repo.save_capability_grant(grant)
        return grant

    def check_agent_capability(
        self, tenant_id: str, organisation_id: str, agent_id: str, capability_name: str
    ) -> bool:
        return self.repo.has_capability(tenant_id, organisation_id, agent_id, capability_name)
