"""Domain entities for Governed Dynamic Agent Capability Evolution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, Optional


class CapabilityTier(str, Enum):
    BASIC = "BASIC"
    ADVANCED = "ADVANCED"
    B2B_COMMERCE = "B2B_COMMERCE"


class CapabilityPermission(str, Enum):
    READ_ONLY = "READ_ONLY"
    EXECUTE_STANDARD = "EXECUTE_STANDARD"
    EXECUTE_ADVANCED = "EXECUTE_ADVANCED"
    EXTERNAL_COMMERCE = "EXTERNAL_COMMERCE"


class CapabilityGrantStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


@dataclass
class CapabilityGrant:
    tenant_id: str
    organisation_id: str
    grant_id: str
    agent_id: str
    capability_name: str
    permission_level: str
    trigger_performance_score: float
    granted_by_policy_id: str
    status: CapabilityGrantStatus = CapabilityGrantStatus.ACTIVE
    granted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str = field(default_factory=lambda: (datetime.now(timezone.utc) + timedelta(days=7)).isoformat())

    def is_valid(self, as_of: Optional[datetime] = None) -> bool:
        if self.status != CapabilityGrantStatus.ACTIVE:
            return False
        ref = as_of or datetime.now(timezone.utc)
        exp = datetime.fromisoformat(self.expires_at)
        return ref <= exp

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "grant_id": self.grant_id,
            "agent_id": self.agent_id,
            "capability_name": self.capability_name,
            "permission_level": self.permission_level,
            "trigger_performance_score": self.trigger_performance_score,
            "granted_by_policy_id": self.granted_by_policy_id,
            "status": self.status.value if isinstance(self.status, CapabilityGrantStatus) else self.status,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
        }


@dataclass
class CapabilityProposal:
    tenant_id: str
    organisation_id: str
    proposal_id: str
    target_agent_id: str
    proposer_agent_id: str
    requested_capability: str
    requested_permission: str
    current_performance_score: float
    rationale: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "proposal_id": self.proposal_id,
            "target_agent_id": self.target_agent_id,
            "proposer_agent_id": self.proposer_agent_id,
            "requested_capability": self.requested_capability,
            "requested_permission": self.requested_permission,
            "current_performance_score": self.current_performance_score,
            "rationale": self.rationale,
            "created_at": self.created_at,
        }
