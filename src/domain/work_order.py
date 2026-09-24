import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from src.domain.enums import CurrencyAsset, DeliverableStatus, WorkOrderStatus


def canonical_json_hash(data: Any) -> str:
    """Computes deterministic SHA-256 of data in canonical JSON representation."""
    serialized = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass
class WorkOrder:
    work_order_id: str
    tenant_id: str
    organisation_id: str
    client_id: str
    title: str
    description: str
    deliverable_type: str
    required_orbio_credits: int
    bounty_amount: int
    bounty_asset: CurrencyAsset = CurrencyAsset.USDG
    deadline_seconds: int = 3600
    status: WorkOrderStatus = WorkOrderStatus.PROPOSED
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def transition_to(self, new_status: WorkOrderStatus) -> None:
        self.status = new_status


@dataclass
class WorkDeliverable:
    deliverable_id: str
    work_order_id: str
    producer_agent_id: str
    content_payload: Dict[str, Any]
    content_hash: str
    orbio_credits_consumed: int
    execution_telemetry: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def create(
        cls,
        work_order_id: str,
        producer_agent_id: str,
        content_payload: Dict[str, Any],
        orbio_credits_consumed: int,
        execution_telemetry: Optional[Dict[str, Any]] = None,
        deliverable_id: Optional[str] = None,
    ) -> "WorkDeliverable":
        d_id = deliverable_id or f"deliv-{uuid.uuid4()}"
        telemetry = dict(execution_telemetry or {})
        # Cryptographic and operational invariant: simulated execution must never claim LIVE provenance
        if telemetry.get("is_simulated") is True:
            prov = str(telemetry.get("provenance", "")).upper()
            if prov in {"LIVE", "LIVE_ORBIO"}:
                raise ValueError("Provenance conflict: Simulated execution cannot be tagged as LIVE or LIVE_ORBIO.")
        c_hash = canonical_json_hash(content_payload)
        return cls(
            deliverable_id=d_id,
            work_order_id=work_order_id,
            producer_agent_id=producer_agent_id,
            content_payload=content_payload,
            content_hash=c_hash,
            orbio_credits_consumed=orbio_credits_consumed,
            execution_telemetry=telemetry,
        )

    def compute_evidence_hash(self) -> str:
        evidence_data = {
            "deliverable_id": self.deliverable_id,
            "work_order_id": self.work_order_id,
            "producer_agent_id": self.producer_agent_id,
            "orbio_credits_consumed": self.orbio_credits_consumed,
            "telemetry": self.execution_telemetry,
        }
        return canonical_json_hash(evidence_data)


@dataclass
class WorkDeliverableReceipt:
    receipt_id: str
    work_order_id: str
    deliverable_id: str
    content_hash: str
    evidence_hash: str
    verifier_identity: str
    status: DeliverableStatus
    verification_notes: str = ""
    verified_at: datetime = field(default_factory=datetime.utcnow)
    hmac_signature: Optional[str] = None

    def is_verified(self) -> bool:
        return self.status == DeliverableStatus.ACCEPTED

    @classmethod
    def sign_receipt(
        cls,
        receipt_id: str,
        work_order_id: str,
        deliverable_id: str,
        content_hash: str,
        evidence_hash: str,
        verifier_identity: str,
        status: DeliverableStatus,
        secret_key: str,
        verification_notes: str = "",
    ) -> "WorkDeliverableReceipt":
        msg = f"{work_order_id}:{deliverable_id}:{content_hash}:{evidence_hash}:{status.value}:{verifier_identity}"
        sig = hmac.new(secret_key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
        return cls(
            receipt_id=receipt_id,
            work_order_id=work_order_id,
            deliverable_id=deliverable_id,
            content_hash=content_hash,
            evidence_hash=evidence_hash,
            verifier_identity=verifier_identity,
            status=status,
            verification_notes=verification_notes,
            hmac_signature=sig,
        )

    def verify_hmac(self, secret_key: str) -> bool:
        if not self.hmac_signature:
            return False
        msg = f"{self.work_order_id}:{self.deliverable_id}:{self.content_hash}:{self.evidence_hash}:{self.status.value}:{self.verifier_identity}"
        expected = hmac.new(secret_key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(self.hmac_signature, expected)


@dataclass
class RevenueEvent:
    revenue_event_id: str
    work_order_id: str
    gross_revenue_usdg: int
    direct_expense_usdg: int
    net_surplus_usdg: int
    orbio_credits_consumed: int
    allocated_to_mission_budget: int
    allocated_to_reserve: int
    settled_at: datetime = field(default_factory=datetime.utcnow)
    ledger_tx_id: Optional[str] = None
