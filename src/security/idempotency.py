"""Durable idempotency journal for externally consequential operations."""

import sqlite3
from datetime import datetime
from typing import Optional


class IdempotencyConflict(RuntimeError):
    """Raised when an operation key is reused with different content."""


class IdempotencyInProgress(RuntimeError):
    """Raised when another worker currently owns the idempotency operation."""


class SQLiteIdempotencyJournal:
    """Crash-safe operation journal backed by the same SQLite database.

    Keys are bound to an operation fingerprint. A retry with the same key and
    fingerprint is safe; reusing a key for different work is rejected.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        with self.conn:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS idempotency_operations (
                    operation_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('started','succeeded','failed')),
                    receipt_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )

    def begin(self, operation_key: str, fingerprint: str) -> Optional[str]:
        if not operation_key or not fingerprint:
            raise ValueError("operation_key and fingerprint are required")

        # The read and state transition must be one serialized write
        # transaction. A deferred SELECT followed by INSERT is vulnerable to
        # two workers both observing "missing" and then both executing the
        # external operation. BEGIN IMMEDIATE acquires SQLite's writer lock
        # before the ownership check.
        now = datetime.utcnow().isoformat()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT fingerprint, state, receipt_id FROM idempotency_operations WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()

            if row:
                if row[0] != fingerprint:
                    raise IdempotencyConflict(
                        f"Operation key '{operation_key}' was already bound to different content"
                    )
                if row[1] == "succeeded":
                    self.conn.commit()
                    return row[2]
                if row[1] == "started":
                    self.conn.rollback()
                    raise IdempotencyInProgress(
                        f"Operation key '{operation_key}' is already in progress"
                    )

                # A failed operation may be retried with the same key and
                # fingerprint, but ownership must be reacquired atomically.
                self.conn.execute(
                    "UPDATE idempotency_operations SET state='started', receipt_id=NULL, updated_at=? "
                    "WHERE operation_key=?",
                    (now, operation_key),
                )
            else:
                self.conn.execute(
                    "INSERT INTO idempotency_operations(operation_key,fingerprint,state,created_at,updated_at) "
                    "VALUES(?,?,?,?,?)",
                    (operation_key, fingerprint, "started", now, now),
                )

            self.conn.commit()
            return None
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def succeed(self, operation_key: str, fingerprint: str, receipt_id: str) -> None:
        if not receipt_id:
            raise ValueError("receipt_id is required")
        with self.conn:
            row = self.conn.execute(
                "SELECT fingerprint FROM idempotency_operations WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
            if not row or row[0] != fingerprint:
                raise IdempotencyConflict("Unknown or mismatched idempotency operation")
            self.conn.execute(
                "UPDATE idempotency_operations SET state='succeeded', receipt_id=?, updated_at=? WHERE operation_key=?",
                (receipt_id, datetime.utcnow().isoformat(), operation_key),
            )

    def fail(self, operation_key: str, fingerprint: str) -> None:
        with self.conn:
            row = self.conn.execute(
                "SELECT fingerprint, state FROM idempotency_operations WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
            if not row or row[0] != fingerprint:
                raise IdempotencyConflict("Unknown or mismatched idempotency operation")
            if row[1] != "succeeded":
                self.conn.execute(
                    "UPDATE idempotency_operations SET state='failed', updated_at=? WHERE operation_key=?",
                    (datetime.utcnow().isoformat(), operation_key),
                )

    def get(self, operation_key: str):
        row = self.conn.execute(
            "SELECT operation_key, fingerprint, state, receipt_id, created_at, updated_at FROM idempotency_operations WHERE operation_key=?",
            (operation_key,),
        ).fetchone()
        return row
