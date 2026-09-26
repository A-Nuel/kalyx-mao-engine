"""Phase 21E — public wallet identity boundary.

A Wallet is public metadata only. No seed phrase, private key, or signing
credential belongs in this model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from web3 import Web3


@dataclass(frozen=True)
class WalletIdentity:
    wallet_id: str
    tenant_id: str
    organisation_id: str
    chain_id: int
    address: str
    provider: str
    label: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.wallet_id.strip():
            raise ValueError("wallet_id is required")
        if not self.tenant_id.strip() or not self.organisation_id.strip():
            raise ValueError("wallet scope is required")
        if self.chain_id <= 0:
            raise ValueError("chain_id must be positive")
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", self.address.strip()):
            raise ValueError("wallet address must be a 20-byte hexadecimal address")

    @property
    def checksum_address(self) -> str:
        return Web3.to_checksum_address(self.address)

    def assert_scope(self, tenant_id: str, organisation_id: str) -> None:
        if tenant_id != self.tenant_id:
            raise PermissionError("Wallet tenant boundary violation")
        if organisation_id != self.organisation_id:
            raise PermissionError("Wallet organisation boundary violation")
