"""Canonical account namespace for tenant + organisation isolation.

Logical accounts (TREASURY, ESCROW, EXTERNAL_SINK) must never be spent
across organisations. Physical account strings are derived only here.
"""

from __future__ import annotations

from dataclasses import dataclass

LOGICAL_ACCOUNTS = frozenset({"TREASURY", "ESCROW", "EXTERNAL_SINK"})
SYSTEM_MINT = "SYSTEM_MINT"


@dataclass(frozen=True)
class AccountNamespace:
    tenant_id: str
    organisation_id: str

    def __post_init__(self):
        if not self.tenant_id or ":" in self.tenant_id:
            raise ValueError("tenant_id must be a non-empty identifier without ':'")
        if not self.organisation_id or ":" in self.organisation_id:
            raise ValueError("organisation_id must be a non-empty identifier without ':'")

    def physical(self, logical: str) -> str:
        """Map a logical account name to the physical ledger account string."""
        if logical == SYSTEM_MINT:
            return SYSTEM_MINT
        if ":" in logical:
            raise ValueError(
                "Callers must not supply physical account strings; use logical names only"
            )
        # Allow agent accounts and standard logical accounts
        return f"{self.tenant_id}:{self.organisation_id}:{logical}"

    def org_relative(self, logical: str) -> str:
        """Organisation-scoped name used by TenantScopedLedger intermediate layer."""
        if logical == SYSTEM_MINT:
            return SYSTEM_MINT
        if ":" in logical:
            raise ValueError("Callers must not supply physical account strings")
        return f"{self.organisation_id}:{logical}"

    @staticmethod
    def parse_physical(account: str) -> tuple[str, str, str] | None:
        """Return (tenant_id, org_id, logical) if account matches canonical form."""
        if account == SYSTEM_MINT:
            return None
        parts = account.split(":")
        if len(parts) < 3:
            return None
        return parts[0], parts[1], ":".join(parts[2:])
