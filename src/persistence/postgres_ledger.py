"""PostgreSQL ledger with transaction-safe concurrent spend prevention.

Balances remain derived from immutable ledger_entries (same model as SQLite).
Concurrency is enforced with transaction-scoped advisory locks on the from_account
so concurrent spends serialize without a separate balance table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from src.domain.entities import LedgerEntry
from src.domain.exceptions import InsufficientCreditsError

SYSTEM_MINT = "SYSTEM_MINT"


class PostgresLedger:
    """Authoritative double-entry ledger for PostgreSQL."""

    def __init__(self, db, initial_treasury: int = 0, tenant_id: str = "tenant-demo"):
        self.db = db
        self.tenant_id = tenant_id
        if initial_treasury > 0 and self.get_balance("TREASURY") == 0:
            self._mint("TREASURY", initial_treasury, "Initial treasury allocation")

    def _mint(self, to_account: str, amount: int, memo: str) -> LedgerEntry:
        tx_id = f"mint-{uuid.uuid4()}"
        entry_id = str(uuid.uuid4())
        now = datetime.utcnow()
        with self.db.conn:
            self.db.conn.execute(
                """INSERT INTO ledger_entries
                   (id, timestamp, transaction_id, from_account, to_account, amount, memo, tenant_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (entry_id, now, tx_id, SYSTEM_MINT, to_account, amount, memo, self.tenant_id),
            )
        return LedgerEntry(
            id=entry_id, timestamp=now, transaction_id=tx_id,
            from_account=SYSTEM_MINT, to_account=to_account, amount=amount, memo=memo,
        )

    def deposit_revenue(self, amount: int, memo: str, transaction_id: Optional[str] = None, to_account: str = "REVENUE") -> LedgerEntry:
        if amount <= 0:
            raise ValueError("Deposit amount must be positive")
        tx_id = transaction_id or f"deposit-{uuid.uuid4()}"
        entry_id = str(uuid.uuid4())
        now = datetime.utcnow()
        with self.db.conn:
            self.db.conn.execute(
                """INSERT INTO ledger_entries
                   (id, timestamp, transaction_id, from_account, to_account, amount, memo, tenant_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (entry_id, now, tx_id, SYSTEM_MINT, to_account, amount, memo, self.tenant_id),
            )
        return LedgerEntry(
            id=entry_id, timestamp=now, transaction_id=tx_id,
            from_account=SYSTEM_MINT, to_account=to_account, amount=amount, memo=memo,
        )

    def get_balance(self, account: str) -> int:
        row = self.db.conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN to_account = %s THEN amount ELSE 0 END), 0)
                      - COALESCE(SUM(CASE WHEN from_account = %s THEN amount ELSE 0 END), 0) AS balance
               FROM ledger_entries""",
            (account, account),
        ).fetchone()
        if row is None:
            return 0
        return int(row["balance"] if hasattr(row, "keys") and "balance" in row.keys() else row[0] or 0)

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
        tid = tenant_id or self.tenant_id
        tx_id = transaction_id or str(uuid.uuid4())
        entry_id = str(uuid.uuid4())
        now = datetime.utcnow()
        conn = self.db.conn
        try:
            # Serialize spends on the same from_account within this transaction.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (from_account,))
            dup = conn.execute(
                "SELECT 1 FROM ledger_entries WHERE transaction_id = %s", (tx_id,)
            ).fetchone()
            if dup:
                conn.rollback()
                raise ValueError(
                    f"Duplicate transaction ID '{tx_id}' detected. Transfer aborted to prevent double-spending."
                )
            bal_row = conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN to_account = %s THEN amount ELSE 0 END), 0)
                          - COALESCE(SUM(CASE WHEN from_account = %s THEN amount ELSE 0 END), 0) AS balance
                   FROM ledger_entries""",
                (from_account, from_account),
            ).fetchone()
            balance = int(
                bal_row["balance"] if hasattr(bal_row, "keys") and "balance" in bal_row.keys() else (bal_row[0] if bal_row else 0) or 0
            )
            if balance < amount:
                conn.rollback()
                raise InsufficientCreditsError(
                    f"Account '{from_account}' has {balance} credits, cannot transfer {amount}"
                )
            conn.execute(
                """INSERT INTO ledger_entries
                   (id, timestamp, transaction_id, from_account, to_account, amount, memo, tenant_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (entry_id, now, tx_id, from_account, to_account, amount, memo, tid),
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        return LedgerEntry(
            id=entry_id, timestamp=now, transaction_id=tx_id,
            from_account=from_account, to_account=to_account, amount=amount, memo=memo,
        )

    def get_entries(self, account: Optional[str] = None) -> List[LedgerEntry]:
        if account:
            rows = self.db.conn.execute(
                """SELECT * FROM ledger_entries
                   WHERE from_account = %s OR to_account = %s
                   ORDER BY sequence_num ASC""",
                (account, account),
            ).fetchall()
        else:
            rows = self.db.conn.execute(
                "SELECT * FROM ledger_entries ORDER BY sequence_num ASC"
            ).fetchall()
        out: List[LedgerEntry] = []
        for r in rows:
            ts = r["timestamp"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            out.append(
                LedgerEntry(
                    id=r["id"], timestamp=ts, transaction_id=r["transaction_id"],
                    from_account=r["from_account"], to_account=r["to_account"],
                    amount=int(r["amount"]), memo=r["memo"],
                )
            )
        return out

    def verify_conservation(self) -> bool:
        rows = self.get_entries()
        minted = sum(e.amount for e in rows if e.from_account == SYSTEM_MINT)
        net = {}
        for e in rows:
            if e.from_account != SYSTEM_MINT:
                net[e.from_account] = net.get(e.from_account, 0) - e.amount
            net[e.to_account] = net.get(e.to_account, 0) + e.amount
        return sum(net.values()) == minted
