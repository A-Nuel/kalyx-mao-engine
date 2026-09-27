"""Phase 26 production authorization and surface-hardening tests."""

from fastapi.testclient import TestClient

from src.api.identity_auth import create_identity_token
from src.api.server import app
from src.domain.entities import Organisation
from src.identity.models import Membership, MembershipRole, Principal
from src.identity.repository import IdentityRepository
from src.persistence.database import Database
from src.persistence.repositories import SqliteRepository
from src.api.bootstrap import validate_production_config


def _seed_orgs(db_path: str) -> None:
    db = Database(db_path)
    try:
        db.conn.execute(
            "INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            ("tenant-a", "Tenant A", "2026-09-27T00:00:00"),
        )
        db.conn.execute(
            "INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            ("tenant-b", "Tenant B", "2026-09-27T00:00:00"),
        )
        identity = IdentityRepository(db)
        identity.save_principal(Principal(id="principal-a", name="Alice"))
        identity.save_principal(Principal(id="principal-b", name="Bob"))
        identity.save_membership(
            Membership(principal_id="principal-a", tenant_id="tenant-a", role=MembershipRole.OWNER)
        )
        identity.save_membership(
            Membership(principal_id="principal-b", tenant_id="tenant-b", role=MembershipRole.OWNER)
        )
        repo = SqliteRepository(db)
        repo.save_organisation(
            Organisation(id="org-a", tenant_id="tenant-a", mission="A", treasury_balance=100)
        )
        repo.save_organisation(
            Organisation(id="org-b", tenant_id="tenant-b", mission="B", treasury_balance=100)
        )
        db.conn.commit()
    finally:
        db.close()


def test_production_config_rejects_public_demo(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.setenv("KALYX_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("KALYX_POLICY_SECRET", "x" * 32)
    monkeypatch.setenv("KALYX_OPERATOR_KEY", "operator-key")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "production")
    monkeypatch.setenv("KALYX_PUBLIC_DEMO", "true")

    try:
        validate_production_config()
        assert False, "production config must reject public demo mode"
    except RuntimeError as exc:
        assert "KALYX_PUBLIC_DEMO" in str(exc)


def test_production_config_rejects_simulated_orbio_fallback(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.setenv("KALYX_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("KALYX_POLICY_SECRET", "x" * 32)
    monkeypatch.setenv("KALYX_OPERATOR_KEY", "operator-key")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "production")
    monkeypatch.setenv("KALYX_ORBIO_ALLOW_SIMULATED_FALLBACK", "true")

    try:
        validate_production_config()
        assert False, "production config must reject simulated Orbio fallback"
    except RuntimeError as exc:
        assert "KALYX_ORBIO_ALLOW_SIMULATED_FALLBACK" in str(exc)


def test_production_config_rejects_process_local_blockchain_signing(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.setenv("KALYX_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("KALYX_POLICY_SECRET", "x" * 32)
    monkeypatch.setenv("KALYX_OPERATOR_KEY", "operator-key")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "production")
    monkeypatch.setenv("KALYX_BLOCKCHAIN_ENABLED", "true")

    try:
        validate_production_config()
        assert False, "production control-plane config must reject process-local blockchain signing"
    except RuntimeError as exc:
        assert "KALYX_BLOCKCHAIN_ENABLED" in str(exc)


def test_organisation_listing_is_tenant_scoped_under_identity_auth(tmp_path, monkeypatch):
    db_path = str(tmp_path / "phase26-auth.db")
    _seed_orgs(db_path)

    monkeypatch.setenv("KALYX_DB", db_path)
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "production")
    secret = "phase26-production-secret-" + "x" * 16
    monkeypatch.setenv("KALYX_POLICY_SECRET", secret)

    client = TestClient(app)

    missing_scope = client.get("/api/organisations")
    assert missing_scope.status_code == 400

    missing_identity = client.get(
        "/api/organisations",
        headers={"X-Tenant-ID": "tenant-a"},
    )
    assert missing_identity.status_code == 401

    token = create_identity_token("principal-a", secret, ttl_seconds=300)
    response = client.get(
        "/api/organisations",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": "tenant-a",
        },
    )
    assert response.status_code == 200
    assert [org["id"] for org in response.json()] == ["org-a"]

    cross_tenant = client.get(
        "/api/organisations",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": "tenant-b",
        },
    )
    assert cross_tenant.status_code == 403


def test_economic_experiment_requires_operator_credential(monkeypatch):
    monkeypatch.setenv("KALYX_OPERATOR_KEY", "phase26-operator-key")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "false")

    client = TestClient(app)
    response = client.post("/api/experiments/run?num_rounds=1")

    assert response.status_code == 401
    assert "Operator authentication required" in response.json()["detail"]
