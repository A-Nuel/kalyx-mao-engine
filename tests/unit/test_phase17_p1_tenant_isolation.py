"""Milestone 1: Tenant Isolation & Public Projection regression suite."""

import pytest
from src.domain.marketplace import EscrowAgreement, EscrowStatus, MarketplaceOrder, MarketplaceOrderStatus, PublicMarketplaceOrder
from src.domain.capability import CapabilityGrant, CapabilityGrantStatus
from src.persistence.database import Database
from src.persistence.marketplace_repository import MarketplaceRepository


@pytest.fixture
def repo():
    db = Database(":memory:")
    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("tenant-alpha", "Tenant Alpha", "active"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("tenant-beta", "Tenant Beta", "active"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-alpha", "tenant-alpha", "Alpha Mission", 1000, "PLANNING"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-beta", "tenant-beta", "Beta Mission", 500, "PLANNING"),
        )
    return MarketplaceRepository(db)


def test_cross_tenant_private_order_read_isolation(repo):
    """Tenant Beta cannot read Tenant Alpha private order details via scoped get_order."""
    order = MarketplaceOrder.create(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        order_id="mkt-isolated-1",
        title="Secret Spec",
        description="Private instructions",
        required_capability="CODE_REVIEW",
        bounty_amount=100,
    )
    repo.create_order(order)

    # Scoped to alpha -> success
    alpha_read = repo.get_order("tenant-alpha", "org-alpha", "mkt-isolated-1")
    assert alpha_read is not None
    assert alpha_read.title == "Secret Spec"

    # Scoped to beta -> isolated (None)
    beta_read = repo.get_order("tenant-beta", "org-beta", "mkt-isolated-1")
    assert beta_read is None


def test_public_discovery_returns_sanitized_projection(repo):
    """Cross-tenant discovery returns PublicMarketplaceOrder without private fields."""
    order = MarketplaceOrder.create(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        order_id="mkt-pub-1",
        title="Public Work Request",
        description="Perform verification",
        required_capability="DATA_ANALYSIS",
        bounty_amount=250,
    )
    repo.create_order(order)

    public_orders = repo.list_public_orders(status="OPEN")
    assert len(public_orders) >= 1
    target = next((p for p in public_orders if p.order_id == "mkt-pub-1"), None)
    assert target is not None
    assert isinstance(target, PublicMarketplaceOrder)

    # Exposed public fields
    assert target.order_id == "mkt-pub-1"
    assert target.bounty_amount == 250
    assert target.required_capability == "DATA_ANALYSIS"

    # Verify no private internal fields leaked in dict representation
    d = target.to_dict()
    assert "tenant_id" not in d
    assert "organisation_id" not in d
    assert "claimed_by_tenant_id" not in d
    assert "deliverable_id" not in d


def test_cross_tenant_escrow_isolation(repo):
    """Tenant Beta cannot read or access Tenant Alpha escrow agreements."""
    escrow = EscrowAgreement(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        escrow_id="esc-iso-1",
        order_id="mkt-iso-1",
        client_tenant_id="tenant-alpha",
        client_org_id="org-alpha",
        bounty_amount=300,
    )
    repo.save_escrow(escrow)

    # Alpha gets it
    assert repo.get_escrow("tenant-alpha", "org-alpha", "esc-iso-1") is not None

    # Beta cannot access it
    assert repo.get_escrow("tenant-beta", "org-beta", "esc-iso-1") is None


def test_cross_tenant_capability_grant_isolation(repo):
    """Capability grants issued in Tenant Alpha cannot be accessed by Tenant Beta."""
    grant = CapabilityGrant(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        grant_id="grant-iso-1",
        agent_id="agent-alpha-1",
        capability_name="ADVANCED_SECURITY_AUDIT",
        permission_level="EXECUTE",
        trigger_performance_score=95.0,
        granted_by_policy_id="pol-1",
    )
    repo.save_capability_grant(grant)

    # Alpha org has active grant
    alpha_grants = repo.list_agent_grants("tenant-alpha", "org-alpha", "agent-alpha-1", active_only=True)
    assert len(alpha_grants) == 1

    # Beta org has zero grants for this agent
    beta_grants = repo.list_agent_grants("tenant-beta", "org-beta", "agent-alpha-1", active_only=True)
    assert len(beta_grants) == 0
