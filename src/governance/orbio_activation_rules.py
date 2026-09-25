"""Phase 20B — Deterministic policy for Orbio CREDIT activation (already-held).

Mainnet-only (4663). Hard ceiling MAX_ACTIVATION_AMOUNT = 1_000_000.
First production path always requires human confirmation.
Does not execute, sign, or broadcast.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from src.domain.orbio_activation import (
    MAX_ACTIVATION_AMOUNT,
    ORBIO_ACTIVATION_CHAIN_ID,
    ORBIO_ACTIVATION_NETWORK,
    ORBIO_CREDIT_ACTIVATION_CONTRACT,
    OrbioCreditActivationIntent,
)
from src.domain.events import canonical_json


class ActivationDecisionResult(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    HUMAN_CONFIRMATION_REQUIRED = "HUMAN_CONFIRMATION_REQUIRED"


class ActivationDenialCode(str, Enum):
    UNAUTHORIZED_NETWORK = "UNAUTHORIZED_NETWORK"
    UNAUTHORIZED_CONTRACT = "UNAUTHORIZED_CONTRACT"
    AMOUNT_EXCEEDS_CEILING = "AMOUNT_EXCEEDS_CEILING"
    AMOUNT_INVALID = "AMOUNT_INVALID"
    GAS_BOUND_EXCEEDED = "GAS_BOUND_EXCEEDED"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    MISSING_HUMAN_APPROVAL = "MISSING_HUMAN_APPROVAL"
    HUMAN_APPROVAL_INTENT_MISMATCH = "HUMAN_APPROVAL_INTENT_MISMATCH"
    HUMAN_APPROVAL_EXPIRED = "HUMAN_APPROVAL_EXPIRED"
    HUMAN_APPROVAL_TENANT_MISMATCH = "HUMAN_APPROVAL_TENANT_MISMATCH"
    HUMAN_APPROVAL_REVOKED = "HUMAN_APPROVAL_REVOKED"
    TESTNET_FORBIDDEN = "TESTNET_FORBIDDEN"
    SIMULATION_FORBIDDEN = "SIMULATION_FORBIDDEN"


@dataclass(frozen=True)
class ActivationPolicyDecision:
    result: ActivationDecisionResult
    policy_id: str
    policy_version: str
    intent_hash: str
    reasons: List[str] = field(default_factory=list)
    denial_codes: List[str] = field(default_factory=list)
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    requires_human_confirmation: bool = False
    max_activation_amount: int = MAX_ACTIVATION_AMOUNT

    def is_allowed(self) -> bool:
        return self.result == ActivationDecisionResult.ALLOW

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
            "max_activation_amount": self.max_activation_amount,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


@dataclass(frozen=True)
class HumanActivationApproval:
    """Operator approval bound to one activation intent hash."""

    approval_id: str
    intent_hash: str
    tenant_id: str
    organisation_id: str
    operator_id: str
    chain_id: int
    credit_contract: str
    activation_amount: int
    issued_at: float
    expires_at: float
    notes: Optional[str] = None
    issuance_token: Optional[str] = None

    def is_valid_for(
        self,
        intent: OrbioCreditActivationIntent,
        *,
        current_time: Optional[float] = None,
    ) -> tuple[bool, Optional[str], Optional[str]]:
        now = current_time if current_time is not None else time.time()
        if now > self.expires_at:
            return False, ActivationDenialCode.HUMAN_APPROVAL_EXPIRED.value, (
                f"Human approval expired at {self.expires_at}"
            )
        expected = intent.compute_activation_intent_hash()
        if self.intent_hash != expected:
            return False, ActivationDenialCode.HUMAN_APPROVAL_INTENT_MISMATCH.value, (
                "Human approval intent hash does not match activation intent"
            )
        if self.tenant_id != intent.tenant_id or self.organisation_id != intent.organisation_id:
            return False, ActivationDenialCode.HUMAN_APPROVAL_TENANT_MISMATCH.value, (
                "Human approval tenant/organisation mismatch"
            )
        if self.chain_id != intent.chain_id:
            return False, ActivationDenialCode.HUMAN_APPROVAL_INTENT_MISMATCH.value, (
                "Human approval chain_id mismatch"
            )
        if self.credit_contract.lower() != intent.credit_contract.lower():
            return False, ActivationDenialCode.HUMAN_APPROVAL_INTENT_MISMATCH.value, (
                "Human approval credit_contract mismatch"
            )
        if self.activation_amount != intent.amount:
            return False, ActivationDenialCode.HUMAN_APPROVAL_INTENT_MISMATCH.value, (
                "Human approval activation_amount mismatch"
            )
        return True, None, None


class OrbioCreditActivationPolicy:
    """Fail-closed evaluator for OrbioCreditActivationIntent."""

    policy_id = "POLICY-ORBIO-ACTIVATION-01"

    def __init__(
        self,
        *,
        max_activation_amount: int = MAX_ACTIVATION_AMOUNT,
        allowed_chain_id: int = ORBIO_ACTIVATION_CHAIN_ID,
        allowed_network: str = ORBIO_ACTIVATION_NETWORK,
        allowed_credit_contract: str = ORBIO_CREDIT_ACTIVATION_CONTRACT,
        max_fee_per_gas: int = 100_000_000_000,
        max_priority_fee_per_gas: int = 10_000_000_000,
        max_gas_limit: int = 500_000,
        require_human_approval: bool = True,
        human_approval_ttl_seconds: float = 3600.0,
        signing_secret: Optional[str] = None,
        forbid_testnet: bool = True,
        forbid_simulation: bool = True,
    ):
        if max_activation_amount <= 0:
            raise ValueError("max_activation_amount must be positive")
        self.max_activation_amount = max_activation_amount
        self.allowed_chain_id = allowed_chain_id
        self.allowed_network = allowed_network.lower()
        self.allowed_credit_contract = allowed_credit_contract.lower()
        self.max_fee_per_gas = max_fee_per_gas
        self.max_priority_fee_per_gas = max_priority_fee_per_gas
        self.max_gas_limit = max_gas_limit
        self.require_human_approval = require_human_approval
        self.human_approval_ttl_seconds = human_approval_ttl_seconds
        self.forbid_testnet = forbid_testnet
        self.forbid_simulation = forbid_simulation
        self._secret = (signing_secret or str(uuid.uuid4())).encode("utf-8")
        self._approvals: Dict[str, HumanActivationApproval] = {}
        self._issued: Dict[str, HumanActivationApproval] = {}
        self._revoked: Set[str] = set()

    def get_policy_version(self) -> str:
        meta = {
            "policy_id": self.policy_id,
            "max_activation_amount": self.max_activation_amount,
            "allowed_chain_id": self.allowed_chain_id,
            "allowed_network": self.allowed_network,
            "allowed_credit_contract": self.allowed_credit_contract,
            "require_human_approval": self.require_human_approval,
            "forbid_testnet": self.forbid_testnet,
        }
        return hashlib.sha256(canonical_json(meta).encode("utf-8")).hexdigest()

    def _issuance_token(
        self,
        approval_id: str,
        intent_hash: str,
        tenant_id: str,
        organisation_id: str,
        operator_id: str,
        chain_id: int,
        credit_contract: str,
        activation_amount: int,
        issued_at: float,
        expires_at: float,
    ) -> str:
        payload = (
            f"{approval_id}:{intent_hash}:{tenant_id}:{organisation_id}:{operator_id}:"
            f"{chain_id}:{credit_contract}:{activation_amount}:{issued_at}:{expires_at}"
        )
        return hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue_human_approval(
        self,
        intent: OrbioCreditActivationIntent,
        operator_id: str = "OPERATOR",
        *,
        notes: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
        current_time: Optional[float] = None,
    ) -> HumanActivationApproval:
        now = current_time if current_time is not None else time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.human_approval_ttl_seconds
        intent_hash = intent.compute_activation_intent_hash()
        approval_id = str(uuid.uuid4())
        issued_at = now
        expires_at = now + ttl
        token = self._issuance_token(
            approval_id,
            intent_hash,
            intent.tenant_id,
            intent.organisation_id,
            operator_id,
            intent.chain_id,
            intent.credit_contract,
            intent.amount,
            issued_at,
            expires_at,
        )
        approval = HumanActivationApproval(
            approval_id=approval_id,
            intent_hash=intent_hash,
            tenant_id=intent.tenant_id,
            organisation_id=intent.organisation_id,
            operator_id=operator_id,
            chain_id=intent.chain_id,
            credit_contract=intent.credit_contract,
            activation_amount=intent.amount,
            issued_at=issued_at,
            expires_at=expires_at,
            notes=notes,
            issuance_token=token,
        )
        self._approvals[intent_hash] = approval
        self._issued[approval_id] = approval
        return approval

    def revoke_human_approval(self, approval_id_or_hash: str) -> bool:
        if approval_id_or_hash in self._approvals:
            app = self._approvals.pop(approval_id_or_hash)
            self._revoked.add(app.approval_id)
            self._issued.pop(app.approval_id, None)
            return True
        if approval_id_or_hash in self._issued:
            app = self._issued.pop(approval_id_or_hash)
            self._revoked.add(app.approval_id)
            self._approvals.pop(app.intent_hash, None)
            return True
        self._revoked.add(approval_id_or_hash)
        return True

    def evaluate(
        self,
        intent: OrbioCreditActivationIntent,
        *,
        human_approval: Optional[HumanActivationApproval] = None,
        current_time: Optional[float] = None,
        runtime_mode: Optional[str] = None,
    ) -> ActivationPolicyDecision:
        intent_hash = intent.compute_activation_intent_hash()
        policy_version = self.get_policy_version()
        now = current_time if current_time is not None else time.time()

        def deny(code: ActivationDenialCode, reason: str) -> ActivationPolicyDecision:
            return ActivationPolicyDecision(
                result=ActivationDecisionResult.DENY,
                policy_id=self.policy_id,
                policy_version=policy_version,
                intent_hash=intent_hash,
                reasons=[reason],
                denial_codes=[code.value],
                max_activation_amount=self.max_activation_amount,
            )

        # Forbid testnet / simulation for this path
        if self.forbid_testnet and intent.chain_id == 46630:
            return deny(
                ActivationDenialCode.TESTNET_FORBIDDEN,
                "Robinhood testnet (46630) has no deployed Orbio contracts; activation forbidden",
            )
        if self.forbid_testnet and intent.chain_id != self.allowed_chain_id:
            return deny(
                ActivationDenialCode.UNAUTHORIZED_NETWORK,
                f"chain_id {intent.chain_id} not allowed; required {self.allowed_chain_id}",
            )
        if intent.network.lower() not in {self.allowed_network, "robinhood"}:
            # allow alias "robinhood" only if chain_id already matched mainnet
            if intent.chain_id != self.allowed_chain_id:
                return deny(
                    ActivationDenialCode.UNAUTHORIZED_NETWORK,
                    f"network '{intent.network}' not allowed for activation",
                )

        if self.forbid_simulation and runtime_mode and runtime_mode.lower() in {
            "simulation",
            "simulated",
            "sim",
        }:
            return deny(
                ActivationDenialCode.SIMULATION_FORBIDDEN,
                "Mainnet activation cannot run under simulation runtime mode",
            )

        if intent.credit_contract.lower() != self.allowed_credit_contract:
            return deny(
                ActivationDenialCode.UNAUTHORIZED_CONTRACT,
                f"credit_contract '{intent.credit_contract}' not allowlisted",
            )

        if intent.amount <= 0:
            return deny(ActivationDenialCode.AMOUNT_INVALID, "amount must be positive")

        if intent.amount > self.max_activation_amount:
            return deny(
                ActivationDenialCode.AMOUNT_EXCEEDS_CEILING,
                f"amount {intent.amount} exceeds ceiling {self.max_activation_amount}",
            )

        if intent.max_fee_per_gas > self.max_fee_per_gas:
            return deny(
                ActivationDenialCode.GAS_BOUND_EXCEEDED,
                f"max_fee_per_gas {intent.max_fee_per_gas} exceeds policy",
            )
        if intent.max_priority_fee_per_gas > self.max_priority_fee_per_gas:
            return deny(
                ActivationDenialCode.GAS_BOUND_EXCEEDED,
                f"max_priority_fee_per_gas exceeds policy",
            )
        if intent.gas_limit > self.max_gas_limit:
            return deny(
                ActivationDenialCode.GAS_BOUND_EXCEEDED,
                f"gas_limit {intent.gas_limit} exceeds policy",
            )

        if intent.deadline is not None and now > intent.deadline:
            return deny(ActivationDenialCode.DEADLINE_EXPIRED, "activation deadline expired")

        # Human gate (required for first production path)
        if self.require_human_approval:
            approval = human_approval or self._approvals.get(intent_hash)
            if approval is None:
                return ActivationPolicyDecision(
                    result=ActivationDecisionResult.HUMAN_CONFIRMATION_REQUIRED,
                    policy_id=self.policy_id,
                    policy_version=policy_version,
                    intent_hash=intent_hash,
                    reasons=["First mainnet activation requires explicit human approval"],
                    denial_codes=[ActivationDenialCode.MISSING_HUMAN_APPROVAL.value],
                    requires_human_confirmation=True,
                    max_activation_amount=self.max_activation_amount,
                )
            if approval.approval_id in self._revoked:
                return deny(
                    ActivationDenialCode.HUMAN_APPROVAL_REVOKED,
                    "Human approval was revoked",
                )
            ok, code, reason = approval.is_valid_for(intent, current_time=now)
            if not ok:
                return deny(
                    ActivationDenialCode(code) if code else ActivationDenialCode.HUMAN_APPROVAL_INTENT_MISMATCH,
                    reason or "invalid human approval",
                )

        return ActivationPolicyDecision(
            result=ActivationDecisionResult.ALLOW,
            policy_id=self.policy_id,
            policy_version=policy_version,
            intent_hash=intent_hash,
            reasons=["Activation within ceiling, mainnet allowlist, and human approval"],
            requires_human_confirmation=False,
            max_activation_amount=self.max_activation_amount,
        )
