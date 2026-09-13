from src.persistence.database import Database
from src.persistence.repositories import SqliteLedger
from src.tenancy.ledger import TenantScopedLedger


def test_tenant_scoped_ledgers_cannot_see_each_others_balances():
    db = Database(":memory:")
    try:
        base = SqliteLedger(db, initial_treasury=0)
        alpha = TenantScopedLedger(base, "tenant-alpha", initial_treasury=100)
        beta = TenantScopedLedger(base, "tenant-beta", initial_treasury=70)

        assert alpha.get_balance("TREASURY") == 100
        assert beta.get_balance("TREASURY") == 70

        alpha.transfer("TREASURY", "EXTERNAL_SINK", 20, "alpha execution")

        assert alpha.get_balance("TREASURY") == 80
        assert beta.get_balance("TREASURY") == 70
        assert alpha.verify_conservation() is True
        assert beta.verify_conservation() is True

        alpha_entries = alpha.get_entries()
        beta_entries = beta.get_entries()
        assert all(e.from_account != "tenant-beta:TREASURY" for e in alpha_entries)
        assert all(e.from_account != "tenant-alpha:TREASURY" for e in beta_entries)
    finally:
        db.close()
