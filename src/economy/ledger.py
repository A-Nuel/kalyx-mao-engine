import uuid
from typing import Dict, List, Optional, Set
from datetime import datetime
from src.domain.entities import LedgerEntry
from src.domain.exceptions import InsufficientCreditsError

SYSTEM_MINT = "SYSTEM_MINT"
TREASURY = "TREASURY"
ESCROW = "ESCROW"
EXTERNAL_SINK = "EXTERNAL_SINK"
REVENUE = "REVENUE"
SURPLUS_RESERVE = "SURPLUS_RESERVE"

class DoubleEntryLedger:
    """
    Deterministic double-entry ledger for ORG Credits.
    Enforces invariant: Total credits minted == Sum of all non-mint account balances.
    Overdrafts are strictly prohibited for non-mint accounts.
    Idempotent by transaction_id to prevent double-spending.
    """
    def __init__(self, initial_treasury: int = 100):
        self._balances: Dict[str, int] = {}
        self._entries: List[LedgerEntry] = []
        self._recorded_tx_ids: Set[str] = set()
        self._total_minted = 0
        
        if initial_treasury > 0:
            self._mint(TREASURY, initial_treasury, "Initial Organisation Treasury Allocation")

    def _mint(self, to_account: str, amount: int, memo: str) -> LedgerEntry:
        tx_id = f"mint-{uuid.uuid4()}"
        entry = LedgerEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            transaction_id=tx_id,
            from_account=SYSTEM_MINT,
            to_account=to_account,
            amount=amount,
            memo=memo
        )
        self._entries.append(entry)
        self._recorded_tx_ids.add(tx_id)
        self._balances[to_account] = self._balances.get(to_account, 0) + amount
        self._total_minted += amount
        return entry

    def deposit_revenue(self, amount: int, memo: str, transaction_id: Optional[str] = None) -> LedgerEntry:
        if amount <= 0:
            raise ValueError("Deposit amount must be positive")
        tx_id = transaction_id or f"deposit-{uuid.uuid4()}"
        if tx_id in self._recorded_tx_ids:
            raise ValueError(f"Duplicate transaction_id: {tx_id}")
        entry = LedgerEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            transaction_id=tx_id,
            from_account=SYSTEM_MINT,
            to_account=REVENUE,
            amount=amount,
            memo=memo,
        )
        self._entries.append(entry)
        self._recorded_tx_ids.add(tx_id)
        self._balances[REVENUE] = self._balances.get(REVENUE, 0) + amount
        self._total_minted += amount
        return entry

    def get_balance(self, account: str) -> int:
        return self._balances.get(account, 0)

    def transfer(
        self,
        from_account: str,
        to_account: str,
        amount: int,
        memo: str,
        transaction_id: Optional[str] = None
    ) -> LedgerEntry:
        tx_id = transaction_id or str(uuid.uuid4())
        if tx_id in self._recorded_tx_ids:
            raise ValueError(f"Duplicate transaction ID '{tx_id}' detected. Transfer aborted to prevent double-spending.")

        if amount <= 0:
            raise ValueError(f"Transfer amount must be positive, got {amount}")
        
        if from_account == to_account:
            raise ValueError("Cannot transfer credits to the same account")

        current_balance = self.get_balance(from_account)
        if current_balance < amount:
            raise InsufficientCreditsError(
                f"Account '{from_account}' has {current_balance} credits, cannot transfer {amount}"
            )

        entry = LedgerEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            transaction_id=tx_id,
            from_account=from_account,
            to_account=to_account,
            amount=amount,
            memo=memo
        )
        self._entries.append(entry)
        self._recorded_tx_ids.add(tx_id)
        self._balances[from_account] = current_balance - amount
        self._balances[to_account] = self.get_balance(to_account) + amount
        return entry

    def get_entries(self, account: Optional[str] = None) -> List[LedgerEntry]:
        if account is None:
            return list(self._entries)
        return [e for e in self._entries if e.from_account == account or e.to_account == account]

    def verify_conservation(self) -> bool:
        sum_balances = sum(b for acc, b in self._balances.items() if acc != SYSTEM_MINT)
        return sum_balances == self._total_minted
