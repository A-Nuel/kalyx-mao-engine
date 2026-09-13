import os

from fastapi.testclient import TestClient

from src.api.server import app
from src.domain.entities import Organisation
from src.identity.models import Membership, MembershipRole, Principal
from src.identity.repository import IdentityRepository
from src.persistence.database import Database
from src.persistence.repositories import SqliteRepository
from src.tenancy.ledger import TenantScopedLedger
from src.tenancy.organisation_ledger import OrganisationScopedLedger
from src.persistence.repositories import SqliteLedger


def _seed(db_path: str) -> None:
    db = Database(db_path)
    try:
        db.conn.execute("INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)", ("tenant-a", "Tenant A", "2026-09-13T00:00:00"))
        identity = IdentityRepository(db)
        identity.save_principal(Principal(id="principal-a", name="Alice"))
        identity.save_membership(Membership(principal_id="principal-a", tenant_id="tenant-a", role=MembershipRole.OWNER))
        for org_id, balance in (("org-a", 100), ("org-b", 250)):
            org = Organisation(id=org_id, tenant_id="tenant-a", mission=f"Mission {org_id}", treasury_balance=balance)
            SqliteRepository(db).save_organisation(org)
            tenant = TenantScopedLedger(SqliteLedger(db, initial_treasury=0), "tenant-a")
            OrganisationScopedLedger(tenant, org_id, initial_treasury=balance)
        db.conn.commit()
    finally:
        db.close()


def test_api_ledger_is_organisation_scoped(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ledger-isolation.db")
    _seed(db_path)
    monkeypatch.setenv("KALYX_DB", db_path)
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")
    client = TestClient(app)
    headers = {"X-Principal-ID": "principal-a", "X-Tenant-ID": "tenant-a"}

    a = client.get("/api/organisations/org-a/ledger", headers=headers)
    b = client.get("/api/organisations/org-b/ledger", headers=headers)
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json()["treasury"] == 100
    assert b.json()["treasury"] == 250
    assert a.json()["treasury"] != b.json()["treasury"]

    org_a = client.get("/api/organisations/org-a", headers=headers)
    org_b = client.get("/api/organisations/org-b", headers=headers)
    assert org_a.json()["treasury"] == 100
    assert org_b.json()["treasury"] == 250
