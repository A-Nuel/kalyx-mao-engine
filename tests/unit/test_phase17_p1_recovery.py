"""Milestone 5: Crash/Restart Recovery and Fail-Closed Scanner verification."""

import pytest
from src.domain.marketplace import EscrowAgreement, EscrowStatus, MarketplaceOrder, MarketplaceOrderStatus
from src.persistence.database import Database
from src.persistence.marketplace_repository import MarketplaceRepository
from src.persistence.recovery import Phase17RecoveryScanner


def test_recovery_scanner_detects_dangling_escrow_without_auto_refund():
    """Dangling escrows are detected for operator review, never automatically refunded."""
    db = Database(":memory:")
    with db.conn:
        db.conn.execute("INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES ('t-rec', 'Rec Tenant', 'active', datetime('now'))")
        db.conn.execute("INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES ('org-rec', 't-rec', 'Rec Mission', 1000, 'PLANNING', datetime('now'))")
        
        # Create an escrow whose order is CANCELLED (dangling escrow)
        db.conn.execute(
            """
            INSERT INTO marketplace_orders (
                tenant_id, organisation_id, order_id, title, description, specification_hash,
                required_capability, bounty_amount, status, created_at
            ) VALUES ('t-rec', 'org-rec', 'order-cancelled-1', 'Cancelled Order', 'Desc', 'hash1', 'CAP', 100, 'CANCELLED', datetime('now'))
            """
        )
        db.conn.execute(
            """
            INSERT INTO marketplace_escrows (
                tenant_id, organisation_id, escrow_id, order_id, client_tenant_id,
                client_org_id, bounty_amount, status, created_at
            ) VALUES ('t-rec', 'org-rec', 'esc-dangling-1', 'order-cancelled-1', 't-rec', 'org-rec', 100, 'HELD', datetime('now'))
            """
        )

    scanner = Phase17RecoveryScanner(db)
    dangling = scanner.scan_dangling_escrows()
    assert len(dangling) == 1
    assert dangling[0]["escrow_id"] == "esc-dangling-1"
    assert dangling[0]["escrow_status"] == "HELD"
    assert dangling[0]["order_status"] == "CANCELLED"

    # Invariant: Escrow MUST remain HELD, not silently auto-refunded or deleted
    cur = db.conn.cursor()
    row = cur.execute("SELECT status FROM marketplace_escrows WHERE escrow_id = 'esc-dangling-1'").fetchone()
    assert row["status"] == "HELD"


def test_recovery_scanner_preserves_unknown_consequential_operations():
    """UNKNOWN consequential operations are discovered, never automatically marked SUCCEEDED."""
    db = Database(":memory:")
    with db.conn:
        db.conn.execute("INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES ('t-rec', 'Rec Tenant', 'active', datetime('now'))")
        db.conn.execute("INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES ('org-rec', 't-rec', 'Rec Mission', 1000, 'PLANNING', datetime('now'))")
        
        # Insert consequential operation interrupted in UNKNOWN state
        db.conn.execute(
            """
            INSERT INTO consequential_operations (
                id, tenant_id, organisation_id, proposal_id, decision_id, idempotency_key,
                action_type, target, parameters, amount, provider_name, state, created_at, updated_at
            ) VALUES (
                'op-unknown-1', 't-rec', 'org-rec', 'prop-1', 'dec-1', 'idem-unknown-key',
                'ORBIO_CREDIT_PURCHASE', 'https://api.orbio.ai/v1/purchase', '{}', 50, 'orbio', 'unknown', datetime('now'), datetime('now')
            )
            """
        )

    scanner = Phase17RecoveryScanner(db)
    unknowns = scanner.scan_unknown_consequential_operations()
    assert len(unknowns) == 1
    assert unknowns[0]["id"] == "op-unknown-1"
    assert unknowns[0]["state"] == "unknown"

    # Invariant: State in database MUST remain 'unknown' pending verified audit
    cur = db.conn.cursor()
    row = cur.execute("SELECT state FROM consequential_operations WHERE id = 'op-unknown-1'").fetchone()
    assert row["state"] == "unknown"


def test_durable_state_persists_across_database_reconnection(tmp_path):
    """File-backed SQLite state survives full process/connection teardown and reboot."""
    db_file = str(tmp_path / "kalyx_recovery_test.db")

    # --- PROCESS 1: Write State ---
    db1 = Database(db_file)
    with db1.conn:
        db1.conn.execute("INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES ('t-disk', 'Disk Tenant', 'active', datetime('now'))")
        db1.conn.execute("INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES ('org-disk', 't-disk', 'Disk Mission', 1000, 'PLANNING', datetime('now'))")
    
    repo1 = MarketplaceRepository(db1)
    order = MarketplaceOrder.create(
        tenant_id="t-disk",
        organisation_id="org-disk",
        order_id="order-persisted-42",
        title="Durable Market Order",
        description="Must survive process shutdown",
        required_capability="CRYPTO_AUDIT",
        bounty_amount=300,
    )
    repo1.create_order(order)
    db1.close()
    del db1, repo1

    # --- PROCESS 2: Reboot & Recover ---
    db2 = Database(db_file)
    repo2 = MarketplaceRepository(db2)
    recovered = repo2.get_order("t-disk", "org-disk", "order-persisted-42")
    assert recovered is not None
    assert recovered.order_id == "order-persisted-42"
    assert recovered.bounty_amount == 300
    assert recovered.title == "Durable Market Order"
    db2.close()
