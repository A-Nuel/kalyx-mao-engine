from typing import Any, List, Optional

from src.domain.entities import LedgerEntry
from src.persistence.repositories import SqliteLedger


class TenantScopedLedger:
    """Tenant-isolated facade over the authoritative SQLite ledger.

    Public account names remain stable (TREASURY, ESCROW, EXTERNAL_SINK), while
    physical SQLite accounts are namespaced by tenant. This lets existing policy,
    execution, and audit code keep its domain vocabulary without sharing balances.
    """

    def __init__(self, ledger: SqliteLedger, tenant_id: str, initial_treasury: int = 0):
        if not tenant_id or ":" in tenant_id:
            raise ValueError("tenant_id must be a non-empty identifier without ':'")
        self.ledger = ledger
        self.tenant_id = tenant_id
        if initial_treasury > 0 and self.get_balance("TREASURY") == 0:
            self.ledger._mint(self._account("TREASURY"), initial_treasury, f"Initial treasury for {tenant_id}")

    def _account(self, account: str) -> str:
        if account.startswith(f"{self.tenant_id}:"):
            return account
        return f"{self.tenant_id}:{account}"

    def get_balance(self, account: str) -> int:
        return self.ledger.get_balance(self._account(account))

    def transfer(self, from_account: str, to_account: str, amount: int, memo: str, transaction_id: Optional[str] = None) -> LedgerEntry:
        return self.ledger.transfer(
            self._account(from_account),
            self._account(to_account),
            amount,
            memo,
            transaction_id=transaction_id,
        )

    def get_entries(self, account: Optional[str] = None) -> List[LedgerEntry]:
        entries = self.ledger.get_entries(self._account(account) if account else None)
        prefix = f"{self.tenant_id}:"
        return [
            LedgerEntry(
                id=e.id,
                timestamp=e.timestamp,
                transaction_id=e.transaction_id,
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
