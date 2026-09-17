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
