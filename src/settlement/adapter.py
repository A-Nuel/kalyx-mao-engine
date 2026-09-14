"""Explicit settlement boundary.

The engine owns authorization and accounting. A settlement adapter is only
allowed to receive an already-authorized settlement intent and return a
verifiable receipt. No adapter gets access to policy secrets or arbitrary
agent prompts.
"""
from dataclasses import dataclass
from typing import Protocol, Dict, Any, Optional
import hashlib
import time


@dataclass(frozen=True)
class SettlementIntent:
    organization_id: str
    proposal_id: str
    from_account: str
    destination: str
    amount: int
    authorization_token: str


@dataclass(frozen=True)
class SettlementReceipt:
    adapter: str
    proposal_id: str
    destination: str
    amount: int
    external_reference: str
    success: bool
    evidence_hash: str


class SettlementAdapter(Protocol):
    name: str

    def settle(self, intent: SettlementIntent) -> SettlementReceipt:
        ...


@dataclass(frozen=True)
class ProviderExecutionResult:
    provider_name: str
    operation_id: str
    outcome: str  # SUCCESS, FAILURE, TIMEOUT, UNKNOWN
    provider_reference: Optional[str]
    raw_response: Dict[str, Any]
    error_message: Optional[str] = None
    evidence_hash: Optional[str] = None


@dataclass(frozen=True)
class ProviderStatusResult:
    provider_name: str
    operation_id: str
    outcome: str  # SUCCESS, FAILURE, UNKNOWN
    provider_reference: Optional[str]
    raw_status: Dict[str, Any]
    error_message: Optional[str] = None
    evidence_hash: Optional[str] = None


class ConsequentialProviderAdapter(Protocol):
    name: str

    def prepare(self, operation: Any) -> bool:
        ...

    def execute(self, operation: Any) -> ProviderExecutionResult:
        ...

    def status(self, operation_id: str, idempotency_key: Optional[str] = None) -> ProviderStatusResult:
        ...


class SimulatedSettlementAdapter:
    """Deterministic settlement sink for demos and integration tests."""
    name = "simulated"

    def settle(self, intent: SettlementIntent) -> SettlementReceipt:
        if intent.amount <= 0:
            raise ValueError("Settlement amount must be positive")
        if not intent.authorization_token:
            raise ValueError("Settlement requires an authorization token")
        reference = "sim-" + hashlib.sha256(
            f"{intent.organization_id}|{intent.proposal_id}|{intent.destination}|{intent.amount}".encode()
        ).hexdigest()[:24]
        evidence = hashlib.sha256(
            f"{reference}|{time.time_ns()}".encode()
        ).hexdigest()
        return SettlementReceipt(self.name, intent.proposal_id, intent.destination, intent.amount, reference, True, evidence)
