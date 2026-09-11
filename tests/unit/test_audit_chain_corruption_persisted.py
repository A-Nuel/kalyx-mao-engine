import pytest
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore
from src.domain.exceptions import TamperedAuditLogError

def test_startup_blocks_corrupted_persisted_audit_chain(tmp_path):
    db_file = str(tmp_path / "corrupt_audit.db")
    db1 = Database(db_file)
    store1 = SqliteEventStore(db1, verify_on_startup=True)

    store1.append_event("OPERATOR", "ORG_CREATED", "org-01", {"budget": 100})
    store1.append_event("CEO", "PLAN_CREATED", "org-01", {"tasks": 3})
    store1.append_event("EXECUTOR", "ACTION_EXECUTED", "exec-01", {"cost": 20})
    db1.close()

    # Tamper with the SQLite file directly (simulate malicious direct DB modification)
    db_tamper = Database(db_file)
    with db_tamper.conn:
        db_tamper.conn.execute(
            "UPDATE audit_events SET payload = ? WHERE sequence_id = 2",
            ('{"tasks": 999, "malicious": true}',)
        )
    db_tamper.close()

    # Re-open in new process: MUST raise TamperedAuditLogError on startup!
    db2 = Database(db_file)
    with pytest.raises(TamperedAuditLogError) as exc:
        SqliteEventStore(db2, verify_on_startup=True)

    assert "Audit log corruption detected on startup" in str(exc.value)
    db2.close()
