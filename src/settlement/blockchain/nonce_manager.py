"""Deterministic Nonce Manager for EVM Blockchain Settlements.

Guarantees:
- Replay safety: Retrying an operation with the same idempotency key reuses the exact same nonce.
- Ordering: Strictly sequential nonces per sender account and chain ID.
- Concurrency safety: Thread-safe locking across concurrent proposals.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, Optional, Tuple


class NonceManager:
    """Thread-safe nonce manager with idempotency tracking."""

    def __init__(self):
        self._lock = threading.RLock()
        # (sender_lower, chain_id) -> next available nonce
        self._next_nonces: Dict[Tuple[str, int], int] = {}
        # (operation_id, idempotency_key) -> assigned nonce
        self._assigned_nonces: Dict[Tuple[str, str], int] = {}

    def get_or_allocate_nonce(
        self,
        sender: str,
        chain_id: int,
        operation_id: str,
        idempotency_key: str,
        on_chain_nonce_fetcher: Callable[[], int],
    ) -> int:
        """Acquires a deterministic nonce for an operation.
        
        If this operation was already assigned a nonce (e.g. during a retry or crash recovery),
        the assigned nonce is returned. Otherwise, the next sequential nonce is allocated.
        """
        sender_clean = sender.strip().lower()
        op_key = (operation_id, idempotency_key)
        acct_key = (sender_clean, chain_id)

        with self._lock:
            # 1. Check if already assigned for this operation
            if op_key in self._assigned_nonces:
                return self._assigned_nonces[op_key]

            # 2. Fetch authoritative on-chain count
            on_chain_nonce = on_chain_nonce_fetcher()

            # 3. Determine next local nonce
            current_local = self._next_nonces.get(acct_key, 0)
            allocated = max(on_chain_nonce, current_local)

            # 4. Advance counter and record assignment
            self._assigned_nonces[op_key] = allocated
            self._next_nonces[acct_key] = allocated + 1
            return allocated

    def peek_assigned_nonce(self, operation_id: str, idempotency_key: str) -> Optional[int]:
        """Check if an operation was already assigned a nonce."""
        with self._lock:
            return self._assigned_nonces.get((operation_id, idempotency_key))

    def reset_for_account(self, sender: str, chain_id: int) -> None:
        """Clear cached state for testing."""
        with self._lock:
            self._next_nonces.pop((sender.strip().lower(), chain_id), None)
