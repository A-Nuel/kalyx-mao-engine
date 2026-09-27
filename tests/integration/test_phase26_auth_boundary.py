"""Phase 26 authentication fail-closed boundary tests."""

from datetime import datetime

from fastapi.testclient import TestClient

from src.api.server import app
from src.persistence.database import Database


def _seed_org(db_path):
    db = Database(str(db_path))
    db.conn.execute(
        "INSERT INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "org-auth-boundary",
            "tenant-auth-boundary",
            "Boundary test organisation",
            100,
            "EXECUTING",
            datetime.utcnow().isoformat(),
        ),
    )
    db.conn.commit()
    db.close()


def test_identity_auth_fails_closed_when_environment_is_unset(tmp_path, monkeypatch):
    db_path = tmp_path / "auth-boundary.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    monkeypatch.delenv("KALYX_ENV", raising=False)
    monkeypatch.delenv("KALYX_IDENTITY_AUTH", raising=False)

    _seed_org(db_path)
    client = TestClient(app)

    response = client.get(
        "/api/organisations/org-auth-boundary",
        headers={
            "X-Principal-ID": "attacker-controlled-principal",
            "X-Tenant-ID": "tenant-auth-boundary",
        },
    )

    assert response.status_code == 401
    assert "unverified headers are rejected" in response.json()["detail"]


def test_explicit_demo_mode_is_the_only_unauthenticated_opt_out(tmp_path, monkeypatch):
    db_path = tmp_path / "demo-auth-boundary.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    monkeypatch.setenv("KALYX_ENV", "demo")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "false")

    _seed_org(db_path)
    client = TestClient(app)

    response = client.get(
        "/api/organisations/org-auth-boundary",
        headers={"X-Principal-ID": "demo-principal"},
    )

    assert response.status_code == 200
    assert response.json()["organisation"]["id"] == "org-auth-boundary"


def test_unset_identity_auth_cannot_enumerate_organisations(tmp_path, monkeypatch):
    db_path = tmp_path / "enumeration-boundary.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    monkeypatch.delenv("KALYX_ENV", raising=False)
    monkeypatch.delenv("KALYX_IDENTITY_AUTH", raising=False)

    _seed_org(db_path)
    client = TestClient(app)

    response = client.get("/api/organisations")

    assert response.status_code == 400
    assert response.json()["detail"] == "Tenant scope required"
