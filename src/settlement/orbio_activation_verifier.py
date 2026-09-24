"""Phase 20B — Independent verification of CREDIT.activate receipts.

Official Orbio CREDIT ABI (https://www.orbio.so/protocol/abi/credit.json):

  event Activated(
      uint256 indexed activationId,
      address indexed from,
      bytes32 indexed beneficiary,
      uint256 amount              // non-indexed → log data
  );

  event ActivationFeeCharged(
      uint256 indexed activationId,
      uint256 feeAtoms            // non-indexed → log data
  );

Fail closed: missing receipt.to, missing expected_sender, missing/mismatched
Activated event → REJECTED. Success is never inferred from broadcast alone.
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


def _data_uint(data: str) -> Optional[int]:
    if not data or data in ("0x", "0x0", ""):
        return None
    try:
        return int(data, 16)
    except ValueError:
        return None


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

        # expected_sender is mandatory for first production path
        if not expected_sender:
            return ActivationVerificationReport(
                result=ActivationVerificationResult.REJECTED,
                reasons=["expected_sender is required for production activation verification"],
                tx_hash=receipt.get("transactionHash"),
            )

        if chain_id != ORBIO_ACTIVATION_CHAIN_ID:
            reasons.append(f"chain_id {chain_id} != {ORBIO_ACTIVATION_CHAIN_ID}")

        status = receipt.get("status")
        is_success = status in (1, "0x1", "1", True)
        if not is_success:
            reasons.append(f"receipt status not success: {status}")

        # receipt.to is mandatory and must equal CREDIT allowlist
        if "to" not in receipt or receipt.get("to") is None or receipt.get("to") == "":
            reasons.append("receipt.to is missing")
        else:
            to_addr = str(receipt["to"]).lower()
            if to_addr != ORBIO_CREDIT_ACTIVATION_CONTRACT:
                reasons.append(
                    f"receipt.to {to_addr} != allowlisted CREDIT {ORBIO_CREDIT_ACTIVATION_CONTRACT}"
                )
            if to_addr != intent.credit_contract.lower():
                reasons.append(
                    f"receipt.to {to_addr} != intent.credit_contract {intent.credit_contract}"
                )

        if intent.credit_contract.lower() != ORBIO_CREDIT_ACTIVATION_CONTRACT:
            reasons.append("intent credit_contract not mainnet allowlist")

        logs = receipt.get("logs") or []
        activated: Optional[Dict[str, Any]] = None
        fee_atoms: Optional[int] = None

        for log in logs:
            topics = log.get("topics") or []
            if not topics:
                continue
            t0 = topics[0]
            if not isinstance(t0, str):
                continue
            t0_l = t0.lower()

            if t0_l == ACTIVATED_EVENT_TOPIC0.lower():
                # topics: [sig, activationId, from, beneficiary]; data: amount
                if len(topics) < 4:
                    reasons.append("Activated event has fewer than 4 topics (malformed)")
                    continue
                log_addr = (log.get("address") or "").lower()
                if not log_addr:
                    reasons.append("Activated log missing address")
                    continue
                if log_addr != ORBIO_CREDIT_ACTIVATION_CONTRACT:
                    reasons.append(
                        f"Activated log address {log_addr} != allowlisted CREDIT"
                    )
                    continue
                if log_addr != intent.credit_contract.lower():
                    reasons.append(
                        f"Activated log address {log_addr} != intent.credit_contract"
                    )
                    continue

                amount = _data_uint(log.get("data") or "0x")
                if amount is None:
                    reasons.append("Activated event amount data missing or malformed")
                    continue

                activated = {
                    "activation_id": _topic_uint(topics[1]),
                    "from": _topic_addr(topics[2]),
                    "beneficiary": topics[3].lower(),
                    "amount": amount,
                }

            elif t0_l == ACTIVATION_FEE_EVENT_TOPIC0.lower():
                # topics: [sig, activationId]; data: feeAtoms (non-indexed)
                fee_atoms = _data_uint(log.get("data") or "0x")

        if activated is None:
            reasons.append("Activated event missing from receipt logs")
            return ActivationVerificationReport(
                result=ActivationVerificationResult.REJECTED,
                reasons=reasons,
                fee_atoms=fee_atoms,
                tx_hash=receipt.get("transactionHash"),
            )

        # amount: exact match, no tolerance
        if activated["amount"] != intent.amount:
            reasons.append(
                f"Activated.amount {activated['amount']} != intent.amount {intent.amount}"
            )

        # sender: mandatory compare
        if not activated.get("from"):
            reasons.append("Activated.from missing")
        elif activated["from"].lower() != expected_sender.lower():
            reasons.append(
                f"Activated.from {activated['from']} != expected_sender {expected_sender}"
            )

        # activationId must be present (uint, including 0)
        if activated.get("activation_id") is None:
            reasons.append("Activated.activationId missing")

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
