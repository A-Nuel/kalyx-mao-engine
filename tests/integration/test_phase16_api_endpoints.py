"""
Integration tests for Phase 16 REST API endpoints.

Tests the six Phase 16 endpoints end-to-end using FastAPI TestClient with a
real SQLite database (via tmp_path).  All tests use the /api/demo/run bootstrap
to provision a live organisation with treasury funds before exercising the
Phase 16 routes.
"""
import os
import pytest
from fastapi.testclient import TestClient
import src.api.server as server
from src.identity.models import Membership, MembershipRole, Principal
from src.identity.repository import IdentityRepository
from src.persistence.database import Database
from src.domain.entities import Organisation
from src.persistence.repositories import SqliteRepository


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def client_and_org(tmp_path, monkeypatch):
    """Spin up a test client with a fresh DB and a bootstrapped organisation."""
    db_path = tmp_path / "phase16_api.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    client = TestClient(server.app)

    # Bootstrap via demo run — creates org + agents + seeds treasury
    res = client.post("/api/demo/run")
    assert res.status_code == 200, res.text
    org_id = res.json()["organisation_id"]
    return client, org_id


# ---------------------------------------------------------------------------
# 1. List work orders — empty initially
# ---------------------------------------------------------------------------

def test_list_work_orders_empty(client_and_org):
    client, org_id = client_and_org

    res = client.get(f"/api/v1/organisations/{org_id}/work-orders")
    assert res.status_code == 200
    data = res.json()
    assert "work_orders" in data
    assert "total" in data
    assert isinstance(data["work_orders"], list)
    # Immediately after demo run there are no work orders persisted
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# 2. Create a work order
# ---------------------------------------------------------------------------

