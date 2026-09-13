"""Concurrency-safe SQLite ledger facade.

Uses BEGIN IMMEDIATE so balance check + ledger append are one serialized
critical section. Tenant-aware callers can persist tenant_id in the same write.
"""

import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from src.domain.entities import LedgerEntry
from src.domain.exceptions import InsufficientCreditsError
from src.persistence.repositories import SqliteLedger


class AtomicSqliteLedger(SqliteLedger):
    """SqliteLedger with serialized transfers and deterministic validation."""

    def __init__(self, db, initial_treasury: int = 100):
        super().__init__(db, initial_treasury=initial_treasury)
        self.db.conn.execute("PRAGMA busy_timeout = 10000")

    def transfer(
        self,
        from_account: str,
        to_account: str,
        amount: int,
        memo: str,
        transaction_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> LedgerEntry:
        if amount <= 0:
            raise ValueError(f"Transfer amount must be positive, got {amount}")
        if from_account == to_account:
            raise ValueError("Cannot transfer credits to the same account")
        if tenant_id is not None and (not tenant_id or ":" in tenant_id):
            raise ValueError("tenant_id must be a non-empty identifier without ':'")
        tx_id = transaction_id or str(uuid.uuid4())
        entry_id = str(uuid.uuid4())
        now_iso = datetime.utcnow().isoformat()
        conn = self.db.conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM ledger_entries WHERE transaction_id = ?", (tx_id,)).fetchone():
                conn.rollback()
                raise ValueError(f"Duplicate transaction ID '{tx_id}' detected. Transfer aborted to prevent double-spending.")
            row = conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN to_account=? THEN amount ELSE 0 END),0)
                         - COALESCE(SUM(CASE WHEN from_account=? THEN amount ELSE 0 END),0)
                    FROM ledger_entries""",
                (from_account, from_account),
            ).fetchone()
            balance = int(row[0] or 0)
            if balance < amount:
                conn.rollback()
                raise InsufficientCreditsError(f"Account '{from_account}' has {balance} credits, cannot transfer {amount}")
            if tenant_id is None:
                conn.execute(
                    "INSERT INTO ledger_entries (id,timestamp,transaction_id,from_account,to_account,amount,memo) VALUES (?,?,?,?,?,?,?)",
                    (entry_id, now_iso, tx_id, from_account, to_account, amount, memo),
                )
            else:
                conn.execute(
                    "INSERT INTO ledger_entries (id,timestamp,transaction_id,from_account,to_account,amount,memo,tenant_id) VALUES (?,?,?,?,?,?,?,?)",
                    (entry_id, now_iso, tx_id, from_account, to_account, amount, memo, tenant_id),
                )
            conn.commit()
        except sqlite3.OperationalError:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        return LedgerEntry(
            id=entry_id,
            timestamp=datetime.fromisoformat(now_iso),
            transaction_id=tx_id,
            from_account=from_account,
            to_account=to_account,
            amount=amount,
            memo=memo,
        )
