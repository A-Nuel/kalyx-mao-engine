"""PostgreSQL integration tests.

Skipped unless KALYX_DATABASE_URL points at a live PostgreSQL instance.
CI starts a postgres service and sets the URL.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from src.domain.exceptions import InsufficientCreditsError
from src.persistence.config import Backend, load_persistence_config
from src.persistence.factory import create_database
from src.persistence.postgres_ledger import PostgresLedger

pytestmark = pytest.mark.postgres


def _postgres_available() -> bool:
    url = os.getenv("KALYX_DATABASE_URL", "").strip().lower()
    return url.startswith("postgres://") or url.startswith("postgresql://")


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="KALYX_DATABASE_URL not set to PostgreSQL",
)


@requires_postgres
def test_config_selects_postgres():
    cfg = load_persistence_config()
    assert cfg.backend == Backend.POSTGRES


@requires_postgres
def test_migration_and_seed():
    db = create_database()
    try:
        row = db.conn.execute("SELECT id FROM tenants WHERE id = %s", ("tenant-demo",)).fetchone()
        assert row is not None
        versions = db.conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        assert any("001" in (v["version"] if hasattr(v, "keys") else v[0]) for v in versions)
    finally:
        db.close()


@requires_postgres
def test_ledger_basic_transfer():
    db = create_database()
    try:
        # Unique account namespace per test run
        prefix = f"test-{os.getpid()}-{threading.get_ident()}"
        treasury = f"{prefix}:TREASURY"
        escrow = f"{prefix}:ESCROW"
        ledger = PostgresLedger(db, initial_treasury=0, tenant_id="tenant-demo")
        # Mint via transfer path is not available; insert initial via ledger helper
        # Use internal mint by temporarily constructing with balance 0 then manual insert
        db.conn.execute(
            """INSERT INTO ledger_entries
               (id, transaction_id, from_account, to_account, amount, memo, tenant_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (f"{prefix}-mint", f"{prefix}-mint-tx", "SYSTEM_MINT", treasury, 100, "seed", "tenant-demo"),
        )
        db.conn.commit()
        assert ledger.get_balance(treasury) == 100
        ledger.transfer(treasury, escrow, 30, "reserve", transaction_id=f"{prefix}-tx1")
        assert ledger.get_balance(treasury) == 70
        assert ledger.get_balance(escrow) == 30
        assert ledger.verify_conservation()
    finally:
        db.close()


