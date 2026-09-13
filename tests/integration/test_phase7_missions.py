def test_create_mission_runs_complete_control_loop(tmp_path, monkeypatch):
    db_path = tmp_path / "mission.db"
    monkeypatch.setenv("KALYX_DB", str(db_path)); monkeypatch.delenv("KALYX_OPERATOR_KEY", raising=False); monkeypatch.setenv("KALYX_REQUIRE_OPERATOR_AUTH", "false")
    from fastapi.testclient import TestClient
    from src.api.server import app
    client = TestClient(app)
    response = client.post("/api/missions", json={"mission": "Find the safest high-value sandbox opportunity", "budget": 100})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "COMPLETED"; assert payload["budget"] == 100; assert payload["treasury"] == 80
    assert payload["event_count"] >= 10; assert payload["receipt"] is not None; assert payload["decision"]["result"] == "APPROVED"; assert payload["review"]["mission_success"] is True
    org_id = payload["organisation_id"]
    events = client.get(f"/api/organisations/{org_id}/events").json()
    types = [event["event_type"] for event in events]
    assert "MISSION_STARTED" in types; assert "REPLAN_TRIGGERED" in types; assert "ACTION_EXECUTED" in types; assert "MISSION_COMPLETED" in types


def test_multiple_organisations_share_db_without_resource_mixing(tmp_path, monkeypatch):
    db_path = tmp_path / "multi-org.db"
    monkeypatch.setenv("KALYX_DB", str(db_path)); monkeypatch.delenv("KALYX_OPERATOR_KEY", raising=False); monkeypatch.setenv("KALYX_REQUIRE_OPERATOR_AUTH", "false")
    from fastapi.testclient import TestClient
    from src.api.server import app
    client = TestClient(app)
    first = client.post("/api/missions", json={"mission": "First bounded mission", "budget": 100})
    second = client.post("/api/missions", json={"mission": "Second bounded mission", "budget": 250})
    assert first.status_code == 200, first.text; assert second.status_code == 200, second.text
    a, b = first.json(), second.json()
    assert a["organisation_id"] != b["organisation_id"]
    assert a["treasury"] == 80; assert b["treasury"] == 230
    assert a["tenant_id"] == b["tenant_id"] == "tenant-demo"
    db = __import__("src.persistence.database", fromlist=["Database"]).Database(str(db_path))
    try:
        rows = db.conn.execute("SELECT id, tenant_id FROM organisations ORDER BY created_at").fetchall()
        assert {row["id"] for row in rows} == {a["organisation_id"], b["organisation_id"]}
        agent_ids = [r["id"] for r in db.conn.execute("SELECT id FROM agents").fetchall()]
        assert len(agent_ids) == len(set(agent_ids)) == 8
        task_ids = [r["id"] for r in db.conn.execute("SELECT id FROM tasks").fetchall()]
        assert len(task_ids) == len(set(task_ids)) == 6
    finally:
        db.close()
