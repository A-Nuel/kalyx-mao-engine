"""Unit tests for Phase 17 B2B Marketplace & Escrow."""

import pytest
from src.domain.marketplace import MarketplaceOrder, MarketplaceOrderStatus, EscrowAgreement, EscrowStatus
from src.persistence.database import Database
from src.persistence.marketplace_repository import MarketplaceRepository
from src.domain.work_order import WorkDeliverable


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


def test_provider_scoped_escrow_lookup_resolves_claimed_client_escrow():
    db = _init_test_db()
    repo = MarketplaceRepository(db)

    order = MarketplaceOrder.create(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        order_id="mkt-provider-scope",
        title="Provider settlement",
        description="Provider must resolve client-owned escrow",
        required_capability="ADVANCED_ANALYTICS",
        bounty_amount=200,
    )
    repo.create_order(order)
    escrow = EscrowAgreement(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        escrow_id="escrow-provider-scope",
        order_id=order.order_id,
        client_tenant_id="tenant-demo",
        client_org_id="org-alpha",
        bounty_amount=200,
        status=EscrowStatus.HELD,
    )
    repo.save_escrow(escrow)
    assert repo.claim_order(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        order_id=order.order_id,
        claimed_by_tenant_id="tenant-demo",
        claimed_by_org_id="org-beta",
        claimed_by_agent_id="agent-beta",
        work_order_id="wo-provider-scope",
    )

    resolved = repo.get_escrow_by_order(order.order_id, "tenant-demo", "org-beta")
    assert resolved is not None
    assert resolved.escrow_id == "escrow-provider-scope"
    assert resolved.client_org_id == "org-alpha"


def test_provider_scoped_escrow_lookup_rejects_unrelated_provider():
    db = _init_test_db()
    repo = MarketplaceRepository(db)

    order = MarketplaceOrder.create(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        order_id="mkt-provider-negative",
        title="Provider settlement",
        description="Unrelated provider must not resolve escrow",
        required_capability="ADVANCED_ANALYTICS",
        bounty_amount=200,
    )
    repo.create_order(order)
    repo.save_escrow(EscrowAgreement(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        escrow_id="escrow-provider-negative",
        order_id=order.order_id,
        client_tenant_id="tenant-demo",
        client_org_id="org-alpha",
        bounty_amount=200,
        status=EscrowStatus.HELD,
    ))

    assert repo.get_escrow_by_order(order.order_id, "tenant-demo", "org-beta") is None


def test_public_projection_reads_completed_provenance_from_provider_scope():
    db = _init_test_db()
    repo = MarketplaceRepository(db)

    order = MarketplaceOrder.create(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        order_id="mkt-provenance",
        title="Provenance test",
        description="Completed provider execution",
        required_capability="ADVANCED_ANALYTICS",
        bounty_amount=200,
    )
    repo.create_order(order)
    repo.save_escrow(EscrowAgreement(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        escrow_id="escrow-provenance",
        order_id=order.order_id,
        client_tenant_id="tenant-demo",
        client_org_id="org-alpha",
        bounty_amount=200,
        status=EscrowStatus.HELD,
    ))
    assert repo.claim_order(
        tenant_id="tenant-demo",
        organisation_id="org-alpha",
        order_id=order.order_id,
        claimed_by_tenant_id="tenant-demo",
        claimed_by_org_id="org-beta",
        claimed_by_agent_id="agent-beta",
        work_order_id="wo-provenance",
    )

    deliverable = WorkDeliverable.create(
        work_order_id="wo-provenance",
        producer_agent_id="agent-beta",
        content_payload={"result": "verified"},
        orbio_credits_consumed=10,
        execution_telemetry={"provenance": "SIMULATED", "is_simulated": True},
        deliverable_id="deliv-provenance",
    )
    db.conn.execute(
        """
        INSERT INTO work_deliverables (
            tenant_id, organisation_id, deliverable_id, work_order_id,
            producer_agent_id, content_payload_json, content_hash,
            orbio_credits_consumed, execution_telemetry_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "tenant-demo", "org-beta", deliverable.deliverable_id,
            deliverable.work_order_id, deliverable.producer_agent_id,
            __import__("json").dumps(deliverable.content_payload),
            deliverable.content_hash, deliverable.orbio_credits_consumed,
            __import__("json").dumps(deliverable.execution_telemetry),
            deliverable.created_at.isoformat(),
        ),
    )
    repo.update_order_status(
        "tenant-demo", "org-alpha", order.order_id,
        MarketplaceOrderStatus.COMPLETED, deliverable_id=deliverable.deliverable_id,
    )

    public = next(o for o in repo.list_public_orders(status="COMPLETED") if o.order_id == order.order_id)
    assert public.provenance == "SIMULATED"