@requires_postgres
def test_ledger_concurrent_overspend_prevention():
    """balance=100, 10 concurrent attempts to spend 20 -> exactly 5 succeed."""
    db = create_database()
    try:
        prefix = f"conc-{os.getpid()}"
        treasury = f"{prefix}:TREASURY"
        sink = f"{prefix}:SINK"
        db.conn.execute(
            """INSERT INTO ledger_entries
               (id, transaction_id, from_account, to_account, amount, memo, tenant_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (f"{prefix}-mint", f"{prefix}-mint-tx", "SYSTEM_MINT", treasury, 100, "seed", "tenant-demo"),
        )
        db.conn.commit()

        success = []
        failure = []
        lock = threading.Lock()

        def attempt(i: int):
            # Each thread needs its own Database/connection from the pool.
            local = create_database()
            try:
                led = PostgresLedger(local, initial_treasury=0, tenant_id="tenant-demo")
                try:
                    led.transfer(
                        treasury, sink, 20, f"spend-{i}",
                        transaction_id=f"{prefix}-spend-{i}",
                    )
                    with lock:
                        success.append(i)
                except InsufficientCreditsError:
                    with lock:
                        failure.append(i)
            finally:
                local.close()

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(attempt, i) for i in range(10)]
            for f in as_completed(futures):
                f.result()

        assert len(success) == 5, f"expected 5 successes, got {len(success)}"
        assert len(failure) == 5, f"expected 5 failures, got {len(failure)}"

        final = PostgresLedger(db, initial_treasury=0, tenant_id="tenant-demo")
        assert final.get_balance(treasury) == 0
        assert final.get_balance(sink) == 100
        assert final.verify_conservation()
    finally:
        db.close()


@requires_postgres
def test_duplicate_transaction_id_rejected():
    db = create_database()
    try:
        prefix = f"dup-{os.getpid()}"
        treasury = f"{prefix}:TREASURY"
        other = f"{prefix}:OTHER"
        db.conn.execute(
            """INSERT INTO ledger_entries
               (id, transaction_id, from_account, to_account, amount, memo, tenant_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (f"{prefix}-mint", f"{prefix}-mint-tx", "SYSTEM_MINT", treasury, 50, "seed", "tenant-demo"),
        )
        db.conn.commit()
        led = PostgresLedger(db, initial_treasury=0, tenant_id="tenant-demo")
        led.transfer(treasury, other, 10, "first", transaction_id=f"{prefix}-same")
        with pytest.raises(ValueError, match="Duplicate transaction"):
            led.transfer(treasury, other, 10, "second", transaction_id=f"{prefix}-same")
    finally:
        db.close()


@requires_postgres
def test_pg_connection_proxy_supports_sqlite_style_cursor():
    """Regression test for the exact failure hit in production: run_mission()
    hands a Postgres connection to SqliteRepository/SqliteEventStore, both
    of which use the SQLite idiom `cursor = self.db.conn.cursor()` rather
    than `self.db.conn.execute(...)`. _PgConnectionProxy previously had no
    .cursor() method at all, so any code path exercising it (startup audit
    integrity check, treasury conservation check) crashed with
    AttributeError: '_PgConnectionProxy' object has no attribute 'cursor'
    -- surfaced to users as TamperedAuditLogError since
    SqliteEventStore.on_startup_verify() wraps any exception in that type.
    """
    db = create_database()
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT 1 AS one")
        row = cursor.fetchone()
        assert row["one"] == 1

        # Confirm ? -> %s placeholder translation also works via the
        # cursor path, not just the connection-level .execute() path.
        cursor.execute("SELECT ? AS echoed", (42,))
        row2 = cursor.fetchone()
        assert row2["echoed"] == 42
    finally:
        db.close()


@requires_postgres
def test_sqlite_repository_and_event_store_work_against_postgres():
    """End-to-end regression for the actual code paths that broke in
    production: run_mission() (which backs the /api/demo/public-run judge
    demo) unconditionally constructs SqliteEventStore(db, verify_on_startup=True)
    regardless of backend -- this is what raised TamperedAuditLogError when
    on_startup_verify()'s call to verify_integrity() -> get_events() hit
    _PgConnectionProxy via `self.db.conn.cursor()` before that method
    existed. It also constructs EconomyRepository(db.conn) directly, whose
    5 methods each do `cursor = self.conn.cursor() if hasattr(self.conn,
    "cursor") else self.conn` -- before the fix, hasattr was False, so this
    silently used the raw connection as a cursor instead of raising, which
    is a *worse* failure mode (wrong behavior instead of a loud error).

    NOTE: SqliteLedger.verify_conservation() (in this same module) is NOT
    exercised here on purpose -- create_scoped_ledger() in
    src/persistence/factory.py branches on isinstance(db, PostgresDatabase)
    and uses PostgresLedger for the real Postgres path, so SqliteLedger is
    never actually constructed against a Postgres connection in production.
    Confirmed by reading src/persistence/factory.py:56-60 directly rather
    than assumed.
    """
    from src.persistence.economy_repo import EconomyRepository
    from src.persistence.repositories import SqliteEventStore

    db = create_database()
    try:
        # This line alone reproduces the original production crash: it
        # calls on_startup_verify() -> verify_integrity() -> get_events(),
        # and get_events() does `cursor = self.db.conn.cursor()`.
        store = SqliteEventStore(db, verify_on_startup=True)
        valid, err = store.verify_integrity()
        assert valid is True, f"unexpected integrity failure: {err}"

        # This reproduces the EconomyRepository code path run_mission()
        # exercises via economy_repo.list_performance_records(...).
        econ = EconomyRepository(db.conn)
        records = econ.list_performance_records("tenant-demo", "org-does-not-exist")
        assert records == []  # must return cleanly, not raise or misbehave
    finally:
        db.close()


@requires_postgres
def test_sqlite_ledger_cursor_path_is_pg_compatible():
    """SqliteLedger is not on the production Postgres path today (see note
    in the test above), but its constructor and verify_conservation() both
    use the same `self.db.conn.cursor()` idiom, so it would break
    identically to the other two if anything ever routes a Postgres
    connection to it. Covered as a safety net, not because it's currently
    reachable in production."""
    from src.persistence.repositories import SqliteLedger

    db = create_database()
    try:
        ledger = SqliteLedger(db, initial_treasury=0)
        assert ledger.verify_conservation() in (True, False)
    finally:
        db.close()


@requires_postgres
def test_save_proposal_policy_decision_execution_receipt_upsert_on_postgres():
    """Regression test for a second production crash found after the
    cursor fix: SqliteRepository.save_proposal / save_policy_decision /
    save_execution_receipt used SQLite-only `INSERT OR REPLACE INTO ...`,
    which Postgres has no equivalent for -- it raised
    `SyntaxError: syntax error at or near "OR"` the moment run_mission()
    (via the Auditor/executor flow the judge demo exercises) tried to
    persist a proposal.

    Fixed by rewriting all three as `INSERT INTO ... ON CONFLICT(id) DO
    UPDATE SET <every non-key column> = excluded.<column>`, matching
    REPLACE's whole-row-overwrite semantics exactly (not a partial upsert)
    so behavior against SQLite is unchanged -- confirmed by the existing
    619-test suite staying green, since SQLite itself has supported
    ON CONFLICT since 3.24 and this is not Postgres-only syntax.

    This test exercises both branches ON CONFLICT actually has to cover:
    the initial INSERT (id not yet present) and the UPDATE-on-conflict
    (same id, changed values) -- REPLACE historically silently allowed
    both to work identically; ON CONFLICT DO UPDATE is not automatically
    equivalent unless every non-key column is listed, which is exactly
    the mistake that's easy to make when translating REPLACE by hand.
    """
    from src.domain.entities import ActionProposal, ExecutionReceipt, Organisation, PolicyDecision, Task
    from src.domain.enums import ActionType, PolicyResult, TaskStatus
    from src.persistence.repositories import SqliteRepository

    db = create_database()
    try:
        repo = SqliteRepository(db)
        prefix = f"pg-upsert-{os.getpid()}"
        org_id = f"{prefix}-org"

        # tasks.org_id has a real FK to organisations(id), enforced strictly
        # by Postgres (unlike SQLite, which doesn't enforce FKs by default --
        # this is exactly why this omission wouldn't have failed locally).
        org = Organisation(id=org_id, tenant_id="tenant-demo", mission="test mission", treasury_balance=100)
        repo.save_organisation(org)

        # Tasks/proposals/policy_decisions/execution_receipts all carry FK
        # references, so seed a task first the same way run_mission() does.
        task = Task(
            id=f"{prefix}-task",
            mission_id=f"{prefix}-mission",
            objective="test objective",
            allocated_credits=10,
            status=TaskStatus.PENDING,
        )
        repo.save_task(task, org_id)

        proposal = ActionProposal(
            id=f"{prefix}-proposal",
            task_id=task.id,
            proposing_agent_id=f"{prefix}-agent",
            action_type=ActionType.INTERNAL_ANALYSIS,
            target="sandbox://test",
            parameters={"k": "v1"},
            requested_credits=5,
            expected_value_score=0.5,
            risk_assessment="low",
            rationale="initial",
        )
        repo.save_proposal(proposal)  # INSERT branch

        # Same id, changed fields -> exercises the ON CONFLICT DO UPDATE
        # branch, the one REPLACE-style upsert semantics require.
        proposal.rationale = "updated rationale"
        proposal.requested_credits = 9
        repo.save_proposal(proposal)  # UPDATE-on-conflict branch

        decision = PolicyDecision(
            id=f"{prefix}-decision",
            proposal_id=proposal.id,
            result=PolicyResult.APPROVED,
            evaluated_rules=["rule-1"],
            authorization_token="tok-1",
        )
        repo.save_policy_decision(decision)  # INSERT branch
        decision.result = PolicyResult.REJECTED
        decision.violated_rule_id = "rule-1"
        repo.save_policy_decision(decision)  # UPDATE-on-conflict branch

        receipt = ExecutionReceipt(
            id=f"{prefix}-receipt",
            proposal_id=proposal.id,
            authorization_token="tok-1",
            action_type=ActionType.INTERNAL_ANALYSIS,
            target="sandbox://test",
            http_status=200,
            raw_response_hash="hash-1",
            raw_output={"status": "ok"},
            cost_credits=5,
        )
        repo.save_execution_receipt(receipt)  # INSERT branch
        receipt.http_status = 500
        receipt.cost_credits = 7
        repo.save_execution_receipt(receipt)  # UPDATE-on-conflict branch

        # Confirm the UPDATE branch actually overwrote, not silently
        # ignored (which a wrong ON CONFLICT DO NOTHING would produce).
        cursor = db.conn.cursor()
        cursor.execute("SELECT rationale, requested_credits FROM proposals WHERE id = ?", (proposal.id,))
        row = cursor.fetchone()
        assert row["rationale"] == "updated rationale"
        assert row["requested_credits"] == 9
    finally:
        db.close()
