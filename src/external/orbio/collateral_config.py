"""CollateralVault on-chain client configuration. Fail-closed in live mode,
mirroring src/external/orbio/config.py's OrbioConfig pattern exactly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from src.external.models import ExternalProviderMode

DEFAULT_TESTNET_RPC = "https://rpc.testnet.chain.robinhood.com"
DEFAULT_MAINNET_RPC = "https://rpc.mainnet.chain.robinhood.com"

# Real Orbio CREDIT token, Robinhood Chain mainnet (4663).
# Source: https://www.orbio.so/protocol/agents
REAL_ORBIO_CREDIT_ADDRESS = "0xe33322Da1380e61E5AE5DFb21e7F62924c73004C"


def collateral_mode() -> ExternalProviderMode:
    raw = os.getenv("KALYX_COLLATERAL_MODE", "disabled").strip().lower()
    try:
        return ExternalProviderMode(raw)
    except ValueError:
        return ExternalProviderMode.DISABLED


@dataclass(frozen=True)
class CollateralVaultConfig:
    mode: ExternalProviderMode
    rpc_url: str
    vault_address: Optional[str]
    credit_token_address: Optional[str]
    signer_private_key: Optional[str]  # env-only; never persisted or logged

    @classmethod
    def from_env(cls) -> "CollateralVaultConfig":
        mode = collateral_mode()
        # Default RPC depends on which chain the deployed vault address is on;
        # callers in LIVE mode MUST set KALYX_COLLATERAL_RPC_URL explicitly
        # rather than rely on this default, enforced in validate() below.
        rpc_url = os.getenv("KALYX_COLLATERAL_RPC_URL", "").strip()
        return cls(
            mode=mode,
            rpc_url=rpc_url,
            vault_address=os.getenv("KALYX_COLLATERAL_VAULT_ADDRESS", "").strip() or None,
            credit_token_address=os.getenv("KALYX_COLLATERAL_TOKEN_ADDRESS", "").strip() or None,
            signer_private_key=os.getenv("KALYX_COLLATERAL_SIGNER_KEY", "").strip() or None,
        )


def validate_collateral_config(cfg: Optional[CollateralVaultConfig] = None) -> None:
    """Fail closed if LIVE mode is enabled without everything required to
    actually sign and send a real transaction. Never silently fall back to
    SIMULATED behavior from within a component configured as LIVE — that
    would let a misconfiguration masquerade as a successful on-chain result."""
    cfg = cfg or CollateralVaultConfig.from_env()
    if cfg.mode != ExternalProviderMode.LIVE:
        return
    errors: List[str] = []
    if not cfg.rpc_url:
        errors.append("KALYX_COLLATERAL_RPC_URL is required when KALYX_COLLATERAL_MODE=live")
    elif not cfg.rpc_url.startswith("https://"):
        errors.append("KALYX_COLLATERAL_RPC_URL must be HTTPS in live mode")
    if not cfg.vault_address:
        errors.append("KALYX_COLLATERAL_VAULT_ADDRESS is required when KALYX_COLLATERAL_MODE=live")
    if not cfg.credit_token_address:
        errors.append("KALYX_COLLATERAL_TOKEN_ADDRESS is required when KALYX_COLLATERAL_MODE=live")
    if not cfg.signer_private_key:
        errors.append("KALYX_COLLATERAL_SIGNER_KEY is required when KALYX_COLLATERAL_MODE=live")
    if errors:
        raise RuntimeError(f"Collateral vault live configuration error: {'; '.join(errors)}")
