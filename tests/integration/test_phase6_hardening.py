from datetime import datetime

from fastapi.testclient import TestClient


def _seed_org(db_path):
    from src.persistence.database import Database

    db = Database(str(db_path))
    db.conn.execute(
        "INSERT INTO organisations (id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?)",
        ("org-phase6", "Phase 6 test mission", 100, "EXECUTING", datetime.utcnow().isoformat()),
    )
    db.conn.commit()
    db.close()


def test_production_operator_controls_require_key(tmp_path, monkeypatch):
    db_path = tmp_path / "operator-auth.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.setenv("KALYX_OPERATOR_KEY", "test-secret")

    _seed_org(db_path)

    from src.api.server import app
    client = TestClient(app)
    assert client.post("/api/organisations/org-phase6/pause").status_code == 401
    assert client.post(
        "/api/organisations/org-phase6/pause", headers={"X-API-Key": "test-secret"}
    ).json()["state"] == "PAUSED"


def test_audit_endpoint_reports_corruption_instead_of_raising(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))

    from src.persistence.database import Database

    db = Database(str(db_path))
    db.conn.execute(
        "INSERT INTO organisations (id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?)",
        ("org-audit", "Audit test mission", 100, "EXECUTING", datetime.utcnow().isoformat()),
    )
    db.conn.execute(
        """
        INSERT INTO audit_events
        (sequence_id, timestamp, actor_id, event_type, entity_id, payload, payload_hash, previous_event_hash, event_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, datetime.utcnow().isoformat(), "tester", "TEST", "org-audit", "{}", "bad", "0" * 64, "bad"),
    )
    db.conn.commit()
    db.close()

    from src.api.server import app
    client = TestClient(app)
    response = client.get("/api/organisations/org-audit/audit")
    assert response.status_code == 200
    body = response.json()
    assert body["chain_valid"] is False
    assert body["chain_error"]


def test_security_headers_are_present():
    from src.api.server import app

    response = TestClient(app).get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
