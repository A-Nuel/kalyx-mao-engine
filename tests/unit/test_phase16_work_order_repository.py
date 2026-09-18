"""Unit tests for Phase 16 WorkOrderRepository.

Verifies:
1. Work order save, get, list, filter by status, update
2. Deliverable save, get, get_by_work_order
3. Deliverable receipt save, get, get_for_work_order
4. Revenue event save, list, idempotency
5. Mission lineage save, get, list
6. Multi-tenant and organisation isolation
7. Durability across database reconnect / restart
"""

from datetime import datetime, timezone
import os
import tempfile
import pytest

from src.domain.enums import CurrencyAsset, DeliverableStatus, WorkOrderStatus
from src.domain.work_order import (
    RevenueEvent,
    WorkDeliverable,
    WorkDeliverableReceipt,
    WorkOrder,
)
from src.persistence.database import Database
from src.persistence.work_order_repository import WorkOrderRepository


def _seed_tenant_and_org(db: Database, tenant_id: str, org_id: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, 'active', ?)",
            (tenant_id, f"Tenant {tenant_id}", now_iso),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, 'Autonomous Ops', 1000, 'ACTIVE', ?)",
            (org_id, tenant_id, now_iso),
        )


@pytest.fixture
def db():
    database = Database(":memory:")
    _seed_tenant_and_org(database, "t1", "org1")
    _seed_tenant_and_org(database, "t2", "org1")
    return database


@pytest.fixture
def repo(db):
    return WorkOrderRepository(db)


def test_work_order_crud(repo):
    wo = WorkOrder(
        tenant_id="t1",
        organisation_id="org1",
        work_order_id="wo-101",
        client_id="client-alpha",
        title="Analyze Q3 Market Trends",
        description="Detailed market trend report for Q3",
        deliverable_type="market_analysis",
        required_orbio_credits=500_000,
        bounty_amount=250,
        bounty_asset=CurrencyAsset.USDG,
        deadline_seconds=7200,
        status=WorkOrderStatus.PROPOSED,
        created_at=datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc),
        metadata={"client_tier": "enterprise"},
    )

    repo.save_work_order(wo)

    fetched = repo.get_work_order("t1", "org1", "wo-101")
    assert fetched is not None
    assert fetched.work_order_id == "wo-101"
    assert fetched.client_id == "client-alpha"
    assert fetched.bounty_amount == 250
    assert fetched.bounty_asset == CurrencyAsset.USDG
    assert fetched.required_orbio_credits == 500_000
    assert fetched.status == WorkOrderStatus.PROPOSED
    assert fetched.metadata == {"client_tier": "enterprise"}

    # Update status
    wo.status = WorkOrderStatus.IN_PROGRESS
    repo.save_work_order(wo)

    updated = repo.get_work_order("t1", "org1", "wo-101")
    assert updated is not None
    assert updated.status == WorkOrderStatus.IN_PROGRESS

    # Listing
    all_orders = repo.list_work_orders("t1", "org1")
    assert len(all_orders) == 1
    assert all_orders[0].work_order_id == "wo-101"

    filtered = repo.list_work_orders("t1", "org1", status=WorkOrderStatus.IN_PROGRESS)
    assert len(filtered) == 1

    empty_filter = repo.list_work_orders("t1", "org1", status=WorkOrderStatus.SETTLED)
    assert len(empty_filter) == 0


def test_deliverable_crud(repo):
    deliverable = WorkDeliverable(
        deliverable_id="deliv-201",
        work_order_id="wo-101",
        producer_agent_id="agent-analyst",
        content_payload={"summary": "Bullish", "score": 92},
        content_hash="hash-deliv-abc123",
        orbio_credits_consumed=450_000,
        execution_telemetry={"latency_ms": 320, "model": "orbio-v1"},
        created_at=datetime(2026, 9, 17, 11, 0, tzinfo=timezone.utc),
    )

    repo.save_deliverable("t1", "org1", deliverable)

    fetched = repo.get_deliverable("t1", "org1", "deliv-201")
    assert fetched is not None
    assert fetched.deliverable_id == "deliv-201"
    assert fetched.work_order_id == "wo-101"
    assert fetched.producer_agent_id == "agent-analyst"
    assert fetched.content_payload == {"summary": "Bullish", "score": 92}
    assert fetched.content_hash == "hash-deliv-abc123"
    assert fetched.orbio_credits_consumed == 450_000
    assert fetched.execution_telemetry == {"latency_ms": 320, "model": "orbio-v1"}

    by_wo = repo.get_deliverable_by_work_order("t1", "org1", "wo-101")
    assert by_wo is not None
    assert by_wo.deliverable_id == "deliv-201"


