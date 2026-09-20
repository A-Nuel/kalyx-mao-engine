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


def test_verify_integrity_tolerates_timezone_aware_timestamp_round_trip(tmp_path):
    """Regression test for a 4th distinct production crash found via the
    live judge demo: "Event hash mismatch at sequence N" /
    TamperedAuditLogError, firing on every single fresh Postgres
    deployment, 100% reproducibly.

    Root cause: append_event() computes the event hash from
    datetime.utcnow().isoformat() -- a naive (no tzinfo) string. SQLite
    stores this as TEXT and returns the identical string on read, so the
    hash always matched. But when the same underlying value round-trips
    through a Postgres TIMESTAMPTZ column, psycopg returns a
    timezone-AWARE datetime (Postgres attaches the session/UTC offset on
    read even though a naive value was written) -- and datetime.isoformat()
    on a naive vs. an equivalent timezone-aware value produces two
    different strings ("...123456" vs "...123456+00:00"), which hash
    differently even though nothing was ever tampered with.

    Fixed via canonical_timestamp_iso() (src/domain/events.py), which
    strips tzinfo before formatting on both the write path
    (append_event) and the verify path (verify_integrity), so the two
    sides always agree regardless of which backend round-tripped the
    value.

    This test simulates the exact Postgres round-trip effect directly
    (an event whose in-memory timestamp becomes timezone-aware between
    write and verify) without needing a live Postgres connection, since
    the bug is really about datetime.isoformat()'s string shape, not
    about Postgres specifically.
    """
    from datetime import timezone

    db_file = str(tmp_path / "tz_roundtrip.db")
    db = Database(db_file)
    store = SqliteEventStore(db, verify_on_startup=False)
    store.append_event("OPERATOR", "ORG_CREATED", "org-01", {"budget": 100})

    # Simulate the Postgres round-trip effect: get_events() would normally
    # return the identical naive datetime SQLite stored, so monkeypatch it
    # to return a timezone-aware version of the same instant instead --
    # exactly what psycopg does for a TIMESTAMPTZ column.
    original_get_events = SqliteEventStore.get_events

    def get_events_as_if_postgres(self, *args, **kwargs):
        events = original_get_events(self, *args, **kwargs)
        for event in events:
            if event.timestamp.tzinfo is None:
                event.timestamp = event.timestamp.replace(tzinfo=timezone.utc)
        return events

    SqliteEventStore.get_events = get_events_as_if_postgres
    try:
        valid, err = store.verify_integrity()
        assert valid is True, f"tz-aware round-trip incorrectly flagged as corruption: {err}"
    finally:
        SqliteEventStore.get_events = original_get_events
        db.close()


def test_verify_integrity_still_catches_genuine_hash_tampering_after_tz_fix(tmp_path):
    """Companion to the tz round-trip fix above: confirms the fix didn't
    accidentally make verify_integrity() permissive in general -- a
    genuinely tampered event_hash (not just a timestamp representation
    difference) must still be caught."""
    db_file = str(tmp_path / "genuine_tamper.db")
    db1 = Database(db_file)
    store1 = SqliteEventStore(db1, verify_on_startup=True)
    store1.append_event("OPERATOR", "ORG_CREATED", "org-01", {"budget": 100})
    db1.close()

    db_tamper = Database(db_file)
    with db_tamper.conn:
        db_tamper.conn.execute(
            "UPDATE audit_events SET event_hash = ? WHERE sequence_id = 1",
            ("0" * 64,),
        )
    db_tamper.close()

    db2 = Database(db_file)
    with pytest.raises(TamperedAuditLogError):
        SqliteEventStore(db2, verify_on_startup=True)
    db2.close()
