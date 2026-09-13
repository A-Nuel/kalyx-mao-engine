from src.domain.entities import Organisation
from src.domain.enums import OrgState
from src.persistence.database import Database
from src.persistence.repositories import SqliteRepository


def test_organisation_tenant_id_round_trips_through_repository():
    db = Database(":memory:")
    repo = SqliteRepository(db)
    original = Organisation(
        id="org-tenant-roundtrip",
        tenant_id="tenant-a",
        mission="Tenant persistence",
        treasury_balance=100,
        state=OrgState.INITIALIZING,
    )

    repo.save_organisation(original)
    loaded = repo.load_organisation(original.id)

    assert loaded is not None
    assert loaded.tenant_id == "tenant-a"


def test_organisation_update_cannot_silently_reassign_tenant():
    db = Database(":memory:")
    repo = SqliteRepository(db)
    original = Organisation(id="org-tenant-immutable", tenant_id="tenant-a", mission="Original")
    repo.save_organisation(original)

    changed = Organisation(id=original.id, tenant_id="tenant-b", mission="Updated")
    repo.save_organisation(changed)

    row = db.conn.execute("SELECT tenant_id, mission FROM organisations WHERE id = ?", (original.id,)).fetchone()
    assert row["tenant_id"] == "tenant-a"
    assert row["mission"] == "Updated"