def test_receipt_crud(repo):
    receipt = WorkDeliverableReceipt(
        receipt_id="rcpt-301",
        work_order_id="wo-101",
        deliverable_id="deliv-201",
        content_hash="hash-deliv-abc123",
        evidence_hash="evidence-xyz789",
        verifier_identity="auditor-verifier-1",
        status=DeliverableStatus.ACCEPTED,
        verification_notes="Deliverable meets all rubric criteria",
        verified_at=datetime(2026, 9, 17, 11, 30, tzinfo=timezone.utc),
        hmac_signature="sig-hmac-secure",
    )

    repo.save_receipt("t1", "org1", receipt)

    fetched = repo.get_receipt("t1", "org1", "rcpt-301")
    assert fetched is not None
    assert fetched.receipt_id == "rcpt-301"
    assert fetched.status == DeliverableStatus.ACCEPTED
    assert fetched.hmac_signature == "sig-hmac-secure"
    assert fetched.verification_notes == "Deliverable meets all rubric criteria"

    by_wo = repo.get_receipt_for_work_order("t1", "org1", "wo-101")
    assert by_wo is not None
    assert by_wo.receipt_id == "rcpt-301"


def test_revenue_event_crud_and_idempotency(repo):
    rev = RevenueEvent(
        revenue_event_id="rev-401",
        work_order_id="wo-101",
        gross_revenue_usdg=250,
        direct_expense_usdg=50,
        net_surplus_usdg=200,
        orbio_credits_consumed=450_000,
        allocated_to_mission_budget=140,
        allocated_to_reserve=60,
        settled_at=datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
        ledger_tx_id="tx-settle-001",
    )

    repo.save_revenue_event("t1", "org1", rev)

    events = repo.list_revenue_events("t1", "org1")
    assert len(events) == 1
    assert events[0].revenue_event_id == "rev-401"
    assert events[0].gross_revenue_usdg == 250
    assert events[0].net_surplus_usdg == 200
    assert events[0].allocated_to_mission_budget == 140
    assert events[0].allocated_to_reserve == 60

    # Idempotency check: duplicate save does not raise error and does not duplicate
    repo.save_revenue_event("t1", "org1", rev)
    events_after = repo.list_revenue_events("t1", "org1")
    assert len(events_after) == 1


def test_mission_lineage_tracking(repo):
    repo.record_mission_lineage(
        tenant_id="t1",
        organisation_id="org1",
        mission_id="m-genesis",
        parent_mission_id=None,
        funding_source="GENESIS_SEED",
        funding_amount_usdg=100,
    )

    repo.record_mission_lineage(
        tenant_id="t1",
        organisation_id="org1",
        mission_id="m-child-1",
        parent_mission_id="m-genesis",
        funding_source="SURPLUS_RECONCILIATION",
        funding_amount_usdg=140,
    )

    m1 = repo.get_mission_lineage("t1", "org1", "m-genesis")
    assert m1 is not None
    assert m1["mission_id"] == "m-genesis"
    assert m1["parent_mission_id"] is None
    assert m1["funding_amount_usdg"] == 100

    m2 = repo.get_mission_lineage("t1", "org1", "m-child-1")
    assert m2 is not None
    assert m2["mission_id"] == "m-child-1"
    assert m2["parent_mission_id"] == "m-genesis"
    assert m2["funding_amount_usdg"] == 140

    all_lineage = repo.list_mission_lineage("t1", "org1")
    assert len(all_lineage) == 2
    assert [l["mission_id"] for l in all_lineage] == ["m-genesis", "m-child-1"]


def test_multi_tenant_and_org_isolation(repo):
    # Save in Tenant 1 Org 1
    wo1 = WorkOrder(
        tenant_id="t1",
        organisation_id="org1",
        work_order_id="wo-cross-1",
        client_id="c1",
        title="Secret Project 1",
        description="Confidential",
        deliverable_type="code",
        required_orbio_credits=100_000,
        bounty_amount=100,
        bounty_asset=CurrencyAsset.USDG,
        deadline_seconds=3600,
        status=WorkOrderStatus.PROPOSED,
    )
    repo.save_work_order(wo1)

    # Save in Tenant 2 Org 1
    wo2 = WorkOrder(
        tenant_id="t2",
        organisation_id="org1",
        work_order_id="wo-cross-2",
        client_id="c2",
        title="Secret Project 2",
        description="Confidential",
        deliverable_type="code",
        required_orbio_credits=100_000,
        bounty_amount=100,
        bounty_asset=CurrencyAsset.USDG,
        deadline_seconds=3600,
        status=WorkOrderStatus.PROPOSED,
    )
    repo.save_work_order(wo2)

    # Cross-tenant get
    assert repo.get_work_order("t1", "org1", "wo-cross-1") is not None
    assert repo.get_work_order("t2", "org1", "wo-cross-1") is None
    assert repo.get_work_order("t1", "org1", "wo-cross-2") is None
    assert repo.get_work_order("t2", "org1", "wo-cross-2") is not None

    # Cross-tenant list
    t1_orders = repo.list_work_orders("t1", "org1")
    assert len(t1_orders) == 1
    assert t1_orders[0].work_order_id == "wo-cross-1"

    t2_orders = repo.list_work_orders("t2", "org1")
    assert len(t2_orders) == 1
    assert t2_orders[0].work_order_id == "wo-cross-2"


