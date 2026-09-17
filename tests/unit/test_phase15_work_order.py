import uuid
import pytest
from src.domain.enums import CurrencyAsset, DeliverableStatus, WorkOrderStatus
from src.domain.work_order import (
    WorkOrder,
    WorkDeliverable,
    WorkDeliverableReceipt,
    RevenueEvent,
    canonical_json_hash,
)

def test_work_order_creation_and_transition():
    wo = WorkOrder(
        work_order_id="wo-101",
        tenant_id="tenant-1",
        organisation_id="org-1",
        client_id="client-dao",
        title="Smart Contract Security Audit",
        description="Verify reentrancy and access control",
        deliverable_type="SECURITY_AUDIT",
        required_orbio_credits=10,
        bounty_amount=50,
        bounty_asset=CurrencyAsset.USDG,
    )
    assert wo.status == WorkOrderStatus.PROPOSED
    assert wo.bounty_asset == CurrencyAsset.USDG
    
    wo.transition_to(WorkOrderStatus.AUTHORIZED)
    assert wo.status == WorkOrderStatus.AUTHORIZED
    wo.transition_to(WorkOrderStatus.IN_PROGRESS)
    assert wo.status == WorkOrderStatus.IN_PROGRESS

def test_work_deliverable_hash_determinism():
    payload = {"vulnerabilities_found": 0, "status": "SECURE", "coverage": 0.98}
    d1 = WorkDeliverable.create(
        work_order_id="wo-101",
        producer_agent_id="agent-eng",
        content_payload=payload,
        orbio_credits_consumed=10,
        execution_telemetry={"latency_ms": 120},
    )
    d2 = WorkDeliverable.create(
        work_order_id="wo-101",
        producer_agent_id="agent-eng",
        content_payload=payload,
        orbio_credits_consumed=10,
        execution_telemetry={"latency_ms": 120},
    )
    # Content hashes must be identical regardless of deliverable ID
    assert d1.content_hash == d2.content_hash
    assert d1.content_hash == canonical_json_hash(payload)

def test_work_deliverable_receipt_hmac_signing_and_verification():
    secret_key = "verifier-secret-key-1234"
    payload = {"report": "All clean"}
    d = WorkDeliverable.create(
        work_order_id="wo-101",
        producer_agent_id="agent-eng",
        content_payload=payload,
        orbio_credits_consumed=10,
    )
    evidence_hash = d.compute_evidence_hash()
    
    receipt = WorkDeliverableReceipt.sign_receipt(
        receipt_id=f"rcpt-{uuid.uuid4()}",
        work_order_id=d.work_order_id,
        deliverable_id=d.deliverable_id,
        content_hash=d.content_hash,
        evidence_hash=evidence_hash,
        verifier_identity="VerifierProtocolAlpha",
        status=DeliverableStatus.ACCEPTED,
        secret_key=secret_key,
        verification_notes="Criteria satisfied",
    )
    
    assert receipt.is_verified() is True
    assert receipt.verify_hmac(secret_key) is True
    assert receipt.verify_hmac("wrong-secret") is False
    
    # Tampering with content hash must fail verification
    receipt.content_hash = "tampered-hash"
    assert receipt.verify_hmac(secret_key) is False

def test_revenue_event_creation():
    rev = RevenueEvent(
        revenue_event_id="rev-1",
        work_order_id="wo-101",
        gross_revenue_usdg=50,
        direct_expense_usdg=10,
        net_surplus_usdg=40,
        orbio_credits_consumed=10,
        allocated_to_mission_budget=32,
        allocated_to_reserve=8,
    )
    assert rev.net_surplus_usdg == rev.gross_revenue_usdg - rev.direct_expense_usdg
    assert rev.allocated_to_mission_budget + rev.allocated_to_reserve == rev.net_surplus_usdg
