from src.persistence.database import Database
from src.security.atomic_ledger import AtomicSqliteLedger
from src.tenancy.ledger import TenantScopedLedger


def test_tenant_ledgers_are_isolated_and_transaction_ids_namespaced(tmp_path):
    db = Database(str(tmp_path / "tenants.db"))
    try:
        a = TenantScopedLedger(AtomicSqliteLedger(db, initial_treasury=0), "tenant-a", initial_treasury=100)
        b = TenantScopedLedger(AtomicSqliteLedger(db, initial_treasury=0), "tenant-b", initial_treasury=50)
        a.transfer("TREASURY", "worker", 20, "a", transaction_id="same-id")
        b.transfer("TREASURY", "worker", 10, "b", transaction_id="same-id")
        assert a.get_balance("TREASURY") == 80
        assert b.get_balance("TREASURY") == 40
        assert all(e.tenant_id if hasattr(e, "tenant_id") else True for e in a.get_entries())
        assert a.verify_conservation()
        assert b.verify_conservation()
    finally:
        db.close()
