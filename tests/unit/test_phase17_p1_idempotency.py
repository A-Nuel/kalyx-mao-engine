"""Milestone 2: Replay protection and 3-way canonical hash idempotency tests."""

import pytest
from src.domain.exceptions import IdempotencyConflict
from src.domain.marketplace import EscrowAgreement, EscrowStatus, MarketplaceOrder
from src.domain.work_order import RevenueEvent, WorkOrder, WorkDeliverableReceipt
from src.domain.enums import CurrencyAsset, DeliverableStatus, WorkOrderStatus
from src.economy.ledger import DoubleEntryLedger
from src.economy.surplus_accounting import SurplusReconciler
from src.persistence.database import Database
from src.persistence.marketplace_repository import MarketplaceRepository
from src.persistence.work_order_repository import WorkOrderRepository


@pytest.fixture
def test_env():
    db = Database(":memory:")
    with db.conn:
        db.conn.execute("INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES ('t1', 'Tenant 1', 'active', datetime('now'))")
        db.conn.execute("INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES ('org1', 't1', 'Mission', 1000, 'PLANNING', datetime('now'))")
    
    mkt_repo = MarketplaceRepository(db)
    wo_repo = WorkOrderRepository(db)
    ledger = DoubleEntryLedger(initial_treasury=1000)
    secret = "test-secret-key-for-idempotency"
    reconciler = SurplusReconciler(ledger=ledger, receipt_secret_key=secret, work_order_repo=wo_repo)
    
    return {
        "db": db,
        "mkt_repo": mkt_repo,
        "wo_repo": wo_repo,
        "ledger": ledger,
        "reconciler": reconciler,
        "secret": secret,
    }


def test_order_creation_idempotency_three_states(test_env):
    """Proves NEW -> DUPLICATE -> CONFLICT 3-state canonical hash idempotency."""
    repo = test_env["mkt_repo"]
    order1 = MarketplaceOrder.create(
        tenant_id="t1",
        organisation_id="org1",
        order_id="order-idemp-1",
        title="Audit Contract",
        description="Verify smart contract invariants",
        required_capability="SECURITY_AUDIT",
        bounty_amount=200,
    )
    # 1. State NEW
    created = repo.create_order(order1)
    assert created.order_id == "order-idemp-1"

    # 2. State DUPLICATE (identical payload)
    duplicate_order = MarketplaceOrder.create(
        tenant_id="t1",
        organisation_id="org1",
        order_id="order-idemp-1",
        title="Audit Contract",
        description="Verify smart contract invariants",
        required_capability="SECURITY_AUDIT",
        bounty_amount=200,
    )
    dup_res = repo.create_order(duplicate_order)
    assert dup_res.order_id == "order-idemp-1"
    assert dup_res.specification_hash == order1.specification_hash

    # 3. State CONFLICT (same order_id, different bounty/content)
    conflicting_order = MarketplaceOrder.create(
        tenant_id="t1",
        organisation_id="org1",
        order_id="order-idemp-1",
        title="Attacker Modified Contract",
        description="Drain funds maliciously",
        required_capability="SECURITY_AUDIT",
        bounty_amount=9999,
    )
    with pytest.raises(IdempotencyConflict) as exc:
        repo.create_order(conflicting_order)
    assert "conflicting specification hash" in str(exc.value)


def test_escrow_creation_idempotency_three_states(test_env):
    """Proves EscrowAgreement persistence distinguishes matching vs conflicting terms."""
    repo = test_env["mkt_repo"]
    escrow = EscrowAgreement(
        tenant_id="t1",
        organisation_id="org1",
        escrow_id="esc-100",
        order_id="order-100",
        client_tenant_id="t1",
        client_org_id="org1",
        bounty_amount=150,
    )
    # 1. First insert
    saved = repo.save_escrow(escrow)
    assert saved.escrow_id == "esc-100"

    # 2. Duplicate with identical terms
    dup_saved = repo.save_escrow(escrow)
    assert dup_saved.escrow_id == "esc-100"

    # 3. Conflicting replay with tampered bounty
    tampered_escrow = EscrowAgreement(
        tenant_id="t1",
        organisation_id="org1",
        escrow_id="esc-100",
        order_id="order-100",
        client_tenant_id="t1",
        client_org_id="org1",
        bounty_amount=5000,
    )
    with pytest.raises(IdempotencyConflict) as exc:
        repo.save_escrow(tampered_escrow)
    assert "conflicting parameters" in str(exc.value)


def test_surplus_reconciliation_durable_idempotency_rejection(test_env):
    """Surplus reconciliation is protected by both in-memory and durable database checks."""
    reconciler = test_env["reconciler"]
    ledger = test_env["ledger"]
    wo_repo = test_env["wo_repo"]
    secret = test_env["secret"]

    wo = WorkOrder(
        tenant_id="t1",
        organisation_id="org1",
        work_order_id="wo-durable-1",
        client_id="client-org",
        title="Test Work Order",
        description="Deliver work",
        deliverable_type="DOCS",
        required_orbio_credits=10,
        bounty_amount=100,
        bounty_asset=CurrencyAsset.USDG,
        deadline_seconds=3600,
        status=WorkOrderStatus.VERIFIED,
    )

    receipt = WorkDeliverableReceipt.sign_receipt(
        receipt_id="rec-durable-1",
        work_order_id="wo-durable-1",
        deliverable_id="deliv-1",
        content_hash="hash-1234",
        evidence_hash="ev-1234",
        verifier_identity="independent-auditor",
        status=DeliverableStatus.ACCEPTED,
        secret_key=secret,
    )

    ledger.deposit_revenue(100, "Deposit revenue before reconciliation")

    # 1. First reconciliation succeeds
    rev_event = reconciler.reconcile_surplus(
        work_order=wo,
        receipt=receipt,
        gross_revenue_usdg=100,
        direct_expense_usdg=0,
        orbio_credits_consumed=10,
    )
    assert rev_event.net_surplus_usdg == 100

    # Save to durable repository
    wo_repo.save_revenue_event("t1", "org1", rev_event)

    # 2. In-memory duplicate check catches re-run on same reconciler instance
    with pytest.raises(ValueError) as exc1:
        reconciler.reconcile_surplus(
            work_order=wo,
            receipt=receipt,
            gross_revenue_usdg=100,
            direct_expense_usdg=0,
            orbio_credits_consumed=10,
        )
    assert "already been reconciled" in str(exc1.value)

    # 3. Simulate process restart: Fresh reconciler instance (empty in-memory cache)
    fresh_reconciler = SurplusReconciler(ledger=ledger, receipt_secret_key=secret, work_order_repo=wo_repo)
    with pytest.raises(ValueError) as exc2:
        fresh_reconciler.reconcile_surplus(
            work_order=wo,
            receipt=receipt,
            gross_revenue_usdg=100,
            direct_expense_usdg=0,
            orbio_credits_consumed=10,
        )
    assert "reconciled in durable storage" in str(exc2.value)
