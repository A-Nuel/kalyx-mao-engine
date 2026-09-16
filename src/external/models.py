"""Provider-neutral models for external economic resources.

Kalyx Internal ORG Credits != Orbio on-chain CREDIT != Orbio inference balance.
External balances are observations only. DoubleEntryLedger is Kalyx authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
import hashlib
import json


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExternalProviderMode(str, Enum):
    DISABLED = "disabled"
    SIMULATED = "simulated"
    LIVE = "live"


class ExternalProviderOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"
    KEY_REVOKED = "KEY_REVOKED"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"


class KeyLifecycleOperation(str, Enum):
    CREATE = "CREATE"
    REVOKE = "REVOKE"


@dataclass(frozen=True)
class ExternalBalance:
    """Provider-reported balance observation. Not Kalyx accounting authority."""
    available: str  # decimal string, exact
    used: str
    currency: str
    timestamp: datetime
    provider_reference: Optional[str] = None
    rate_limit: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def evidence_hash(self) -> str:
        payload = {
            "available": self.available,
            "used": self.used,
            "currency": self.currency,
            "provider_reference": self.provider_reference,
            "timestamp": self.timestamp.isoformat(),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ExternalKeyStatus:
    key_id: str
    status: str  # active | revoked | unknown
    account_id: Optional[str] = None
    balance: Optional[ExternalBalance] = None
    rate_limit: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    provider_reference: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def evidence_hash(self) -> str:
        payload = {
            "key_id": self.key_id,
            "status": self.status,
            "account_id": self.account_id,
            "provider_reference": self.provider_reference,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class KeyLifecycleIntent:
    tenant_id: str
    organisation_id: str
    operation: KeyLifecycleOperation
    actor_principal_id: str
    mission_id: Optional[str] = None
    agent_id: Optional[str] = None
    key_id: Optional[str] = None  # required for revoke
    authorization_token: Optional[str] = None
    idempotency_key: Optional[str] = None


@dataclass(frozen=True)
class KeyLifecycleReceipt:
    operation: KeyLifecycleOperation
    outcome: ExternalProviderOutcome
    key_id: Optional[str]
    # Plaintext secret is returned AT MOST once to an authorized operator and MUST NOT be persisted.
    one_time_secret: Optional[str] = None
    provider_reference: Optional[str] = None
    evidence_hash: Optional[str] = None
    error_message: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def audit_safe_dict(self) -> Dict[str, Any]:
        """Serialize without plaintext secrets."""
        return {
            "operation": self.operation.value,
            "outcome": self.outcome.value,
            "key_id": self.key_id,
            "secret_delivered": bool(self.one_time_secret),
            "provider_reference": self.provider_reference,
            "evidence_hash": self.evidence_hash,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class InferenceRequest:
    tenant_id: str
    organisation_id: str
    mission_id: Optional[str]
    agent_id: Optional[str]
    model: str
    messages: list
    authorization_token: str
    idempotency_key: str
    key_id: Optional[str] = None
    max_tokens: Optional[int] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InferenceReceipt:
    """Provider inference telemetry. Does NOT equal Kalyx task success."""
    outcome: ExternalProviderOutcome
    provider_reference: Optional[str]
    model: Optional[str]
    usage: Dict[str, Any]  # tokens, cost if reported
    balance_before: Optional[str] = None  # X-Orbio-Balance style observation
    balance_after: Optional[str] = None
    evidence_hash: Optional[str] = None
    error_message: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def audit_safe_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "provider_reference": self.provider_reference,
            "model": self.model,
            "usage": self.usage,
            "balance_before": self.balance_before,
            "balance_after": self.balance_after,
            "evidence_hash": self.evidence_hash,
            "error_message": self.error_message,
        }
