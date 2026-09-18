import uuid
from typing import Optional
from src.domain.entities import LedgerEntry
from src.tenancy.ledger import TenantScopedLedger


class OrganisationScopedLedger:
    """Economic boundary below a tenant: every account belongs to one organisation."""

    def __init__(self, tenant_ledger: TenantScopedLedger, organisation_id: str, initial_treasury: int = 0):
        if not organisation_id or ":" in organisation_id:
            raise ValueError("organisation_id must be a non-empty identifier without ':'")
        self.tenant_ledger = tenant_ledger
        self.tenant_id = tenant_ledger.tenant_id
        self.organisation_id = organisation_id
        if initial_treasury > 0 and self.get_balance("TREASURY") == 0:
            entry = LedgerEntry(
                id=str(uuid.uuid4()), timestamp=__import__('datetime').datetime.utcnow(),
                transaction_id=f"{self.tenant_id}:org-{organisation_id}:mint-{uuid.uuid4()}",
                from_account="SYSTEM_MINT", to_account=self._account("TREASURY"), amount=initial_treasury,
                memo=f"Initial organisation treasury for {organisation_id}",
            )
            with self.db.conn:
                self.db.conn.execute(
                    "INSERT INTO ledger_entries (id,timestamp,transaction_id,from_account,to_account,amount,memo,tenant_id) VALUES (?,?,?,?,?,?,?,?)",
                    (entry.id, entry.timestamp.isoformat(), entry.transaction_id, entry.from_account, f"{self.tenant_id}:{entry.to_account}", entry.amount, entry.memo, self.tenant_id),
                )

    def _account(self, account: str) -> str:
        return f"{self.organisation_id}:{account}"

    def get_balance(self, account: str) -> int:
        return self.tenant_ledger.get_balance(self._account(account))

    def transfer(self, from_account: str, to_account: str, amount: int, memo: str, transaction_id: Optional[str] = None) -> LedgerEntry:
        return self.tenant_ledger.transfer(self._account(from_account), self._account(to_account), amount, memo, transaction_id)

    def deposit_revenue(self, amount: int, memo: str, transaction_id: Optional[str] = None) -> LedgerEntry:
        if amount <= 0:
            raise ValueError("Deposit amount must be positive")
        target_account = self._account("REVENUE")
        entry_id = str(uuid.uuid4())
        tx_id = transaction_id or f"{self.tenant_id}:org-{self.organisation_id}:deposit-{uuid.uuid4()}"
        timestamp = __import__('datetime').datetime.utcnow()
        full_to_account = f"{self.tenant_id}:{target_account}"
        with self.db.conn:
            self.db.conn.execute(
                "INSERT INTO ledger_entries (id,timestamp,transaction_id,from_account,to_account,amount,memo,tenant_id) VALUES (?,?,?,?,?,?,?,?)",
                (entry_id, timestamp.isoformat(), tx_id, "SYSTEM_MINT", full_to_account, amount, memo, self.tenant_id),
            )
        return LedgerEntry(
            id=entry_id, timestamp=timestamp, transaction_id=tx_id,
            from_account="SYSTEM_MINT", to_account=target_account, amount=amount, memo=memo,
        )

    def _mint(self, to_account: str, amount: int, memo: str) -> LedgerEntry:
        if to_account == "REVENUE":
            return self.deposit_revenue(amount, memo)
        raise NotImplementedError(f"Minting to {to_account} is not supported on OrganisationScopedLedger")

    def get_entries(self, account: Optional[str] = None):
        prefix = f"{self.organisation_id}:"
        entries = self.tenant_ledger.get_entries(self._account(account) if account else None)
        result = []
        for entry in entries:
            if entry.from_account != "SYSTEM_MINT" and not entry.from_account.startswith(prefix): continue
            if not entry.to_account.startswith(prefix): continue
            result.append(entry.model_copy(update={
                "from_account": entry.from_account.removeprefix(prefix) if entry.from_account != "SYSTEM_MINT" else entry.from_account,
                "to_account": entry.to_account.removeprefix(prefix),
            }))
        return result

    def verify_conservation(self) -> bool:
        entries = self.get_entries()
        minted = sum(e.amount for e in entries if e.from_account == "SYSTEM_MINT")
        net = {}
        for entry in entries:
            if entry.from_account != "SYSTEM_MINT": net[entry.from_account] = net.get(entry.from_account, 0) - entry.amount
            net[entry.to_account] = net.get(entry.to_account, 0) + entry.amount
        return sum(net.values()) == minted

    @property
    def db(self):
        return self.tenant_ledger.ledger.db