def test_durability_across_process_restart():
    """Verify that state persists to disk and is completely intact when re-opened."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "kalyx_test.db")

        # Session 1: Create and write
        db1 = Database(db_path)
        try:
            _seed_tenant_and_org(db1, "t-durable", "org-durable")
            repo1 = WorkOrderRepository(db1)

            wo = WorkOrder(
                tenant_id="t-durable",
                organisation_id="org-durable",
                work_order_id="wo-durable-1",
                client_id="client-persist",
                title="Durable Order",
                description="Must survive crash",
                deliverable_type="audit",
                required_orbio_credits=200_000,
                bounty_amount=300,
                bounty_asset=CurrencyAsset.USDG,
                deadline_seconds=3600,
                status=WorkOrderStatus.SETTLED,
                metadata={"restart_verified": True},
            )
            repo1.save_work_order(wo)

            deliv = WorkDeliverable(
                deliverable_id="deliv-durable-1",
                work_order_id="wo-durable-1",
                producer_agent_id="agent-persist",
                content_payload={"audit": "clean"},
                content_hash="hash-durable",
                orbio_credits_consumed=180_000,
                execution_telemetry={"step": "final"},
            )
            repo1.save_deliverable("t-durable", "org-durable", deliv)

            receipt = WorkDeliverableReceipt(
                receipt_id="rcpt-durable-1",
                work_order_id="wo-durable-1",
                deliverable_id="deliv-durable-1",
                content_hash="hash-durable",
                evidence_hash="ev-durable",
                verifier_identity="auditor-persist",
                status=DeliverableStatus.ACCEPTED,
                verification_notes="Persisted verified",
                hmac_signature="sig-persist",
            )
            repo1.save_receipt("t-durable", "org-durable", receipt)

            rev = RevenueEvent(
                revenue_event_id="rev-durable-1",
                work_order_id="wo-durable-1",
                gross_revenue_usdg=300,
                direct_expense_usdg=20,
                net_surplus_usdg=280,
                orbio_credits_consumed=180_000,
                allocated_to_mission_budget=196,
                allocated_to_reserve=84,
                ledger_tx_id="tx-durable",
            )
            repo1.save_revenue_event("t-durable", "org-durable", rev)

            repo1.record_mission_lineage(
                tenant_id="t-durable",
                organisation_id="org-durable",
                mission_id="m-durable-1",
                parent_mission_id=None,
                funding_source="TEST",
                funding_amount_usdg=100,
            )
        finally:
            db1.close()

        # Session 2: Reopen from disk (simulating restart)
        db2 = Database(db_path)
        try:
            repo2 = WorkOrderRepository(db2)

            recovered_wo = repo2.get_work_order("t-durable", "org-durable", "wo-durable-1")
            assert recovered_wo is not None
            assert recovered_wo.title == "Durable Order"
            assert recovered_wo.status == WorkOrderStatus.SETTLED
            assert recovered_wo.metadata == {"restart_verified": True}

            recovered_deliv = repo2.get_deliverable("t-durable", "org-durable", "deliv-durable-1")
            assert recovered_deliv is not None
            assert recovered_deliv.content_hash == "hash-durable"
            assert recovered_deliv.content_payload == {"audit": "clean"}

            recovered_receipt = repo2.get_receipt("t-durable", "org-durable", "rcpt-durable-1")
            assert recovered_receipt is not None
            assert recovered_receipt.status == DeliverableStatus.ACCEPTED

            recovered_revs = repo2.list_revenue_events("t-durable", "org-durable")
            assert len(recovered_revs) == 1
            assert recovered_revs[0].net_surplus_usdg == 280

            recovered_lineage = repo2.get_mission_lineage("t-durable", "org-durable", "m-durable-1")
            assert recovered_lineage is not None
            assert recovered_lineage["funding_amount_usdg"] == 100
        finally:
            db2.close()
