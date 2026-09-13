"""Durable idempotency journal for externally consequential operations."""

import sqlite3
from datetime import datetime
from typing import Optional


class IdempotencyConflict(RuntimeError):
    """Raised when an operation key is reused with different content."""


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
        now = datetime.utcnow().isoformat()
        with self.conn:
            row = self.conn.execute(
                "SELECT fingerprint, state, receipt_id FROM idempotency_operations WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise IdempotencyConflict(
                        f"Operation key '{operation_key}' was already bound to different content"
                    )
                return row[2] if row[1] == "succeeded" else None
            self.conn.execute(
                "INSERT INTO idempotency_operations(operation_key,fingerprint,state,created_at,updated_at) VALUES(?,?,?,?,?)",
                (operation_key, fingerprint, "started", now, now),
            )
        return None

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
