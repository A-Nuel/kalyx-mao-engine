from datetime import datetime

from fastapi.testclient import TestClient


def test_pause_and_resume_are_audited(tmp_path, monkeypatch):
    db_path = tmp_path / "operator.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))

    from src.persistence.database import Database
    db = Database(str(db_path))
    db.conn.execute(
        "INSERT INTO organisations (id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?)",
        ("org-test", "Test mission", 100, "EXECUTING", datetime.utcnow().isoformat()),
    )
    db.conn.commit()
    db.close()

    from src.api.server import app
    client = TestClient(app)
    assert client.post("/api/organisations/org-test/pause").json()["state"] == "PAUSED"
    assert client.post("/api/organisations/org-test/resume").json()["state"] == "EXECUTING"
    events = client.get("/api/organisations/org-test/events").json()
    assert [e["event_type"] for e in events[-2:]] == ["ORG_PAUSED", "ORG_RESUMED"]
