"""Unit tests for Phase 17 B2B Marketplace & Escrow."""

import pytest
from src.domain.marketplace import MarketplaceOrder, MarketplaceOrderStatus, EscrowAgreement, EscrowStatus
from src.persistence.database import Database
from src.persistence.marketplace_repository import MarketplaceRepository


def _init_test_db():
    db = Database(":memory:")
    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-alpha", "tenant-demo", "Alpha Mission", 100, "PLANNING"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-beta", "tenant-demo", "Beta Mission", 100, "PLANNING"),
        )
    return db


def test_marketplace_order_creation():
    order = MarketplaceOrder.create(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        order_id="mkt-1",
        title="Security Audit",
        description="Verify smart contracts",
        required_capability="ADVANCED_ANALYTICS",
        bounty_amount=250,
        bounty_asset="USDG",
    )
    assert order.order_id == "mkt-1"
    assert order.status == MarketplaceOrderStatus.OPEN
    assert order.bounty_amount == 250
    assert len(order.specification_hash) == 64


def test_marketplace_order_invalid_bounty():
    with pytest.raises(ValueError, match="bounty_amount must be positive"):
        MarketplaceOrder.create(
            tenant_id="tenant-demo",
            organisation_id="org-alpha",
            order_id="mkt-err",
            title="Bad Order",
            description="Negative bounty",
            required_capability="BASIC",
            bounty_amount=-50,
        )


def test_marketplace_repository_crud():
    db = _init_test_db()
    repo = MarketplaceRepository(db)

    order = MarketplaceOrder.create(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        order_id="mkt-crud-1",
        title="Data Analysis",
        description="Analyze telemetry",
        required_capability="DATA_ANALYSIS",
        bounty_amount=100,
    )
    repo.create_order(order)

    fetched = repo.get_order("tenant-demo", "org-alpha", "mkt-crud-1")
    assert fetched is not None
    assert fetched.title == "Data Analysis"
    assert fetched.status == MarketplaceOrderStatus.OPEN

    # List public orders
    public_orders = repo.list_public_orders(status="OPEN")
    assert any(o.order_id == "mkt-crud-1" for o in public_orders)

    # Claim order
    claimed = repo.claim_order(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        order_id="mkt-crud-1",
        claimed_by_tenant_id="tenant-demo",
        claimed_by_org_id="org-beta",
        claimed_by_agent_id="agent-worker-1",
        work_order_id="wo-101",
    )
    assert claimed is True

    # Attempt second claim should fail (already claimed)
    claimed_again = repo.claim_order(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        order_id="mkt-crud-1",
        claimed_by_tenant_id="tenant-demo",
        claimed_by_org_id="org-beta",
        claimed_by_agent_id="agent-worker-2",
    )
    assert claimed_again is False

    # Check updated status
    updated = repo.get_order("tenant-demo", "org-alpha", "mkt-crud-1")
    assert updated.status == MarketplaceOrderStatus.CLAIMED
    assert updated.claimed_by_org_id == "org-beta"


def test_escrow_repository_lifecycle():
    db = _init_test_db()
    repo = MarketplaceRepository(db)

    escrow = EscrowAgreement(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        escrow_id="escrow-1",
        order_id="mkt-crud-1",
        client_tenant_id="tenant-demo",
        client_org_id="org-alpha",
        bounty_amount=200,
        status=EscrowStatus.HELD,
        client_ledger_tx_id="tx-lock-1",
    )
    repo.save_escrow(escrow)

    fetched = repo.get_escrow("tenant-demo", "org-alpha", "escrow-1")
    assert fetched is not None
    assert fetched.status == EscrowStatus.HELD
    assert fetched.bounty_amount == 200

    # Release escrow
    repo.update_escrow_status(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        escrow_id="escrow-1",
        status=EscrowStatus.RELEASED,
        provider_tenant_id="tenant-demo",
        provider_org_id="org-beta",
        provider_ledger_tx_id="tx-rev-1",
    )

    updated = repo.get_escrow("tenant-demo", "org-alpha", "escrow-1")
    assert updated.status == EscrowStatus.RELEASED
    assert updated.provider_org_id == "org-beta"
    assert updated.released_at is not None
