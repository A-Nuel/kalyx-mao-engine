import os

from fastapi.testclient import TestClient

from src.api.server import app
from src.identity.models import Membership, MembershipRole, Principal
from src.identity.repository import IdentityRepository
from src.persistence.database import Database
from src.domain.entities import Organisation
from src.persistence.repositories import SqliteRepository


def _seed(db_path: str) -> tuple[str, str, str, str]:
    db = Database(db_path)
    try:
        db.conn.execute("INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)", ("tenant-a", "Tenant A", "2026-09-13T00:00:00"))
        db.conn.execute("INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)", ("tenant-b", "Tenant B", "2026-09-13T00:00:00"))
        identity = IdentityRepository(db)
        identity.save_principal(Principal(id="principal-a", name="Alice"))
        identity.save_principal(Principal(id="principal-b", name="Bob"))
        identity.save_membership(Membership(principal_id="principal-a", tenant_id="tenant-a", role=MembershipRole.OWNER))
        identity.save_membership(Membership(principal_id="principal-b", tenant_id="tenant-b", role=MembershipRole.OWNER))
        for org_id, tenant_id in (("org-a", "tenant-a"), ("org-b", "tenant-b")):
            org = Organisation(id=org_id, tenant_id=tenant_id, mission=f"Mission {org_id}", treasury_balance=100)
            SqliteRepository(db).save_organisation(org)
        db.conn.commit()
        return "org-a", "org-b", "tenant-a", "tenant-b"
    finally:
        db.close()


def test_api_rejects_cross_tenant_organisation_access(tmp_path, monkeypatch):
    db_path = str(tmp_path / "identity.db")
    _seed(db_path)
    monkeypatch.setenv("KALYX_DB", db_path)
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")
    client = TestClient(app)

    allowed = client.get("/api/organisations/org-a", headers={"X-Principal-ID": "principal-a", "X-Tenant-ID": "tenant-a"})
    assert allowed.status_code == 200

    wrong_scope = client.get("/api/organisations/org-b", headers={"X-Principal-ID": "principal-a", "X-Tenant-ID": "tenant-a"})
    assert wrong_scope.status_code == 404

    wrong_membership = client.get("/api/organisations/org-a", headers={"X-Principal-ID": "principal-b", "X-Tenant-ID": "tenant-a"})
    assert wrong_membership.status_code == 403


def test_api_requires_authenticated_scope_when_identity_mode_enabled(tmp_path, monkeypatch):
    db_path = str(tmp_path / "identity.db")
    _seed(db_path)
    monkeypatch.setenv("KALYX_DB", db_path)
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")
    client = TestClient(app)

    response = client.get("/api/organisations/org-a")
    assert response.status_code == 401
