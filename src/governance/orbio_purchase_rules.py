"""Phase 14B.2 — Deterministic policy for Orbio CREDIT purchase intents.

Evaluates a typed OrbioPurchaseIntent against fixed governance bounds.
Does not execute transactions. Does not sign. Does not talk to RPC.

Decision outcomes:
  ALLOW                         — autonomous path within limits
  DENY                          — hard policy violation
  HUMAN_CONFIRMATION_REQUIRED   — within absolute max but above autonomous ceiling

Human approval, when granted, is cryptographically bound to the exact
purchase intent hash so a modified intent cannot reuse an older approval.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from src.domain.blockchain import (
    ORBIO_CREDIT_MAINNET,
    ORBIO_EXCHANGE_MAINNET,
    USDG_MAINNET,
    OrbioPurchaseIntent,
)
from src.domain.events import canonical_json


class PurchaseDecisionResult(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    HUMAN_CONFIRMATION_REQUIRED = "HUMAN_CONFIRMATION_REQUIRED"


class PurchaseDenialCode(str, Enum):
    UNAUTHORIZED_NETWORK = "UNAUTHORIZED_NETWORK"
    UNAUTHORIZED_EXCHANGE = "UNAUTHORIZED_EXCHANGE"
    UNAUTHORIZED_PAYMENT_TOKEN = "UNAUTHORIZED_PAYMENT_TOKEN"
    UNAUTHORIZED_CREDIT_TOKEN = "UNAUTHORIZED_CREDIT_TOKEN"
    UNAUTHORIZED_BENEFICIARY = "UNAUTHORIZED_BENEFICIARY"
    MAX_USDG_EXCEEDED = "MAX_USDG_EXCEEDED"
    SLIPPAGE_BOUND_INVALID = "SLIPPAGE_BOUND_INVALID"
    MAX_FILLS_EXCEEDED = "MAX_FILLS_EXCEEDED"
    GAS_BOUND_EXCEEDED = "GAS_BOUND_EXCEEDED"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    MISSING_HUMAN_APPROVAL = "MISSING_HUMAN_APPROVAL"
    HUMAN_APPROVAL_INTENT_MISMATCH = "HUMAN_APPROVAL_INTENT_MISMATCH"
    HUMAN_APPROVAL_EXPIRED = "HUMAN_APPROVAL_EXPIRED"
    HUMAN_APPROVAL_TENANT_MISMATCH = "HUMAN_APPROVAL_TENANT_MISMATCH"
    POLICY_BINDING_MISMATCH = "POLICY_BINDING_MISMATCH"
    INVALID_INTENT = "INVALID_INTENT"


@dataclass(frozen=True)
class PurchasePolicyDecision:
    """Auditable outcome of purchase-policy evaluation."""

    result: PurchaseDecisionResult
    policy_id: str
    policy_version: str
    intent_hash: str
    reasons: List[str] = field(default_factory=list)
    denial_codes: List[str] = field(default_factory=list)
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    requires_human_confirmation: bool = False
    autonomous_usdg_ceiling: int = 0
    absolute_usdg_ceiling: int = 0

    def is_allowed(self) -> bool:
        return self.result == PurchaseDecisionResult.ALLOW

    def is_denied(self) -> bool:
        return self.result == PurchaseDecisionResult.DENY

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "result": self.result.value,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "intent_hash": self.intent_hash,
            "reasons": list(self.reasons),
            "denial_codes": list(self.denial_codes),
            "requires_human_confirmation": self.requires_human_confirmation,
            "autonomous_usdg_ceiling": self.autonomous_usdg_ceiling,
            "absolute_usdg_ceiling": self.absolute_usdg_ceiling,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


@dataclass(frozen=True)
class HumanPurchaseApproval:
    """Operator approval bound to a single purchase intent hash."""

    approval_id: str
    intent_hash: str
    tenant_id: str
    organisation_id: str
    operator_id: str
    issued_at: float
    expires_at: float
    notes: Optional[str] = None

    def is_valid_for(
        self,
        intent: OrbioPurchaseIntent,
        *,
        current_time: Optional[float] = None,
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """Return (ok, denial_code, reason)."""
        now = current_time if current_time is not None else time.time()
        if now > self.expires_at:
            return False, PurchaseDenialCode.HUMAN_APPROVAL_EXPIRED.value, (
                f"Human approval expired at {self.expires_at}, current time {now}"
            )
        expected = intent.compute_purchase_intent_hash()
        if self.intent_hash != expected:
            return False, PurchaseDenialCode.HUMAN_APPROVAL_INTENT_MISMATCH.value, (
                "Human approval intent hash does not match current purchase intent"
            )
        if self.tenant_id != intent.tenant_id or self.organisation_id != intent.organisation_id:
            return False, PurchaseDenialCode.HUMAN_APPROVAL_TENANT_MISMATCH.value, (
                "Human approval tenant/organisation does not match purchase intent"
            )
        return True, None, None


class OrbioPurchasePolicy:
    """Deterministic evaluator for OrbioPurchaseIntent.

    Defaults are deliberately conservative (tiny autonomous ceiling).
    Absolute ceiling is the hard deny bound; between autonomous and absolute
    the result is HUMAN_CONFIRMATION_REQUIRED unless a matching approval is supplied.
    """

    policy_id = "POLICY-ORBIO-PURCHASE-01"

    def __init__(
        self,
        *,
        allowed_chain_ids: Optional[Set[int]] = None,
        allowed_networks: Optional[Set[str]] = None,
        allowed_exchange_contracts: Optional[Set[str]] = None,
        allowed_payment_tokens: Optional[Set[str]] = None,
        allowed_credit_tokens: Optional[Set[str]] = None,
        allowed_beneficiaries: Optional[Set[str]] = None,
        # 6-decimal USDG units. Default autonomous: $5; absolute: $25.
        autonomous_usdg_ceiling: int = 5_000_000,
        absolute_usdg_ceiling: int = 25_000_000,
        max_fills: int = 20,
        max_fee_per_gas: int = 100_000_000_000,  # 100 gwei
        max_priority_fee_per_gas: int = 10_000_000_000,
        max_gas_limit: int = 500_000,
        require_beneficiary_allowlist: bool = False,
        human_approval_ttl_seconds: float = 3600.0,
    ):
        # Testnet-first defaults; mainnet 4663 may be added explicitly when ready.
        self.allowed_chain_ids = allowed_chain_ids or {46630, 4663}
        self.allowed_networks = {
            n.lower() for n in (allowed_networks or {"robinhood-testnet", "robinhood", "robinhood-mainnet"})
        }
        self.allowed_exchange_contracts = {
            a.lower() for a in (allowed_exchange_contracts or {ORBIO_EXCHANGE_MAINNET})
        }
        self.allowed_payment_tokens = {
            a.lower() for a in (allowed_payment_tokens or {USDG_MAINNET})
        }
        self.allowed_credit_tokens = {
            a.lower() for a in (allowed_credit_tokens or {ORBIO_CREDIT_MAINNET})
        }
        self.allowed_beneficiaries = {
            a.lower() for a in allowed_beneficiaries
        } if allowed_beneficiaries else set()
        self.require_beneficiary_allowlist = require_beneficiary_allowlist

        if autonomous_usdg_ceiling > absolute_usdg_ceiling:
            raise ValueError("autonomous_usdg_ceiling cannot exceed absolute_usdg_ceiling")

        self.autonomous_usdg_ceiling = autonomous_usdg_ceiling
        self.absolute_usdg_ceiling = absolute_usdg_ceiling
        self.max_fills = max_fills
        self.max_fee_per_gas = max_fee_per_gas
        self.max_priority_fee_per_gas = max_priority_fee_per_gas
        self.max_gas_limit = max_gas_limit
        self.human_approval_ttl_seconds = human_approval_ttl_seconds

        # intent_hash -> HumanPurchaseApproval
        self._approvals: Dict[str, HumanPurchaseApproval] = {}

    def get_policy_version(self) -> str:
        meta = {
            "policy_id": self.policy_id,
            "allowed_chain_ids": sorted(self.allowed_chain_ids),
            "allowed_networks": sorted(self.allowed_networks),
            "allowed_exchange_contracts": sorted(self.allowed_exchange_contracts),
            "allowed_payment_tokens": sorted(self.allowed_payment_tokens),
            "allowed_credit_tokens": sorted(self.allowed_credit_tokens),
            "allowed_beneficiaries": sorted(self.allowed_beneficiaries),
            "require_beneficiary_allowlist": self.require_beneficiary_allowlist,
            "autonomous_usdg_ceiling": self.autonomous_usdg_ceiling,
            "absolute_usdg_ceiling": self.absolute_usdg_ceiling,
            "max_fills": self.max_fills,
            "max_fee_per_gas": self.max_fee_per_gas,
            "max_priority_fee_per_gas": self.max_priority_fee_per_gas,
            "max_gas_limit": self.max_gas_limit,
        }
        return hashlib.sha256(canonical_json(meta).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Human confirmation gate
    # ------------------------------------------------------------------

    def issue_human_approval(
        self,
        intent: OrbioPurchaseIntent,
        operator_id: str = "OPERATOR",
        *,
        notes: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
        current_time: Optional[float] = None,
    ) -> HumanPurchaseApproval:
        """Record operator approval bound to this intent's purchase hash."""
        now = current_time if current_time is not None else time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.human_approval_ttl_seconds
        intent_hash = intent.compute_purchase_intent_hash()
        approval = HumanPurchaseApproval(
            approval_id=str(uuid.uuid4()),
            intent_hash=intent_hash,
            tenant_id=intent.tenant_id,
            organisation_id=intent.organisation_id,
            operator_id=operator_id,
            issued_at=now,
            expires_at=now + ttl,
            notes=notes,
        )
        self._approvals[intent_hash] = approval
        return approval

    def revoke_human_approval(self, intent_hash: str) -> bool:
        return self._approvals.pop(intent_hash, None) is not None

    def get_human_approval(self, intent_hash: str) -> Optional[HumanPurchaseApproval]:
        return self._approvals.get(intent_hash)

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        intent: OrbioPurchaseIntent,
        *,
        human_approval: Optional[HumanPurchaseApproval] = None,
        current_time: Optional[float] = None,
    ) -> PurchasePolicyDecision:
        """Evaluate intent. Same inputs always produce the same decision class."""
        intent_hash = intent.compute_purchase_intent_hash()
        policy_version = self.get_policy_version()
        reasons: List[str] = []
        codes: List[str] = []
        now = current_time if current_time is not None else time.time()

        def deny(code: PurchaseDenialCode, reason: str) -> PurchasePolicyDecision:
            return PurchasePolicyDecision(
                result=PurchaseDecisionResult.DENY,
                policy_id=self.policy_id,
                policy_version=policy_version,
                intent_hash=intent_hash,
                reasons=[reason],
                denial_codes=[code.value],
                autonomous_usdg_ceiling=self.autonomous_usdg_ceiling,
                absolute_usdg_ceiling=self.absolute_usdg_ceiling,
            )

        # --- hard bounds ---
        if intent.chain_id not in self.allowed_chain_ids:
            return deny(
                PurchaseDenialCode.UNAUTHORIZED_NETWORK,
                f"chain_id {intent.chain_id} not in allowed set {sorted(self.allowed_chain_ids)}",
            )

        if intent.network.lower() not in self.allowed_networks:
            return deny(
                PurchaseDenialCode.UNAUTHORIZED_NETWORK,
                f"network '{intent.network}' not in allowed set {sorted(self.allowed_networks)}",
            )

        if intent.exchange_contract.lower() not in self.allowed_exchange_contracts:
            return deny(
                PurchaseDenialCode.UNAUTHORIZED_EXCHANGE,
                f"exchange_contract '{intent.exchange_contract}' is not authorized",
            )

        if intent.payment_token.lower() not in self.allowed_payment_tokens:
            return deny(
                PurchaseDenialCode.UNAUTHORIZED_PAYMENT_TOKEN,
                f"payment_token '{intent.payment_token}' is not authorized",
            )

        if intent.credit_token.lower() not in self.allowed_credit_tokens:
            return deny(
                PurchaseDenialCode.UNAUTHORIZED_CREDIT_TOKEN,
                f"credit_token '{intent.credit_token}' is not authorized",
            )

        beneficiary_key = intent.beneficiary.lower()
        if self.require_beneficiary_allowlist or self.allowed_beneficiaries:
            # Accept either address form or bytes32 form if the address is allowlisted.
            from src.domain.blockchain import address_to_beneficiary_bytes32, ETH_ADDRESS_PATTERN

            allowed = False
            if beneficiary_key in self.allowed_beneficiaries:
                allowed = True
            elif ETH_ADDRESS_PATTERN.match(beneficiary_key):
                b32 = address_to_beneficiary_bytes32(beneficiary_key).lower()
                if b32 in self.allowed_beneficiaries:
                    allowed = True
            else:
                # bytes32 provided — check whether any allowlisted address maps to it
                for addr in self.allowed_beneficiaries:
                    if ETH_ADDRESS_PATTERN.match(addr):
                        if address_to_beneficiary_bytes32(addr).lower() == beneficiary_key:
                            allowed = True
                            break
                    elif addr == beneficiary_key:
                        allowed = True
                        break
            if not allowed:
                return deny(
                    PurchaseDenialCode.UNAUTHORIZED_BENEFICIARY,
                    f"beneficiary '{intent.beneficiary}' is not on the approved allowlist",
                )

        if intent.usdg_in > self.absolute_usdg_ceiling:
            return deny(
                PurchaseDenialCode.MAX_USDG_EXCEEDED,
                f"usdg_in {intent.usdg_in} exceeds absolute ceiling {self.absolute_usdg_ceiling}",
            )

        if intent.min_credit_out > intent.usdg_in:
            # Face value of CREDIT is $1; requiring more CREDIT out than USDG in is invalid
            # at any realistic discount ≤ 100%. Hard deny.
            return deny(
                PurchaseDenialCode.SLIPPAGE_BOUND_INVALID,
                f"min_credit_out ({intent.min_credit_out}) exceeds usdg_in ({intent.usdg_in})",
            )

        if intent.min_credit_out <= 0 and intent.usdg_in > 0:
            return deny(
                PurchaseDenialCode.SLIPPAGE_BOUND_INVALID,
                "min_credit_out must be positive when usdg_in > 0 (unbounded slippage forbidden)",
            )

        if intent.max_fills > self.max_fills:
            return deny(
                PurchaseDenialCode.MAX_FILLS_EXCEEDED,
                f"max_fills {intent.max_fills} exceeds policy maximum {self.max_fills}",
            )

        if intent.max_fee_per_gas > self.max_fee_per_gas:
            return deny(
                PurchaseDenialCode.GAS_BOUND_EXCEEDED,
                f"max_fee_per_gas {intent.max_fee_per_gas} exceeds ceiling {self.max_fee_per_gas}",
            )

        if intent.max_priority_fee_per_gas > self.max_priority_fee_per_gas:
            return deny(
                PurchaseDenialCode.GAS_BOUND_EXCEEDED,
                f"max_priority_fee_per_gas {intent.max_priority_fee_per_gas} exceeds ceiling "
                f"{self.max_priority_fee_per_gas}",
            )

        if intent.gas_limit > self.max_gas_limit:
            return deny(
                PurchaseDenialCode.GAS_BOUND_EXCEEDED,
                f"gas_limit {intent.gas_limit} exceeds ceiling {self.max_gas_limit}",
            )

        if intent.deadline is not None and now > intent.deadline:
            return deny(
                PurchaseDenialCode.DEADLINE_EXPIRED,
                f"purchase intent deadline {intent.deadline} has passed (now={now})",
            )

        # --- human confirmation band ---
        needs_human = intent.usdg_in > self.autonomous_usdg_ceiling

        if needs_human:
            approval = human_approval or self._approvals.get(intent_hash)
            if approval is None:
                return PurchasePolicyDecision(
                    result=PurchaseDecisionResult.HUMAN_CONFIRMATION_REQUIRED,
                    policy_id=self.policy_id,
                    policy_version=policy_version,
                    intent_hash=intent_hash,
                    reasons=[
                        f"usdg_in {intent.usdg_in} exceeds autonomous ceiling "
                        f"{self.autonomous_usdg_ceiling}; human confirmation required"
                    ],
                    denial_codes=[PurchaseDenialCode.MISSING_HUMAN_APPROVAL.value],
                    requires_human_confirmation=True,
                    autonomous_usdg_ceiling=self.autonomous_usdg_ceiling,
                    absolute_usdg_ceiling=self.absolute_usdg_ceiling,
                )

            ok, code, reason = approval.is_valid_for(intent, current_time=now)
            if not ok:
                return deny(
                    PurchaseDenialCode(code) if code else PurchaseDenialCode.MISSING_HUMAN_APPROVAL,
                    reason or "human approval invalid",
                )

            reasons.append(
                f"Human approval {approval.approval_id} by {approval.operator_id} accepted "
                f"for intent_hash={intent_hash[:16]}…"
            )

        return PurchasePolicyDecision(
            result=PurchaseDecisionResult.ALLOW,
            policy_id=self.policy_id,
            policy_version=policy_version,
            intent_hash=intent_hash,
            reasons=reasons or ["Purchase within autonomous policy bounds"],
            denial_codes=[],
            requires_human_confirmation=False,
            autonomous_usdg_ceiling=self.autonomous_usdg_ceiling,
            absolute_usdg_ceiling=self.absolute_usdg_ceiling,
        )
