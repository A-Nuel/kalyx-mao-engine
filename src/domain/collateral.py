"""Domain entities for Phase 18 Credit Collateral — pledging tokenized CREDIT
as an economic commitment behind an autonomous obligation.

Orbio's CREDIT is a standard transferable ERC-20 token; the protocol itself
exposes hold / transfer / sell / activate but no native lock or collateral
facility (activation is one-way and burns the token into non-transferable
API balance). This module is Kalyx's addition: a governed primitive that
sits on top of CREDIT's transferability, distinct from — and never confused
with — Kalyx's internal ORG-credit ledger or Orbio's activated API balance.
See src/external/models.py for that three-way distinction.

State machine:

    PROPOSED -> AUTHORIZED -> LOCKED -> OBLIGATION_ACTIVE -> (
        VERIFIED_SUCCESS -> RELEASED
        VERIFIED_FAILURE -> FORFEITED
    )

Invariants enforced by this module and its tests:
  1. A position cannot exceed the organisation's available (unlocked) CREDIT.
  2. Locked CREDIT cannot simultaneously be released/forfeited more than once.
  3. Only an authorized obligation reference may transition LOCKED -> settled.
  4. Settlement is driven by independent auditor evidence, never by the
     executor's own report of success.
  5. Cross-tenant/cross-org access to a position is impossible (tenant_id +
     organisation_id are part of every identity check, mirroring EscrowAgreement).
  6. Replay of release/forfeit against an already-settled position is rejected.
  7. An UNKNOWN or timed-out verification does NOT default to forfeiture —
     it must stay in OBLIGATION_ACTIVE pending resolution, never silently
     resolve to the org's disadvantage.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class CollateralStatus(str, Enum):
    PROPOSED = "PROPOSED"
    AUTHORIZED = "AUTHORIZED"
    LOCKED = "LOCKED"
    OBLIGATION_ACTIVE = "OBLIGATION_ACTIVE"
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    RELEASED = "RELEASED"
    FORFEITED = "FORFEITED"


# Legal transitions. Enforced by CreditCollateralPosition.transition().
_ALLOWED_TRANSITIONS: Dict[CollateralStatus, tuple] = {
    CollateralStatus.PROPOSED: (CollateralStatus.AUTHORIZED,),
    CollateralStatus.AUTHORIZED: (CollateralStatus.LOCKED,),
    CollateralStatus.LOCKED: (CollateralStatus.OBLIGATION_ACTIVE,),
    CollateralStatus.OBLIGATION_ACTIVE: (
        CollateralStatus.VERIFIED_SUCCESS,
        CollateralStatus.VERIFIED_FAILURE,
    ),
    CollateralStatus.VERIFIED_SUCCESS: (CollateralStatus.RELEASED,),
    CollateralStatus.VERIFIED_FAILURE: (CollateralStatus.FORFEITED,),
    CollateralStatus.RELEASED: (),
    CollateralStatus.FORFEITED: (),
}

TERMINAL_STATES = (CollateralStatus.RELEASED, CollateralStatus.FORFEITED)


class CollateralTransitionError(ValueError):
    """Raised on an illegal state transition (including any replay attempt)."""


@dataclass
class CreditCollateralPosition:
    tenant_id: str
    organisation_id: str
    position_id: str
    obligation_reference: str  # e.g. work_order_id / order_id this position backs
    pledging_org_id: str
    beneficiary_org_id: str
    amount: int  # CREDIT units, 6-decimal atoms to match Orbio's on-chain precision
    asset: str = "CREDIT"
    status: CollateralStatus = CollateralStatus.PROPOSED
    onchain_tx_hash: Optional[str] = None  # lock tx, set on LOCKED
    settlement_tx_hash: Optional[str] = None  # release/forfeit tx, set on terminal state
    authorization_evidence_hash: Optional[str] = None
    verification_evidence_hash: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    locked_at: Optional[str] = None
    settled_at: Optional[str] = None

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        organisation_id: str,
        position_id: str,
        obligation_reference: str,
        pledging_org_id: str,
        beneficiary_org_id: str,
        amount: int,
        asset: str = "CREDIT",
    ) -> "CreditCollateralPosition":
        if amount <= 0:
            raise ValueError("collateral amount must be positive")
        if pledging_org_id == beneficiary_org_id:
            raise ValueError("pledging_org_id and beneficiary_org_id must differ")
        return cls(
            tenant_id=tenant_id,
            organisation_id=organisation_id,
            position_id=position_id,
            obligation_reference=obligation_reference,
            pledging_org_id=pledging_org_id,
            beneficiary_org_id=beneficiary_org_id,
            amount=amount,
            asset=asset,
        )

    def _fingerprint(self, event: str) -> str:
        payload = {
            "position_id": self.position_id,
            "event": event,
            "status": self.status.value,
            "amount": self.amount,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def transition(self, target: CollateralStatus, *, actor: str) -> None:
        """Move to `target`, or raise CollateralTransitionError.

        `actor` is recorded implicitly via the evidence hash chain; callers
        (the coordinator) are responsible for checking that `actor` is the
        authorized obligation/auditor before calling this — this method
        only enforces that the state graph itself is respected, including
        rejecting any transition out of a terminal state (replay protection).
        """
        if self.status in TERMINAL_STATES:
            raise CollateralTransitionError(
                f"position {self.position_id} is terminal ({self.status.value}); "
                f"rejected replayed transition to {target.value} by {actor}"
            )
        allowed = _ALLOWED_TRANSITIONS.get(self.status, ())
        if target not in allowed:
            raise CollateralTransitionError(
                f"illegal transition {self.status.value} -> {target.value} "
                f"for position {self.position_id}"
            )
        self.status = target

    def authorize(self, *, evidence_hash: str) -> None:
        self.transition(CollateralStatus.AUTHORIZED, actor="policy_engine")
        self.authorization_evidence_hash = evidence_hash

    def lock(self, *, onchain_tx_hash: Optional[str]) -> None:
        """onchain_tx_hash is the real transfer() tx hash in LIVE mode, or a
        deterministic simulated reference in SIMULATED mode — never fabricated
        as if it were a real chain reference. Caller (adapter) decides which."""
        self.transition(CollateralStatus.LOCKED, actor="collateral_adapter")
        self.onchain_tx_hash = onchain_tx_hash
        self.locked_at = datetime.now(timezone.utc).isoformat()

    def activate_obligation(self) -> None:
        self.transition(CollateralStatus.OBLIGATION_ACTIVE, actor="marketplace_coordinator")

    def record_verification(self, *, success: bool, evidence_hash: str) -> None:
        """Driven exclusively by independent auditor evidence (invariant 4).
        Never called with the executor's self-reported outcome."""
        target = CollateralStatus.VERIFIED_SUCCESS if success else CollateralStatus.VERIFIED_FAILURE
        self.transition(target, actor="auditor")
        self.verification_evidence_hash = evidence_hash

    def settle(self, *, settlement_tx_hash: Optional[str]) -> CollateralStatus:
        """Resolves VERIFIED_SUCCESS -> RELEASED or VERIFIED_FAILURE -> FORFEITED.
        Idempotency/replay is enforced by transition() rejecting any call once
        the position is already terminal."""
        if self.status == CollateralStatus.VERIFIED_SUCCESS:
            self.transition(CollateralStatus.RELEASED, actor="settlement")
        elif self.status == CollateralStatus.VERIFIED_FAILURE:
            self.transition(CollateralStatus.FORFEITED, actor="settlement")
        else:
            raise CollateralTransitionError(
                f"cannot settle position {self.position_id} from status {self.status.value}; "
                "must be VERIFIED_SUCCESS or VERIFIED_FAILURE"
            )
        self.settlement_tx_hash = settlement_tx_hash
        self.settled_at = datetime.now(timezone.utc).isoformat()
        return self.status

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "position_id": self.position_id,
            "obligation_reference": self.obligation_reference,
            "pledging_org_id": self.pledging_org_id,
            "beneficiary_org_id": self.beneficiary_org_id,
            "amount": self.amount,
            "asset": self.asset,
            "status": self.status.value,
            "onchain_tx_hash": self.onchain_tx_hash,
            "settlement_tx_hash": self.settlement_tx_hash,
            "authorization_evidence_hash": self.authorization_evidence_hash,
            "verification_evidence_hash": self.verification_evidence_hash,
            "created_at": self.created_at,
            "locked_at": self.locked_at,
            "settled_at": self.settled_at,
        }
