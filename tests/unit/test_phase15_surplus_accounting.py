import uuid
import pytest
from src.domain.enums import CurrencyAsset, DeliverableStatus
from src.domain.work_order import WorkOrder, WorkDeliverableReceipt
from src.economy.ledger import DoubleEntryLedger, REVENUE, TREASURY, SURPLUS_RESERVE, ESCROW, EXTERNAL_SINK
from src.economy.surplus_accounting import SurplusReconciler

RECEIPT_SECRET = "test-surplus-receipt-secret"


def create_sample_work_order(bounty=50):
    return WorkOrder(
        work_order_id="wo-101",
        tenant_id="tenant-1",
        organisation_id="org-1",
        client_id="client-dao",
        title="Code Audit",
        description="Verify correctness",
        deliverable_type="SECURITY_AUDIT",
        required_orbio_credits=10,
        bounty_amount=bounty,
        bounty_asset=CurrencyAsset.USDG,
    )


def create_verified_receipt(wo_id="wo-101"):
    return WorkDeliverableReceipt.sign_receipt(
        receipt_id=f"rcpt-{uuid.uuid4()}",
        work_order_id=wo_id,
        deliverable_id=f"deliv-{uuid.uuid4()}",
        content_hash="mock-content-hash",
        evidence_hash="mock-evidence-hash",
        verifier_identity="AuditorAlpha",
        status=DeliverableStatus.ACCEPTED,
        secret_key=RECEIPT_SECRET,
        verification_notes="Passed all checks",
    )


def test_surplus_reconciliation_happy_path():
    ledger = DoubleEntryLedger(initial_treasury=100)
    reconciler = SurplusReconciler(ledger, default_reserve_ratio=0.20, receipt_secret_key=RECEIPT_SECRET)
    wo = create_sample_work_order(bounty=50)
    receipt = create_verified_receipt("wo-101")
    ledger.transfer(TREASURY, ESCROW, 10, "Escrow for Orbio purchase")
    ledger.transfer(ESCROW, EXTERNAL_SINK, 10, "Purchase settlement")
    assert ledger.get_balance(TREASURY) == 90
    ledger._mint(REVENUE, 50, "Client payment received")
    event = reconciler.reconcile_surplus(
        work_order=wo, receipt=receipt, gross_revenue_usdg=50,
        direct_expense_usdg=10, orbio_credits_consumed=10,
    )
    assert event.gross_revenue_usdg == 50
    assert event.direct_expense_usdg == 10
    assert event.net_surplus_usdg == 40
    assert event.orbio_credits_consumed == 10
    assert event.allocated_to_reserve == 8
    assert event.allocated_to_mission_budget == 32
    assert ledger.get_balance(REVENUE) == 0
    assert ledger.get_balance(SURPLUS_RESERVE) == 8
    assert ledger.get_balance(TREASURY) == 132
    assert ledger.verify_conservation() is True


def test_surplus_reconciliation_loss_scenario():
    ledger = DoubleEntryLedger(initial_treasury=100)
    reconciler = SurplusReconciler(ledger, default_reserve_ratio=0.20, receipt_secret_key=RECEIPT_SECRET)
    wo = create_sample_work_order(bounty=5)
    receipt = create_verified_receipt("wo-101")
    ledger.transfer(TREASURY, ESCROW, 10, "Escrow")
    ledger.transfer(ESCROW, EXTERNAL_SINK, 10, "Settled")
    ledger._mint(REVENUE, 5, "Partial revenue")
    event = reconciler.reconcile_surplus(
        work_order=wo, receipt=receipt, gross_revenue_usdg=5,
        direct_expense_usdg=10, orbio_credits_consumed=10,
    )
    assert event.net_surplus_usdg == 0
    assert event.allocated_to_reserve == 0
    assert event.allocated_to_mission_budget == 0
    assert ledger.get_balance(REVENUE) == 0
    assert ledger.get_balance(SURPLUS_RESERVE) == 0
    assert ledger.get_balance(TREASURY) == 95
    assert ledger.verify_conservation() is True


def test_reconciliation_unverified_receipt_fails():
    ledger = DoubleEntryLedger(initial_treasury=100)
    reconciler = SurplusReconciler(ledger, receipt_secret_key=RECEIPT_SECRET)
    wo = create_sample_work_order()
    receipt = WorkDeliverableReceipt(
        receipt_id="r-bad", work_order_id="wo-101", deliverable_id="d-1",
        content_hash="h1", evidence_hash="h2", verifier_identity="AuditorAlpha",
        status=DeliverableStatus.REJECTED,
    )
    ledger._mint(REVENUE, 50, "Deposit")
    with pytest.raises(ValueError, match="Cannot reconcile unverified deliverable receipt"):
        reconciler.reconcile_surplus(
            work_order=wo, receipt=receipt, gross_revenue_usdg=50,
            direct_expense_usdg=10, orbio_credits_consumed=10,
        )


def test_reconciliation_invalid_signature_fails_closed():
    ledger = DoubleEntryLedger(initial_treasury=100)
    reconciler = SurplusReconciler(ledger, receipt_secret_key=RECEIPT_SECRET)
    wo = create_sample_work_order()
    receipt = create_verified_receipt()
    receipt.hmac_signature = "invalid-signature"
    ledger._mint(REVENUE, 50, "Deposit")
    with pytest.raises(ValueError, match="invalid HMAC signature"):
        reconciler.reconcile_surplus(
            work_order=wo, receipt=receipt, gross_revenue_usdg=50,
            direct_expense_usdg=10, orbio_credits_consumed=10,
        )
    assert ledger.get_balance(REVENUE) == 50
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.verify_conservation() is True


def test_reconciler_requires_receipt_secret():
    ledger = DoubleEntryLedger(initial_treasury=100)
    with pytest.raises(ValueError, match="receipt_secret_key is required"):
        SurplusReconciler(ledger)
