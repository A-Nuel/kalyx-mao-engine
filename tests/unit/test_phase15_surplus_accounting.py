import uuid
import pytest
from src.domain.enums import CurrencyAsset, DeliverableStatus, WorkOrderStatus
from src.domain.work_order import WorkOrder, WorkDeliverableReceipt
from src.economy.ledger import DoubleEntryLedger, REVENUE, TREASURY, SURPLUS_RESERVE, ESCROW, EXTERNAL_SINK
from src.economy.surplus_accounting import SurplusReconciler

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
    return WorkDeliverableReceipt(
        receipt_id=f"rcpt-{uuid.uuid4()}",
        work_order_id=wo_id,
        deliverable_id=f"deliv-{uuid.uuid4()}",
        content_hash="mock-content-hash",
        evidence_hash="mock-evidence-hash",
        verifier_identity="AuditorAlpha",
        status=DeliverableStatus.ACCEPTED,
        verification_notes="Passed all checks",
    )

def test_surplus_reconciliation_happy_path():
    ledger = DoubleEntryLedger(initial_treasury=100)
    reconciler = SurplusReconciler(ledger, default_reserve_ratio=0.20)
    wo = create_sample_work_order(bounty=50)
    receipt = create_verified_receipt("wo-101")
    
    # 1. Simulate Phase 14B purchase: Treasury spends 10 USDG
    ledger.transfer(TREASURY, ESCROW, 10, "Escrow for Orbio purchase")
    ledger.transfer(ESCROW, EXTERNAL_SINK, 10, "Purchase settlement")
    assert ledger.get_balance(TREASURY) == 90
    
    # 2. Client deposits 50 USDG bounty into REVENUE
    # (Simulated mint to client/revenue or transfer from an external client account)
    ledger._mint(REVENUE, 50, "Client payment received")
    assert ledger.get_balance(REVENUE) == 50
    
    # 3. Reconcile
    event = reconciler.reconcile_surplus(
        work_order=wo,
        receipt=receipt,
        gross_revenue_usdg=50,
        direct_expense_usdg=10,
        orbio_credits_consumed=10,
    )
    
    assert event.gross_revenue_usdg == 50
    assert event.direct_expense_usdg == 10
    assert event.net_surplus_usdg == 40
    assert event.orbio_credits_consumed == 10
    assert event.allocated_to_reserve == 8      # 20% of 40
    assert event.allocated_to_mission_budget == 32 # 80% of 40
    
    # Check ledger state
    assert ledger.get_balance(REVENUE) == 0
    assert ledger.get_balance(SURPLUS_RESERVE) == 8
    # Treasury was 90. Reimbursed 10 + allocated 32 = 132
    assert ledger.get_balance(TREASURY) == 132
    
    # Conservation invariant must strictly hold
    assert ledger.verify_conservation() is True

def test_surplus_reconciliation_loss_scenario():
    ledger = DoubleEntryLedger(initial_treasury=100)
    reconciler = SurplusReconciler(ledger, default_reserve_ratio=0.20)
    wo = create_sample_work_order(bounty=5)
    receipt = create_verified_receipt("wo-101")
    
    # Spent 10 USDG
    ledger.transfer(TREASURY, ESCROW, 10, "Escrow")
    ledger.transfer(ESCROW, EXTERNAL_SINK, 10, "Settled")
    assert ledger.get_balance(TREASURY) == 90
    
    # Revenue only 5 USDG
    ledger._mint(REVENUE, 5, "Partial revenue")
    
    event = reconciler.reconcile_surplus(
        work_order=wo,
        receipt=receipt,
        gross_revenue_usdg=5,
        direct_expense_usdg=10,
        orbio_credits_consumed=10,
    )
    
    assert event.net_surplus_usdg == 0
    assert event.allocated_to_reserve == 0
    assert event.allocated_to_mission_budget == 0
    
    # All 5 USDG goes to reimburse Treasury (Treasury is 95)
    assert ledger.get_balance(REVENUE) == 0
    assert ledger.get_balance(SURPLUS_RESERVE) == 0
    assert ledger.get_balance(TREASURY) == 95
    assert ledger.verify_conservation() is True

def test_reconciliation_unverified_receipt_fails():
    ledger = DoubleEntryLedger(initial_treasury=100)
    reconciler = SurplusReconciler(ledger)
    wo = create_sample_work_order()
    receipt = WorkDeliverableReceipt(
        receipt_id="r-bad",
        work_order_id="wo-101",
        deliverable_id="d-1",
        content_hash="h1",
        evidence_hash="h2",
        verifier_identity="AuditorAlpha",
        status=DeliverableStatus.REJECTED,
    )
    
    ledger._mint(REVENUE, 50, "Deposit")
    with pytest.raises(ValueError, match="Cannot reconcile unverified deliverable receipt"):
        reconciler.reconcile_surplus(
            work_order=wo,
            receipt=receipt,
            gross_revenue_usdg=50,
            direct_expense_usdg=10,
            orbio_credits_consumed=10,
        )
