import threading

import pytest

from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, AgentStatus
from src.domain.exceptions import InsufficientCreditsError, UnauthorizedActionError
from src.persistence.database import Database
from src.persistence.repositories import SqliteLedger
from src.security.atomic_ledger import AtomicSqliteLedger
from src.security.capabilities import enforce_agent_capability
from src.security.idempotency import IdempotencyConflict, IdempotencyInProgress, SQLiteIdempotencyJournal


def test_base_sqlite_ledger_prevents_concurrent_overspend(tmp_path):
    db_path = str(tmp_path / "base-money.db")
    seed = Database(db_path)
    SqliteLedger(seed, initial_treasury=100)
    seed.close()

    dbs = [Database(db_path) for _ in range(10)]
    successes, failures, errors = [], [], []
    lock = threading.Lock()

    def spend(i):
        db = dbs[i]
        try:
            ledger = SqliteLedger(db, initial_treasury=0)
            try:
                ledger.transfer("TREASURY", f"worker-{i}", 20, "base concurrency test", f"base-tx-{i}")
                with lock:
                    successes.append(i)
            except InsufficientCreditsError:
                with lock:
                    failures.append(i)
            except Exception as exc:
                with lock:
                    errors.append(exc)
        except Exception as exc:
            with lock:
                errors.append(exc)

    try:
        threads = [threading.Thread(target=spend, args=(i,)) for i in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors, errors
        assert len(successes) == 5
        assert len(failures) == 5
        ledger = SqliteLedger(dbs[0], initial_treasury=0)
        assert ledger.get_balance("TREASURY") == 0
        assert ledger.verify_conservation()
    finally:
        for db in dbs:
            db.close()


def test_atomic_ledger_prevents_concurrent_overspend(tmp_path):
    db_path = str(tmp_path / "money.db")
    seed = Database(db_path)
    AtomicSqliteLedger(seed, initial_treasury=100)
    seed.close()
    dbs = [Database(db_path) for _ in range(10)]
    successes, failures, errors = [], [], []
    lock = threading.Lock()

    def spend(i):
        db = dbs[i]
        try:
            ledger = AtomicSqliteLedger(db, initial_treasury=0)
            try:
                ledger.transfer("TREASURY", f"worker-{i}", 20, "concurrency test", f"tx-{i}")
                with lock:
                    successes.append(i)
            except InsufficientCreditsError:
                with lock:
                    failures.append(i)
            except Exception as exc:
                with lock:
                    errors.append(exc)
        except Exception as exc:
            with lock:
                errors.append(exc)

    try:
        threads = [threading.Thread(target=spend, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert len(successes) == 5
        assert len(failures) == 5
        ledger = AtomicSqliteLedger(dbs[0], initial_treasury=0)
        assert ledger.get_balance("TREASURY") == 0
        assert ledger.verify_conservation()
    finally:
        for db in dbs:
            db.close()


def test_idempotency_key_is_bound_to_content(tmp_path):
    db = Database(str(tmp_path / "idempotency.db"))
    try:
        journal = SQLiteIdempotencyJournal(db.conn)
        assert journal.begin("op-1", "fingerprint-a") is None
        journal.succeed("op-1", "fingerprint-a", "receipt-1")
        assert journal.begin("op-1", "fingerprint-a") == "receipt-1"
        with pytest.raises(IdempotencyConflict):
            journal.begin("op-1", "fingerprint-b")
    finally:
        db.close()


def test_idempotency_ownership_is_serialized_under_concurrency(tmp_path):
    db_path = str(tmp_path / "idempotency-concurrency.db")
    seed = Database(db_path)
    SQLiteIdempotencyJournal(seed.conn)
    seed.close()

    dbs = [Database(db_path) for _ in range(2)]
    barrier = threading.Barrier(2)
    results = []
    lock = threading.Lock()

    def begin(i):
        journal = SQLiteIdempotencyJournal(dbs[i].conn)
        barrier.wait()
        try:
            value = journal.begin("shared-operation", "same-fingerprint")
            outcome = ("owned", value)
        except IdempotencyInProgress:
            outcome = ("in-progress", None)
        with lock:
            results.append(outcome)

    try:
        threads = [threading.Thread(target=begin, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(results) == [("in-progress", None), ("owned", None)]
        row = dbs[0].conn.execute(
            "SELECT state, fingerprint FROM idempotency_operations WHERE operation_key = ?",
            ("shared-operation",),
        ).fetchone()
        assert row["state"] == "started"
        assert row["fingerprint"] == "same-fingerprint"
    finally:
        for db in dbs:
            db.close()


def _proposal(action_type=ActionType.INTERNAL_ANALYSIS, credits=0):
    return ActionProposal(
        id="proposal-1", task_id="task-1", proposing_agent_id="agent-1", action_type=action_type,
        target="sandbox://target", requested_credits=credits, expected_value_score=0.5,
        risk_assessment="low", rationale="security test",
    )


def test_capability_boundary_rejects_unauthorized_action():
    org = Organisation(id="org-1", mission="test")
    org.agents["agent-1"] = AgentRecord(id="agent-1", role=AgentRole.RESEARCHER, allowed_action_types=[ActionType.INTERNAL_ANALYSIS], authority_ceiling=10)
    with pytest.raises(UnauthorizedActionError):
        enforce_agent_capability(_proposal(ActionType.EXTERNAL_API_CALL, 1), org)


def test_capability_boundary_rejects_inactive_agent():
    org = Organisation(id="org-1", mission="test")
    org.agents["agent-1"] = AgentRecord(id="agent-1", role=AgentRole.RESEARCHER, status=AgentStatus.SUSPENDED, allowed_action_types=[ActionType.INTERNAL_ANALYSIS])
    with pytest.raises(UnauthorizedActionError):
        enforce_agent_capability(_proposal(), org)


def test_capability_boundary_enforces_authority_ceiling():
    org = Organisation(id="org-1", mission="test")
    org.agents["agent-1"] = AgentRecord(id="agent-1", role=AgentRole.FINANCIAL_ANALYST, allowed_action_types=[ActionType.SIMULATED_ALLOCATION], authority_ceiling=5)
    with pytest.raises(UnauthorizedActionError):
        enforce_agent_capability(_proposal(ActionType.SIMULATED_ALLOCATION, 6), org)
