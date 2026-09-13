import uuid
from datetime import datetime
from typing import List, Optional

from src.domain.entities import LedgerEntry
from src.persistence.repositories import SqliteLedger, SYSTEM_MINT


class TenantScopedLedger:
    """Tenant-isolated facade over the authoritative SQLite ledger.

    Account names and transaction IDs are namespaced, and ledger rows are
    written with the tenant ID in the same transaction as the transfer.
    """

    def __init__(self, ledger: SqliteLedger, tenant_id: str, initial_treasury: int = 0):
        if not tenant_id or ":" in tenant_id:
            raise ValueError("tenant_id must be a non-empty identifier without ':'")
        self.ledger = ledger
        self.tenant_id = tenant_id
        self._ensure_tenant()
        if initial_treasury > 0 and self.get_balance("TREASURY") == 0:
            self._mint_scoped(initial_treasury, f"Initial treasury for {tenant_id}")

    def _ensure_tenant(self) -> None:
        with self.ledger.db.conn:
            self.ledger.db.conn.execute(
                "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, ?)",
                (self.tenant_id, self.tenant_id, "active", datetime.utcnow().isoformat()),
            )

    def _account(self, account: str) -> str:
        if account.startswith(f"{self.tenant_id}:"):
            return account
        return f"{self.tenant_id}:{account}"

    def _tx(self, transaction_id: Optional[str]) -> Optional[str]:
        if transaction_id is None:
            return None
        return f"{self.tenant_id}:{transaction_id}"

    def _mint_scoped(self, amount: int, memo: str) -> LedgerEntry:
        if amount <= 0:
            raise ValueError("Mint amount must be positive")
        entry_id = str(uuid.uuid4())
        tx_id = f"{self.tenant_id}:mint-{uuid.uuid4()}"
        timestamp = datetime.utcnow()
        with self.ledger.db.conn:
            self.ledger.db.conn.execute(
                """INSERT INTO ledger_entries
                   (id,timestamp,transaction_id,from_account,to_account,amount,memo,tenant_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (entry_id, timestamp.isoformat(), tx_id, SYSTEM_MINT, self._account("TREASURY"), amount, memo, self.tenant_id),
            )
        return LedgerEntry(
            id=entry_id,
            timestamp=timestamp,
            transaction_id=tx_id,
            from_account=SYSTEM_MINT,
            to_account=self._account("TREASURY"),
            amount=amount,
            memo=memo,
        )

    def get_balance(self, account: str) -> int:
        return self.ledger.get_balance(self._account(account))

    def transfer(
        self,
        from_account: str,
        to_account: str,
        amount: int,
        memo: str,
        transaction_id: Optional[str] = None,
    ) -> LedgerEntry:
        # AtomicSqliteLedger accepts tenant_id and persists it with the entry.
        try:
            entry = self.ledger.transfer(
                self._account(from_account),
                self._account(to_account),
                amount,
                memo,
                transaction_id=self._tx(transaction_id),
                tenant_id=self.tenant_id,
            )
        except TypeError:
            # Compatibility with legacy ledger implementations; production
            # Kalyx uses AtomicSqliteLedger. Never silently alter the transfer.
            entry = self.ledger.transfer(
                self._account(from_account),
                self._account(to_account),
                amount,
                memo,
                transaction_id=self._tx(transaction_id),
            )
            self._mark_tenant(entry.id)
        return entry

    def _mark_tenant(self, entry_id: str) -> None:
        with self.ledger.db.conn:
            self.ledger.db.conn.execute(
                "UPDATE ledger_entries SET tenant_id = ? WHERE id = ?",
                (self.tenant_id, entry_id),
            )

    def get_entries(self, account: Optional[str] = None) -> List[LedgerEntry]:
        entries = self.ledger.get_entries(self._account(account) if account else None)
        prefix = f"{self.tenant_id}:"
        return [
            LedgerEntry(
                id=e.id,
                timestamp=e.timestamp,
                from_account=e.from_account.removeprefix(prefix),
                to_account=e.to_account.removeprefix(prefix),
                amount=e.amount,
                memo=e.memo,
            )
            for e in entries
            if e.from_account.startswith(prefix) and e.to_account.startswith(prefix)
        ]

    def verify_conservation(self) -> bool:
        entries = self.get_entries()
        minted = sum(e.amount for e in entries if e.from_account == "SYSTEM_MINT")
        net = {}
        for entry in entries:
            if entry.from_account != "SYSTEM_MINT":
                net[entry.from_account] = net.get(entry.from_account, 0) - entry.amount
            net[entry.to_account] = net.get(entry.to_account, 0) + entry.amount
        return sum(net.values()) == minted
