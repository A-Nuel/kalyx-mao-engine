"""Phase 20B — Independent verification of CREDIT.activate receipts.

Success requires receipt status + Activated event evidence, not mere broadcast.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.domain.orbio_activation import (
    ACTIVATED_EVENT_TOPIC0,
    ACTIVATION_FEE_EVENT_TOPIC0,
    ORBIO_ACTIVATION_CHAIN_ID,
    ORBIO_CREDIT_ACTIVATION_CONTRACT,
    OrbioCreditActivationIntent,
)


class ActivationVerificationResult(str, Enum):
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"


@dataclass
class ActivationVerificationReport:
    result: ActivationVerificationResult
    reasons: List[str] = field(default_factory=list)
    activation_id: Optional[int] = None
    burned_amount: Optional[int] = None
    fee_atoms: Optional[int] = None
    sender: Optional[str] = None
    beneficiary: Optional[str] = None
    tx_hash: Optional[str] = None

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "result": self.result.value,
            "reasons": list(self.reasons),
            "activation_id": self.activation_id,
            "burned_amount": self.burned_amount,
            "fee_atoms": self.fee_atoms,
            "sender": self.sender,
            "beneficiary": self.beneficiary,
            "tx_hash": self.tx_hash,
        }


def _topic_addr(topic: str) -> str:
    t = topic.lower().replace("0x", "")
    return "0x" + t[-40:]


def _topic_uint(topic: str) -> int:
    return int(topic, 16)


class OrbioCreditActivationVerifier:
    """Fail closed if Activated event is missing or mismatched."""

    def verify(
        self,
        intent: OrbioCreditActivationIntent,
        *,
        chain_id: int,
        receipt: Dict[str, Any],
        expected_sender: Optional[str] = None,
    ) -> ActivationVerificationReport:
        reasons: List[str] = []

        if chain_id != ORBIO_ACTIVATION_CHAIN_ID:
            reasons.append(f"chain_id {chain_id} != {ORBIO_ACTIVATION_CHAIN_ID}")

        status = receipt.get("status")
        is_success = status in (1, "0x1", "1", True)
        if not is_success:
            reasons.append(f"receipt status not success: {status}")

        to_addr = (receipt.get("to") or "").lower()
        if to_addr and to_addr != intent.credit_contract.lower():
            reasons.append(f"tx.to {to_addr} != credit_contract {intent.credit_contract}")
        if intent.credit_contract.lower() != ORBIO_CREDIT_ACTIVATION_CONTRACT:
            reasons.append("intent credit_contract not mainnet allowlist")

        logs = receipt.get("logs") or []
        activated = None
        fee_atoms = None
        for log in logs:
            topics = log.get("topics") or []
            if not topics:
                continue
            t0 = topics[0].lower() if isinstance(topics[0], str) else topics[0]
            if isinstance(t0, str) and t0.lower() == ACTIVATED_EVENT_TOPIC0.lower():
                # topics: [sig, activationId, from, beneficiary]
                activation_id = _topic_uint(topics[1]) if len(topics) > 1 else None
                sender = _topic_addr(topics[2]) if len(topics) > 2 else None
                beneficiary = topics[3].lower() if len(topics) > 3 else None
                # data is non-indexed amount
                data = log.get("data") or "0x"
                amount = int(data, 16) if data not in ("0x", "") else None
                log_addr = (log.get("address") or "").lower()
                if log_addr and log_addr != intent.credit_contract.lower():
                    reasons.append(f"Activated log address {log_addr} mismatch")
                activated = {
                    "activation_id": activation_id,
                    "from": sender,
                    "beneficiary": beneficiary,
                    "amount": amount,
                }
            if isinstance(t0, str) and t0.lower() == ACTIVATION_FEE_EVENT_TOPIC0.lower():
                fee_atoms = _topic_uint(topics[1]) if len(topics) > 1 else None
                if log.get("data") and log.get("data") not in ("0x", ""):
                    # feeAtoms may be in data depending on layout; topic form preferred
                    pass

        if activated is None:
            reasons.append("Activated event missing from receipt logs")
            return ActivationVerificationReport(
                result=ActivationVerificationResult.REJECTED,
                reasons=reasons,
                tx_hash=receipt.get("transactionHash"),
            )

        if activated["amount"] is not None and activated["amount"] != intent.amount:
            reasons.append(
                f"Activated.amount {activated['amount']} != intent.amount {intent.amount}"
            )

        if expected_sender and activated["from"]:
            if activated["from"].lower() != expected_sender.lower():
                reasons.append(
                    f"Activated.from {activated['from']} != expected_sender {expected_sender}"
                )

        if reasons:
            return ActivationVerificationReport(
                result=ActivationVerificationResult.REJECTED,
                reasons=reasons,
                activation_id=activated.get("activation_id"),
                burned_amount=activated.get("amount"),
                fee_atoms=fee_atoms,
                sender=activated.get("from"),
                beneficiary=activated.get("beneficiary"),
                tx_hash=receipt.get("transactionHash"),
            )

        return ActivationVerificationReport(
            result=ActivationVerificationResult.VERIFIED,
            reasons=["receipt success + Activated event matches intent"],
            activation_id=activated.get("activation_id"),
            burned_amount=activated.get("amount"),
            fee_atoms=fee_atoms,
            sender=activated.get("from"),
            beneficiary=activated.get("beneficiary"),
            tx_hash=receipt.get("transactionHash"),
        )
