"""Explicit settlement boundary.

The engine owns authorization and accounting. A settlement adapter is only
allowed to receive an already-authorized settlement intent and return a
verifiable receipt. No adapter gets access to policy secrets or arbitrary
agent prompts.
"""
from dataclasses import dataclass
from typing import Protocol
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
