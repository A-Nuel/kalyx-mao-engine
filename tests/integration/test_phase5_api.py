import os

from fastapi.testclient import TestClient


def test_command_centre_health_and_empty_orgs(tmp_path, monkeypatch):
    db_path = tmp_path / "kalyx.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    import src.api.server as server
    client = TestClient(server.app)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/organisations").json() == []


def test_dashboard_is_served():
    from src.api.server import app
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "<title>Kalyx — Command Centre</title>" in response.text
