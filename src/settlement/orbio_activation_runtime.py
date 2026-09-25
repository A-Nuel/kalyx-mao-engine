"""Phase 20B — Explicit mainnet activation runtime guard.

Fail closed. Never silently downgrade to simulation or testnet.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.domain.orbio_activation import (
    ORBIO_ACTIVATION_CHAIN_ID,
    ORBIO_CREDIT_ACTIVATION_CONTRACT,
)


class MainnetActivationConfigError(RuntimeError):
    """Raised when mainnet activation runtime is misconfigured."""


@dataclass(frozen=True)
class OrbioMainnetActivationRuntime:
    """Explicit configuration for live activation path.

    Construction validates invariants. Does not send transactions.
    """

    chain_id: int
    credit_contract: str
    rpc_url: str
    signer_configured: bool
    mode: str = "mainnet"

    def __post_init__(self) -> None:
        if self.mode.lower() != "mainnet":
            raise MainnetActivationConfigError(
                f"mode must be 'mainnet', got '{self.mode}' (no simulation fallback)"
            )
        if self.chain_id != ORBIO_ACTIVATION_CHAIN_ID:
            raise MainnetActivationConfigError(
                f"chain_id must be {ORBIO_ACTIVATION_CHAIN_ID}, got {self.chain_id}"
            )
        if self.credit_contract.lower() != ORBIO_CREDIT_ACTIVATION_CONTRACT:
            raise MainnetActivationConfigError(
                f"credit_contract must be {ORBIO_CREDIT_ACTIVATION_CONTRACT}"
            )
        if not self.rpc_url or not self.rpc_url.startswith("http"):
            raise MainnetActivationConfigError("rpc_url missing or invalid")
        if not self.signer_configured:
            raise MainnetActivationConfigError("signer not configured; fail closed")

    @classmethod
    def from_env(
        cls,
        *,
        chain_id: Optional[int],
        credit_contract: Optional[str],
        rpc_url: Optional[str],
        signer_configured: bool,
        mode: str = "mainnet",
    ) -> "OrbioMainnetActivationRuntime":
        if chain_id is None or credit_contract is None or rpc_url is None:
            raise MainnetActivationConfigError(
                "missing configuration: chain_id, credit_contract, and rpc_url are required"
            )
        return cls(
            chain_id=int(chain_id),
            credit_contract=credit_contract,
            rpc_url=rpc_url,
            signer_configured=signer_configured,
            mode=mode,
        )