def test_create_work_order(client_and_org):
    client, org_id = client_and_org

    payload = {
        "title": "Security Audit v1",
        "description": "Full smart-contract security audit for client Alpha.",
        "deliverable_type": "SECURITY_AUDIT",
        "required_orbio_credits": 100_000,
        "bounty_amount": 150,
        "bounty_asset": "USDG",
        "deadline_seconds": 3600,
    }
    res = client.post(f"/api/v1/organisations/{org_id}/work-orders", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["created"] is True
    assert "work_order_id" in data
    assert data["status"] == "PROPOSED"


# ---------------------------------------------------------------------------
# 3. List work orders after creating one
# ---------------------------------------------------------------------------

def test_list_work_orders_after_create(client_and_org):
    client, org_id = client_and_org

    # Create two work orders
    for i in range(2):
        payload = {
            "title": f"Data Pipeline Audit {i}",
            "description": f"Audit pipeline batch {i} for data quality.",
            "deliverable_type": "SECURITY_AUDIT",
            "required_orbio_credits": 50_000,
            "bounty_amount": 80,
        }
        r = client.post(f"/api/v1/organisations/{org_id}/work-orders", json=payload)
        assert r.status_code == 200

    res = client.get(f"/api/v1/organisations/{org_id}/work-orders")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    for item in data["work_orders"]:
        assert "work_order" in item
        wo = item["work_order"]
        assert wo["status"] == "PROPOSED"
        assert "work_order_id" in wo
        assert "bounty_amount" in wo


# ---------------------------------------------------------------------------
# 4. List work orders — unknown org returns 404
# ---------------------------------------------------------------------------

def test_list_work_orders_unknown_org(client_and_org):
    client, _ = client_and_org
    res = client.get("/api/v1/organisations/nonexistent-org-xyz/work-orders")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 5. Mission lineage — empty initially
# ---------------------------------------------------------------------------

def test_mission_lineage_empty(client_and_org):
    client, org_id = client_and_org

    res = client.get(f"/api/v1/organisations/{org_id}/missions/lineage")
    assert res.status_code == 200
    data = res.json()
    assert "lineage" in data
    assert "total" in data
    assert isinstance(data["lineage"], list)
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# 6. Treasury breakdown — returns solvency regime and metrics
# ---------------------------------------------------------------------------

def test_treasury_breakdown_structure(client_and_org):
    client, org_id = client_and_org

    res = client.get(f"/api/v1/organisations/{org_id}/treasury/breakdown")
    assert res.status_code == 200
    data = res.json()

    assert data["organisation_id"] == org_id
    assert "solvency_regime" in data
    assert data["solvency_regime"] in {"EXPANSION", "AUSTERE", "STANDBY"}
    assert "treasury_usdg" in data
    assert isinstance(data["treasury_usdg"], int)
    assert "cumulative_gross_revenue_usdg" in data
    assert "cumulative_net_surplus_usdg" in data
    assert "cumulative_compute_credits_consumed" in data
    assert "revenue_events_count" in data
    # Fresh demo org — no revenue events yet
    assert data["revenue_events_count"] == 0


# ---------------------------------------------------------------------------
# 7. Treasury breakdown — demo org is funded, regime should not be STANDBY
# ---------------------------------------------------------------------------

def test_treasury_breakdown_funded_org_not_standby(client_and_org):
    client, org_id = client_and_org

    res = client.get(f"/api/v1/organisations/{org_id}/treasury/breakdown")
    assert res.status_code == 200
    data = res.json()
    # The demo run seeds the treasury above the standby threshold
    assert data["solvency_regime"] != "STANDBY"
    assert data["treasury_usdg"] > 0


# ---------------------------------------------------------------------------
# 8. Treasury breakdown — unknown org returns 404
# ---------------------------------------------------------------------------

def test_treasury_breakdown_unknown_org(client_and_org):
    client, _ = client_and_org
    res = client.get("/api/v1/organisations/ghost-org/treasury/breakdown")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 9. Daemon step — STANDBY regime preserves capital
# ---------------------------------------------------------------------------

def test_daemon_step_standby_preserves_capital(tmp_path, monkeypatch):
    """Manually create an org with zero treasury so daemon halts in STANDBY."""
    db_path = tmp_path / "phase16_standby.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    client = TestClient(server.app)

    # Bootstrap org
    res = client.post("/api/demo/run")
    assert res.status_code == 200
    org_id = res.json()["organisation_id"]

    # Get treasury before step
    before = client.get(f"/api/v1/organisations/{org_id}/treasury/breakdown").json()

    # Step the daemon (may be EXPANSION/AUSTERE with demo treasury)
    step_res = client.post(f"/api/v1/organisations/{org_id}/daemon/step")
    assert step_res.status_code == 200
    step_data = step_res.json()

    # Validate response shape
    assert "cycle_number" in step_data
    assert "regime" in step_data
    assert step_data["regime"] in {"EXPANSION", "AUSTERE", "STANDBY"}
    assert "treasury_before" in step_data
    assert "treasury_after" in step_data
    assert "success" in step_data


# ---------------------------------------------------------------------------
# 10. Daemon step — returns valid shape for funded org
# ---------------------------------------------------------------------------

def test_daemon_step_funded_org(client_and_org):
    client, org_id = client_and_org

    res = client.post(f"/api/v1/organisations/{org_id}/daemon/step")
    assert res.status_code == 200
    data = res.json()

    assert isinstance(data["cycle_number"], int)
    assert data["cycle_number"] >= 1
    assert data["regime"] in {"EXPANSION", "AUSTERE", "STANDBY"}
    assert isinstance(data["treasury_before"], int)
    assert isinstance(data["treasury_after"], int)
    assert isinstance(data["success"], bool)
    # summary is a human-readable state string from DaemonCycleResult.state_summary
    assert isinstance(data["summary"], str)
    assert len(data["summary"]) > 0


# ---------------------------------------------------------------------------
# 11. Daemon step — unknown org returns 404
# ---------------------------------------------------------------------------

def test_daemon_step_unknown_org(client_and_org):
    client, _ = client_and_org
    res = client.post("/api/v1/organisations/phantom-org/daemon/step")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 12. Create work order then list — round-trip with explicit ID
# ---------------------------------------------------------------------------

def test_create_and_retrieve_by_id(client_and_org):
    client, org_id = client_and_org
    explicit_id = "wo-explicit-roundtrip-001"

    payload = {
        "work_order_id": explicit_id,
        "title": "Smart Contract Verification",
        "description": "Formal verification of token contract logic.",
        "deliverable_type": "SECURITY_AUDIT",
        "required_orbio_credits": 200_000,
        "bounty_amount": 300,
        "bounty_asset": "USDG",
        "deadline_seconds": 7200,
        "metadata": {"client_tier": "enterprise"},
    }
    create_res = client.post(f"/api/v1/organisations/{org_id}/work-orders", json=payload)
    assert create_res.status_code == 200
    assert create_res.json()["work_order_id"] == explicit_id

    # Retrieve via list and find the matching order
    list_res = client.get(f"/api/v1/organisations/{org_id}/work-orders")
    assert list_res.status_code == 200
    work_orders = list_res.json()["work_orders"]
    ids = [item["work_order"]["work_order_id"] for item in work_orders]
    assert explicit_id in ids

    match = next(item for item in work_orders if item["work_order"]["work_order_id"] == explicit_id)
    assert match["work_order"]["bounty_amount"] == 300
    assert match["work_order"]["status"] == "PROPOSED"
    assert match["deliverable"] is None
    assert match["receipt"] is None


# ---------------------------------------------------------------------------
# 13. Mission lineage endpoint — unknown org returns 404
# ---------------------------------------------------------------------------

def test_mission_lineage_unknown_org(client_and_org):
    client, _ = client_and_org
    res = client.get("/api/v1/organisations/nonexistent/missions/lineage")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 14. Create work order — validation error on missing title
# ---------------------------------------------------------------------------

def test_create_work_order_missing_title(client_and_org):
    client, org_id = client_and_org

    payload = {
        "description": "Missing title field.",
        "bounty_amount": 100,
    }
    res = client.post(f"/api/v1/organisations/{org_id}/work-orders", json=payload)
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# 15. Create work order — invalid bounty_amount rejected
# ---------------------------------------------------------------------------

def test_create_work_order_invalid_bounty(client_and_org):
    client, org_id = client_and_org

    payload = {
        "title": "Bad Bounty Order",
        "description": "Bounty is below minimum allowed value.",
        "bounty_amount": 0,  # ge=1 constraint
    }
    res = client.post(f"/api/v1/organisations/{org_id}/work-orders", json=payload)
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# 16. P1-1: Identity Authorization & Isolation Regression Tests
# ---------------------------------------------------------------------------

def test_phase16_identity_authorization_and_tenant_isolation(tmp_path, monkeypatch):
    """Prove that Phase 16 endpoints strictly enforce identity auth, write permissions, and tenant isolation."""
    db_path = str(tmp_path / "phase16_identity.db")
    db = Database(db_path)
    try:
        db.conn.execute("INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)", ("tenant-a", "Tenant A", "2026-09-18T00:00:00"))
        db.conn.execute("INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)", ("tenant-b", "Tenant B", "2026-09-18T00:00:00"))
        identity = IdentityRepository(db)
        identity.save_principal(Principal(id="owner-a", name="Alice Owner"))
        identity.save_principal(Principal(id="viewer-a", name="Victor Viewer"))
        identity.save_principal(Principal(id="owner-b", name="Bob Owner"))
        
        identity.save_membership(Membership(principal_id="owner-a", tenant_id="tenant-a", role=MembershipRole.OWNER))
        identity.save_membership(Membership(principal_id="viewer-a", tenant_id="tenant-a", role=MembershipRole.VIEWER))
        identity.save_membership(Membership(principal_id="owner-b", tenant_id="tenant-b", role=MembershipRole.OWNER))
        
        repo = SqliteRepository(db)
        repo.save_organisation(Organisation(id="org-a", tenant_id="tenant-a", mission="Org A Mission", treasury_balance=500))
        repo.save_organisation(Organisation(id="org-b", tenant_id="tenant-b", mission="Org B Mission", treasury_balance=500))
        db.conn.commit()
    finally:
        db.close()

    monkeypatch.setenv("KALYX_DB", db_path)
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")
    client = TestClient(server.app)

    wo_payload = {
        "title": "Audit Protocol",
        "description": "Smart contract audit for security",
        "deliverable_type": "SECURITY_AUDIT",
        "required_orbio_credits": 100_000,
        "bounty_amount": 120,
    }

    # 1. Unauthenticated requests must be rejected with 401
    unauth_list = client.get("/api/v1/organisations/org-a/work-orders")
    assert unauth_list.status_code == 401
    unauth_create = client.post("/api/v1/organisations/org-a/work-orders", json=wo_payload)
    assert unauth_create.status_code == 401
    unauth_step = client.post("/api/v1/organisations/org-a/daemon/step")
    assert unauth_step.status_code == 401

    # 2. Cross-tenant request must return 404 (do not leak org existence in other tenants)
    cross_headers = {"X-Principal-ID": "owner-a", "X-Tenant-ID": "tenant-a"}
    cross_res = client.get("/api/v1/organisations/org-b/work-orders", headers=cross_headers)
    assert cross_res.status_code == 404
    cross_post = client.post("/api/v1/organisations/org-b/work-orders", json=wo_payload, headers=cross_headers)
    assert cross_post.status_code == 404

    # 3. Wrong membership (owner-b trying to access org-a under tenant-a) must return 403
    unauth_member = client.get("/api/v1/organisations/org-a/work-orders", headers={"X-Principal-ID": "owner-b", "X-Tenant-ID": "tenant-a"})
    assert unauth_member.status_code == 403

    # 4. Viewer role has read-only permission: GET allowed, but POST write rejected with 403
    viewer_headers = {"X-Principal-ID": "viewer-a", "X-Tenant-ID": "tenant-a"}
    viewer_read = client.get("/api/v1/organisations/org-a/work-orders", headers=viewer_headers)
    assert viewer_read.status_code == 200

    viewer_write = client.post("/api/v1/organisations/org-a/work-orders", json=wo_payload, headers=viewer_headers)
    assert viewer_write.status_code == 403
    assert "Viewer role is read-only" in viewer_write.json()["detail"]

    viewer_step = client.post("/api/v1/organisations/org-a/daemon/step", headers=viewer_headers)
    assert viewer_step.status_code == 403

    # 5. Authenticated Owner succeeds on write actions
    owner_headers = {"X-Principal-ID": "owner-a", "X-Tenant-ID": "tenant-a"}
    owner_create = client.post("/api/v1/organisations/org-a/work-orders", json=wo_payload, headers=owner_headers)
    assert owner_create.status_code == 200
    assert owner_create.json()["created"] is True


# ---------------------------------------------------------------------------
# 17. P1-2 & P1-3: Governed Credit Purchase, Work Execution, and Revenue Settlement
# ---------------------------------------------------------------------------

def test_work_order_execution_end_to_end_governed_loop(tmp_path, monkeypatch):
    """Prove that executing a work order via the API triggers the governed purchase loop,
    uses deposit_revenue() on OrganisationScopedLedger, and settles surplus correctly."""
    db_path = tmp_path / "phase16_exec.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "false")
    client = TestClient(server.app)

    # Bootstrap org with demo funds
    res = client.post("/api/demo/run")
    assert res.status_code == 200
    org_id = res.json()["organisation_id"]

    # Create work order requiring 200,000 credits (current credits = 0, so requires purchase)
    wo_id = "wo-exec-e2e-001"
    create_res = client.post(
        f"/api/v1/organisations/{org_id}/work-orders",
        json={
            "work_order_id": wo_id,
            "title": "Protocol Math Audit",
            "description": "Audit AMM invariant calculations",
            "deliverable_type": "SECURITY_AUDIT",
            "required_orbio_credits": 200_000,
            "bounty_amount": 100,
            "bounty_asset": "USDG",
            "deadline_seconds": 3600,
        },
    )
    assert create_res.status_code == 200

    # Execute work order via API
    exec_res = client.post(f"/api/v1/organisations/{org_id}/work-orders/{wo_id}/execute")
    assert exec_res.status_code == 200, exec_res.text
    outcome = exec_res.json()

    assert outcome["success"] is True
    assert outcome["status"] == "SETTLED"
    assert outcome["viable"] is True
    assert outcome["net_surplus_usdg"] > 0
    assert outcome["allocated_to_mission_budget"] > 0
    assert outcome["allocated_to_reserve"] > 0

    # Verify deliverable and receipt are persisted and queryable via list endpoint
    list_res = client.get(f"/api/v1/organisations/{org_id}/work-orders")
    assert list_res.status_code == 200
    wo_data = next(item for item in list_res.json()["work_orders"] if item["work_order"]["work_order_id"] == wo_id)
    assert wo_data["deliverable"] is not None
    assert wo_data["receipt"] is not None
    assert wo_data["receipt"]["status"] == "ACCEPTED"


# ---------------------------------------------------------------------------
# 18. P2-4: Daemon State Persistence Across Multiple Calls
# ---------------------------------------------------------------------------

def test_daemon_persists_state_across_separate_http_calls(tmp_path, monkeypatch):
    """Prove that separate /daemon/step calls persist and advance cycle count and lineage across invocations."""
    db_path = tmp_path / "phase16_daemon_state.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "false")
    client = TestClient(server.app)

    # Bootstrap org
    res = client.post("/api/demo/run")
    assert res.status_code == 200
    org_id = res.json()["organisation_id"]

    # Step 1: cycle 1
    step1 = client.post(f"/api/v1/organisations/{org_id}/daemon/step").json()
    assert step1["cycle_number"] == 1

    # Step 2: cycle 2 (proves cycle count is not reset to 1 on subsequent HTTP requests)
    step2 = client.post(f"/api/v1/organisations/{org_id}/daemon/step").json()
    assert step2["cycle_number"] == 2

    # Step 3: cycle 3
    step3 = client.post(f"/api/v1/organisations/{org_id}/daemon/step").json()
    assert step3["cycle_number"] == 3

