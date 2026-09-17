"""Phase 14B.7 — Receipt & event verification for Orbio CREDIT purchases.

Binds execution evidence to the authorized OrbioPurchaseIntent.
Does NOT trust provider success flags alone. Does NOT settle or re-execute.

Flow:
  OrbioPurchaseIntent
    → intent_hash
    → execution result / status evidence
    → OrbioPurchaseVerifier.verify
    → VERIFIED | REJECTED | PENDING_EVIDENCE

PENDING_EVIDENCE is the boundary for Phase 14B.8 UNKNOWN reconciliation:
verification never silently upgrades TIMEOUT/UNKNOWN into SUCCESS.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from src.domain.blockchain import OrbioPurchaseIntent
from src.domain.entities import ConsequentialOperation
from src.domain.enums import ProviderOutcome
from src.domain.events import canonical_json
from src.settlement.adapter import ProviderExecutionResult, ProviderStatusResult


class PurchaseVerificationResult(str, Enum):
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    PENDING_EVIDENCE = "PENDING_EVIDENCE"  # no final receipt yet — 14B.8 territory


class PurchaseVerificationCode(str, Enum):
    OK = "OK"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    PROVIDER_NOT_SUCCESS = "PROVIDER_NOT_SUCCESS"
    WRONG_INTENT_HASH = "WRONG_INTENT_HASH"
    WRONG_EXCHANGE = "WRONG_EXCHANGE"
    WRONG_CHAIN = "WRONG_CHAIN"
    WRONG_NETWORK = "WRONG_NETWORK"
    WRONG_BENEFICIARY = "WRONG_BENEFICIARY"
    WRONG_USDG_AMOUNT = "WRONG_USDG_AMOUNT"
    CREDIT_BELOW_MINIMUM = "CREDIT_BELOW_MINIMUM"
    MISSING_ACTIVATION = "MISSING_ACTIVATION"
    MALFORMED_EVIDENCE = "MALFORMED_EVIDENCE"
    CROSS_TENANT = "CROSS_TENANT"
    CROSS_ORG = "CROSS_ORG"
    EVIDENCE_HASH_MISMATCH = "EVIDENCE_HASH_MISMATCH"
    WRONG_PROVIDER_REFERENCE = "WRONG_PROVIDER_REFERENCE"
    ALREADY_VERIFIED = "ALREADY_VERIFIED"  # idempotent re-verify


@dataclass(frozen=True)
class OrbioPurchaseEvidence:
    """Normalized purchase/activation evidence extracted from provider output.

    Works for SimulatedOrbioExchangeProvider today and real receipts later.
    """

    purchase_intent_hash: str
    provider_reference: str  # tx hash or simulated ref
    chain_id: Optional[int]
    network: Optional[str]
    exchange_contract: str
    usdg_spent: int
    credit_out: int
    min_credit_out: int
    beneficiary: str
    activation_id: str
    tenant_id: Optional[str] = None
    organisation_id: Optional[str] = None
    evidence_hash: Optional[str] = None
    provider_name: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def compute_integrity_hash(self) -> str:
        """Hash over verification-critical fields (detect post-hoc mutation)."""
        data = {
            "purchase_intent_hash": self.purchase_intent_hash,
            "provider_reference": self.provider_reference,
            "chain_id": self.chain_id,
            "network": self.network,
            "exchange_contract": self.exchange_contract.lower() if self.exchange_contract else None,
            "usdg_spent": self.usdg_spent,
            "credit_out": self.credit_out,
            "min_credit_out": self.min_credit_out,
            "beneficiary": self.beneficiary.lower() if self.beneficiary else None,
            "activation_id": self.activation_id,
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
        }
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PurchaseVerificationReport:
    result: PurchaseVerificationResult
    codes: List[str]
    reasons: List[str]
    intent_hash: str
    provider_reference: Optional[str]
    evidence: Optional[OrbioPurchaseEvidence]
    verified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    report_id: str = field(default_factory=lambda: hashlib.sha256(str(datetime.now(timezone.utc)).encode()).hexdigest()[:16])

    def is_verified(self) -> bool:
        return self.result == PurchaseVerificationResult.VERIFIED

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "result": self.result.value,
            "codes": list(self.codes),
            "reasons": list(self.reasons),
            "intent_hash": self.intent_hash,
            "provider_reference": self.provider_reference,
            "verified_at": self.verified_at.isoformat(),
            "evidence_integrity": self.evidence.compute_integrity_hash() if self.evidence else None,
        }


def extract_evidence_from_execution(
    result: ProviderExecutionResult,
    *,
    operation: Optional[ConsequentialOperation] = None,
    intent: Optional[OrbioPurchaseIntent] = None,
) -> Optional[OrbioPurchaseEvidence]:
    """Normalize ProviderExecutionResult.raw_response into OrbioPurchaseEvidence."""
    raw = result.raw_response if isinstance(result.raw_response, dict) else {}
    params = (operation.parameters if operation and isinstance(operation.parameters, dict) else {}) or {}

    # Simulated exchange shape
    usdg = raw.get("usdg_spent", params.get("usdg_in"))
    credit = raw.get("credit_out")
    activation = raw.get("activation_id", "")
    events = raw.get("events") or []
    if not activation and events:
        activation = str(events[0].get("activation_id") or "")

    if usdg is None and credit is None and not activation:
        # Pending / timeout payloads often lack economic fields
        if result.outcome in {ProviderOutcome.TIMEOUT.value, ProviderOutcome.UNKNOWN.value}:
            return None
        # Try BlockchainReceiptEvidence-like dump
        if "transaction_hash" in raw and "intent_hash" in raw:
            return OrbioPurchaseEvidence(
                purchase_intent_hash=str(
                    params.get("purchase_intent_hash") or raw.get("intent_hash") or ""
                ),
                provider_reference=str(result.provider_reference or raw.get("transaction_hash") or ""),
                chain_id=raw.get("chain_id") or params.get("chain_id"),
                network=raw.get("network") or params.get("network"),
                exchange_contract=str(
                    raw.get("recipient") or params.get("exchange_contract") or params.get("recipient") or ""
                ),
                usdg_spent=int(params.get("usdg_in", 0)),
                credit_out=int(raw.get("credit_out", 0) or 0),
                min_credit_out=int(params.get("min_credit_out", 0)),
                beneficiary=str(params.get("beneficiary_bytes32") or params.get("beneficiary") or ""),
                activation_id=str(raw.get("activation_id") or ""),
                tenant_id=operation.tenant_id if operation else None,
                organisation_id=operation.organisation_id if operation else None,
                evidence_hash=result.evidence_hash,
                provider_name=result.provider_name,
                events=list(events),
                raw=raw,
            )
        return None

    intent_hash = str(
        raw.get("purchase_intent_hash")
        or params.get("purchase_intent_hash")
        or (intent.compute_purchase_intent_hash() if intent else "")
    )
    beneficiary = str(
        raw.get("beneficiary")
        or params.get("beneficiary_bytes32")
        or params.get("beneficiary")
        or ""
    )
    exchange = str(
        raw.get("exchange_contract")
        or params.get("exchange_contract")
        or params.get("recipient")
        or ""
    )

    return OrbioPurchaseEvidence(
        purchase_intent_hash=intent_hash,
        provider_reference=str(result.provider_reference or ""),
        chain_id=params.get("chain_id") or raw.get("chain_id"),
        network=params.get("network") or raw.get("network"),
        exchange_contract=exchange,
        usdg_spent=int(usdg or 0),
        credit_out=int(credit or 0),
        min_credit_out=int(raw.get("min_credit_out", params.get("min_credit_out", 0)) or 0),
        beneficiary=beneficiary,
        activation_id=str(activation),
        tenant_id=(
            operation.tenant_id if operation else raw.get("tenant_id")
        ),
        organisation_id=(
            operation.organisation_id if operation else raw.get("organisation_id")
        ),
        evidence_hash=result.evidence_hash,
        provider_name=result.provider_name,
        events=list(events),
        raw=raw,
    )


def extract_evidence_from_status(
    status: ProviderStatusResult,
    *,
    operation: Optional[ConsequentialOperation] = None,
    intent: Optional[OrbioPurchaseIntent] = None,
) -> Optional[OrbioPurchaseEvidence]:
    """Normalize status query evidence (post-timeout reconciliation path)."""
    # Reuse execution extractor shape by wrapping status as a pseudo result
    pseudo = ProviderExecutionResult(
        provider_name=status.provider_name,
        operation_id=status.operation_id,
        outcome=status.outcome,
        provider_reference=status.provider_reference,
        raw_response=status.raw_status if isinstance(status.raw_status, dict) else {},
        error_message=status.error_message,
        evidence_hash=status.evidence_hash,
    )
    return extract_evidence_from_execution(pseudo, operation=operation, intent=intent)


class OrbioPurchaseVerifier:
    """Independent auditor for Orbio purchase evidence vs authorized intent."""

    def __init__(self) -> None:
        # intent_hash -> provider_reference of already-verified evidence (idempotency)
        self._verified: Dict[str, str] = {}

    def verify(
        self,
        intent: OrbioPurchaseIntent,
        evidence: Optional[OrbioPurchaseEvidence],
        *,
        provider_outcome: Optional[str] = None,
        expected_provider_reference: Optional[str] = None,
    ) -> PurchaseVerificationReport:
        intent_hash = intent.compute_purchase_intent_hash()

        if evidence is None:
            # No final evidence — compatible with UNKNOWN / later reconciliation
            if provider_outcome in {
                ProviderOutcome.TIMEOUT.value,
                ProviderOutcome.UNKNOWN.value,
                None,
            }:
                return PurchaseVerificationReport(
                    result=PurchaseVerificationResult.PENDING_EVIDENCE,
                    codes=[PurchaseVerificationCode.MISSING_EVIDENCE.value],
                    reasons=["No final purchase/activation evidence available yet"],
                    intent_hash=intent_hash,
                    provider_reference=None,
                    evidence=None,
                )
            return PurchaseVerificationReport(
                result=PurchaseVerificationResult.REJECTED,
                codes=[PurchaseVerificationCode.MISSING_EVIDENCE.value],
                reasons=["Provider reported terminal outcome without extractable evidence"],
                intent_hash=intent_hash,
                provider_reference=None,
                evidence=None,
            )

        codes: List[str] = []
        reasons: List[str] = []

        def fail(code: PurchaseVerificationCode, reason: str) -> PurchaseVerificationReport:
            return PurchaseVerificationReport(
                result=PurchaseVerificationResult.REJECTED,
                codes=[code.value],
                reasons=[reason],
                intent_hash=intent_hash,
                provider_reference=evidence.provider_reference,
                evidence=evidence,
            )

        # Provider outcome must be success for VERIFIED (TIMEOUT stays pending)
        if provider_outcome is not None and provider_outcome != ProviderOutcome.SUCCESS.value:
            if provider_outcome in {ProviderOutcome.TIMEOUT.value, ProviderOutcome.UNKNOWN.value}:
                return PurchaseVerificationReport(
                    result=PurchaseVerificationResult.PENDING_EVIDENCE,
                    codes=[PurchaseVerificationCode.PROVIDER_NOT_SUCCESS.value],
                    reasons=[f"Provider outcome is {provider_outcome}; awaiting final evidence"],
                    intent_hash=intent_hash,
                    provider_reference=evidence.provider_reference or None,
                    evidence=evidence,
                )
            return fail(
                PurchaseVerificationCode.PROVIDER_NOT_SUCCESS,
                f"Provider outcome '{provider_outcome}' is not SUCCESS",
            )

        # Intent hash binding
        if evidence.purchase_intent_hash != intent_hash:
            return fail(
                PurchaseVerificationCode.WRONG_INTENT_HASH,
                f"Evidence intent hash {evidence.purchase_intent_hash!r} != authorized {intent_hash!r}",
            )

        # Tenant / org
        if evidence.tenant_id is not None and evidence.tenant_id != intent.tenant_id:
            return fail(
                PurchaseVerificationCode.CROSS_TENANT,
                f"Evidence tenant '{evidence.tenant_id}' != intent tenant '{intent.tenant_id}'",
            )
        if evidence.organisation_id is not None and evidence.organisation_id != intent.organisation_id:
            return fail(
                PurchaseVerificationCode.CROSS_ORG,
                f"Evidence org '{evidence.organisation_id}' != intent org '{intent.organisation_id}'",
            )

        # Exchange
        if evidence.exchange_contract.lower() != intent.exchange_contract.lower():
            return fail(
                PurchaseVerificationCode.WRONG_EXCHANGE,
                f"Evidence exchange '{evidence.exchange_contract}' != authorized '{intent.exchange_contract}'",
            )

        # Chain / network when present on evidence
        if evidence.chain_id is not None and int(evidence.chain_id) != int(intent.chain_id):
            return fail(
                PurchaseVerificationCode.WRONG_CHAIN,
                f"Evidence chain_id {evidence.chain_id} != authorized {intent.chain_id}",
            )
        if evidence.network is not None and evidence.network.lower() != intent.network.lower():
            return fail(
                PurchaseVerificationCode.WRONG_NETWORK,
                f"Evidence network '{evidence.network}' != authorized '{intent.network}'",
            )

        # USDG spent must match authorized usdg_in (exact for current sim + intent)
        if int(evidence.usdg_spent) != int(intent.usdg_in):
            return fail(
                PurchaseVerificationCode.WRONG_USDG_AMOUNT,
                f"Evidence usdg_spent {evidence.usdg_spent} != authorized usdg_in {intent.usdg_in}",
            )

        # CREDIT out must meet min_credit_out
        if int(evidence.credit_out) < int(intent.min_credit_out):
            return fail(
                PurchaseVerificationCode.CREDIT_BELOW_MINIMUM,
                f"Evidence credit_out {evidence.credit_out} < min_credit_out {intent.min_credit_out}",
            )

        # Beneficiary (normalize address vs bytes32)
        expected_b32 = intent.beneficiary_as_bytes32().lower()
        got = (evidence.beneficiary or "").lower()
        if got not in {expected_b32, intent.beneficiary.lower()}:
            # Allow address form equal to intent.beneficiary when evidence stored address
            if got != intent.beneficiary.lower() and got != expected_b32:
                return fail(
                    PurchaseVerificationCode.WRONG_BENEFICIARY,
                    f"Evidence beneficiary '{evidence.beneficiary}' does not match authorized beneficiary",
                )

        # Activation event required for purchase success
        if not evidence.activation_id:
            has_activated_event = any(
                (e.get("name") == "Activated" and e.get("activation_id"))
                for e in (evidence.events or [])
                if isinstance(e, dict)
            )
            if not has_activated_event:
                return fail(
                    PurchaseVerificationCode.MISSING_ACTIVATION,
                    "Missing activation_id / Activated event in evidence",
                )

        if not evidence.provider_reference:
            return fail(
                PurchaseVerificationCode.WRONG_PROVIDER_REFERENCE,
                "Missing provider_reference / transaction identity",
            )

        if expected_provider_reference and evidence.provider_reference != expected_provider_reference:
            return fail(
                PurchaseVerificationCode.WRONG_PROVIDER_REFERENCE,
                f"Evidence provider_reference '{evidence.provider_reference}' != expected '{expected_provider_reference}'",
            )

        # Idempotent re-verification
        prior_ref = self._verified.get(intent_hash)
        if prior_ref is not None:
            if prior_ref == evidence.provider_reference:
                return PurchaseVerificationReport(
                    result=PurchaseVerificationResult.VERIFIED,
                    codes=[PurchaseVerificationCode.ALREADY_VERIFIED.value],
                    reasons=["Evidence previously verified for this intent; no duplicate economic effect"],
                    intent_hash=intent_hash,
                    provider_reference=evidence.provider_reference,
                    evidence=evidence,
                )
            return fail(
                PurchaseVerificationCode.WRONG_PROVIDER_REFERENCE,
                f"Intent already verified under different reference '{prior_ref}'",
            )

        self._verified[intent_hash] = evidence.provider_reference
        return PurchaseVerificationReport(
            result=PurchaseVerificationResult.VERIFIED,
            codes=[PurchaseVerificationCode.OK.value],
            reasons=["Purchase evidence matches authorized OrbioPurchaseIntent"],
            intent_hash=intent_hash,
            provider_reference=evidence.provider_reference,
            evidence=evidence,
        )

    def verify_execution(
        self,
        intent: OrbioPurchaseIntent,
        result: ProviderExecutionResult,
        *,
        operation: Optional[ConsequentialOperation] = None,
    ) -> PurchaseVerificationReport:
        evidence = extract_evidence_from_execution(result, operation=operation, intent=intent)
        return self.verify(
            intent,
            evidence,
            provider_outcome=result.outcome,
            expected_provider_reference=result.provider_reference,
        )

    def verify_status(
        self,
        intent: OrbioPurchaseIntent,
        status: ProviderStatusResult,
        *,
        operation: Optional[ConsequentialOperation] = None,
    ) -> PurchaseVerificationReport:
        """Verify evidence obtained after TIMEOUT via independent status query (14B.8 prep)."""
        evidence = extract_evidence_from_status(status, operation=operation, intent=intent)
        return self.verify(
            intent,
            evidence,
            provider_outcome=status.outcome,
            expected_provider_reference=status.provider_reference,
        )

    def was_verified(self, intent_hash: str) -> bool:
        return intent_hash in self._verified
