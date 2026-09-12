def test_create_mission_runs_complete_control_loop(tmp_path, monkeypatch):
    db_path = tmp_path / "mission.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    monkeypatch.delenv("KALYX_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("KALYX_REQUIRE_OPERATOR_AUTH", "false")

    from fastapi.testclient import TestClient
    from src.api.server import app

    client = TestClient(app)
    response = client.post("/api/missions", json={"mission": "Find the safest high-value sandbox opportunity", "budget": 100})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "COMPLETED"
    assert payload["budget"] == 100
    assert payload["treasury"] == 80
    assert payload["event_count"] >= 10
    assert payload["receipt"] is not None
    assert payload["decision"]["result"] == "APPROVED"
    assert payload["review"]["mission_success"] is True

    org_id = payload["organisation_id"]
    events = client.get(f"/api/organisations/{org_id}/events").json()
    types = [event["event_type"] for event in events]
    assert "MISSION_STARTED" in types
    assert "REPLAN_TRIGGERED" in types
    assert "ACTION_EXECUTED" in types
    assert "MISSION_COMPLETED" in types


def test_mission_rejects_second_persistent_org_without_mixing_treasury(tmp_path, monkeypatch):
    db_path = tmp_path / "mission.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    monkeypatch.delenv("KALYX_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("KALYX_REQUIRE_OPERATOR_AUTH", "false")

    from fastapi.testclient import TestClient
    from src.api.server import app

    client = TestClient(app)
    first = client.post("/api/missions", json={"mission": "First bounded mission", "budget": 100})
    assert first.status_code == 200
    second = client.post("/api/missions", json={"mission": "Second bounded mission", "budget": 100})
    assert second.status_code == 409
    assert "one persistent organisation" in second.json()["detail"]
