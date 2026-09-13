from src.persistence.database import Database
from src.persistence.repositories import SqliteLedger
from src.tenancy.ledger import TenantScopedLedger
from src.tenancy.organisation_ledger import OrganisationScopedLedger


def test_organisation_treasuries_are_isolated_within_same_tenant():
    db = Database(":memory:")
    try:
        tenant = TenantScopedLedger(SqliteLedger(db, initial_treasury=0), "tenant-a")
        org_a = OrganisationScopedLedger(tenant, "org-a", initial_treasury=100)
        org_b = OrganisationScopedLedger(tenant, "org-b", initial_treasury=250)
        assert org_a.get_balance("TREASURY") == 100
        assert org_b.get_balance("TREASURY") == 250
        org_a.transfer("TREASURY", "ESCROW", 20, "org-a reservation", "tx-org-a-1")
        assert org_a.get_balance("TREASURY") == 80
        assert org_a.get_balance("ESCROW") == 20
        assert org_b.get_balance("TREASURY") == 250
        assert org_b.get_balance("ESCROW") == 0
        assert org_a.verify_conservation()
        assert org_b.verify_conservation()
    finally:
        db.close()


def test_organisation_ledger_cannot_address_another_org_account():
    db = Database(":memory:")
    try:
        tenant = TenantScopedLedger(SqliteLedger(db, initial_treasury=0), "tenant-a")
        org_a = OrganisationScopedLedger(tenant, "org-a", initial_treasury=100)
        OrganisationScopedLedger(tenant, "org-b", initial_treasury=100)
        assert org_a.get_balance("org-b:TREASURY") == 0
    finally:
        db.close()
