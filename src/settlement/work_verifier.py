import uuid
from typing import Optional

from src.domain.enums import DeliverableStatus
from src.domain.work_order import (
    WorkDeliverable,
    WorkDeliverableReceipt,
    WorkOrder,
    canonical_json_hash,
)


class WorkDeliverableVerifier:
    """
    Independent verification authority for WorkDeliverables.
    
    Invariants:
    1. Verifies that content_hash precisely matches SHA-256 of canonical JSON payload.
    2. Validates schema and deliverable type match the WorkOrder.
    3. Enforces that reported compute consumption (orbio_credits_consumed) is positive
       and bounded by the WorkOrder requirement.
    4. Issues HMAC-signed WorkDeliverableReceipt bound to content_hash and evidence_hash.
    """

    def __init__(
        self,
        verifier_identity: str = "DeterministicClientVerifier",
        secret_key: str = "kalyx-verifier-default-secret-key",
    ):
        self.verifier_identity = verifier_identity
        self.secret_key = secret_key

    def verify(
        self,
        work_order: WorkOrder,
        deliverable: WorkDeliverable,
        receipt_id: Optional[str] = None,
    ) -> WorkDeliverableReceipt:
        r_id = receipt_id or f"rcpt-{uuid.uuid4()}"
        evidence_hash = deliverable.compute_evidence_hash()

        # Check 1: Work Order ID binding
        if deliverable.work_order_id != work_order.work_order_id:
            return WorkDeliverableReceipt.sign_receipt(
                receipt_id=r_id,
                work_order_id=work_order.work_order_id,
                deliverable_id=deliverable.deliverable_id,
                content_hash=deliverable.content_hash,
                evidence_hash=evidence_hash,
                verifier_identity=self.verifier_identity,
                status=DeliverableStatus.REJECTED,
                secret_key=self.secret_key,
                verification_notes=f"Work order ID mismatch: {deliverable.work_order_id} != {work_order.work_order_id}",
            )

        # Check 2: Content Hash Integrity (tamper detection)
        expected_content_hash = canonical_json_hash(deliverable.content_payload)
        if deliverable.content_hash != expected_content_hash:
            return WorkDeliverableReceipt.sign_receipt(
                receipt_id=r_id,
                work_order_id=work_order.work_order_id,
                deliverable_id=deliverable.deliverable_id,
                content_hash=deliverable.content_hash,
                evidence_hash=evidence_hash,
                verifier_identity=self.verifier_identity,
                status=DeliverableStatus.REJECTED,
                secret_key=self.secret_key,
                verification_notes=f"Content hash corruption: expected {expected_content_hash} but deliverable claims {deliverable.content_hash}",
            )

        # Check 3: Resource Consumption Bounds
        if deliverable.orbio_credits_consumed <= 0:
            return WorkDeliverableReceipt.sign_receipt(
                receipt_id=r_id,
                work_order_id=work_order.work_order_id,
                deliverable_id=deliverable.deliverable_id,
                content_hash=deliverable.content_hash,
                evidence_hash=evidence_hash,
                verifier_identity=self.verifier_identity,
                status=DeliverableStatus.REJECTED,
                secret_key=self.secret_key,
                verification_notes="Deliverable reports zero or negative compute credits consumed",
            )

        if deliverable.orbio_credits_consumed > work_order.required_orbio_credits:
            return WorkDeliverableReceipt.sign_receipt(
                receipt_id=r_id,
                work_order_id=work_order.work_order_id,
                deliverable_id=deliverable.deliverable_id,
                content_hash=deliverable.content_hash,
                evidence_hash=evidence_hash,
                verifier_identity=self.verifier_identity,
                status=DeliverableStatus.REJECTED,
                secret_key=self.secret_key,
                verification_notes=f"Excessive compute consumed: {deliverable.orbio_credits_consumed} > {work_order.required_orbio_credits}",
            )

        # Check 4: Payload Completeness & Deliverable Type
        payload = deliverable.content_payload
        if not isinstance(payload, dict) or not payload:
            return WorkDeliverableReceipt.sign_receipt(
                receipt_id=r_id,
                work_order_id=work_order.work_order_id,
                deliverable_id=deliverable.deliverable_id,
                content_hash=deliverable.content_hash,
                evidence_hash=evidence_hash,
                verifier_identity=self.verifier_identity,
                status=DeliverableStatus.REJECTED,
                secret_key=self.secret_key,
                verification_notes="Deliverable payload is empty or invalid format",
            )

        if payload.get("deliverable_type") != work_order.deliverable_type:
            return WorkDeliverableReceipt.sign_receipt(
                receipt_id=r_id,
                work_order_id=work_order.work_order_id,
                deliverable_id=deliverable.deliverable_id,
                content_hash=deliverable.content_hash,
                evidence_hash=evidence_hash,
                verifier_identity=self.verifier_identity,
                status=DeliverableStatus.REJECTED,
                secret_key=self.secret_key,
                verification_notes=f"Deliverable type mismatch in payload: expected {work_order.deliverable_type}, got {payload.get('deliverable_type')}",
            )

        # All checks passed: Issue signed ACCEPTED receipt
        return WorkDeliverableReceipt.sign_receipt(
            receipt_id=r_id,
            work_order_id=work_order.work_order_id,
            deliverable_id=deliverable.deliverable_id,
            content_hash=deliverable.content_hash,
            evidence_hash=evidence_hash,
            verifier_identity=self.verifier_identity,
            status=DeliverableStatus.ACCEPTED,
            secret_key=self.secret_key,
            verification_notes="Deliverable verified: content hash matches, schema valid, resource bounds respected",
        )
