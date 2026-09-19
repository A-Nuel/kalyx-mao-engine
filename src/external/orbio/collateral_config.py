"""Configuration for the Phase 18 CREDIT collateral vault.

LIVE mode deliberately separates the two signing roles:
- pledger key: owns CREDIT, approves the vault, and signs lock()
- owner key: the Kalyx settlement authority, signs release()/forfeit()

For a single-wallet hackathon rehearsal they may be the same key. Production
should use separate custody and settlement controls.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from src.external.models import ExternalProviderMode

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
    owner_private_key: Optional[str]
    pledger_private_key: Optional[str]

    @classmethod
    def from_env(cls) -> "CollateralVaultConfig":
        return cls(
            mode=collateral_mode(),
            rpc_url=os.getenv("KALYX_COLLATERAL_RPC_URL", "").strip(),
            vault_address=os.getenv("KALYX_COLLATERAL_VAULT_ADDRESS", "").strip() or None,
            credit_token_address=os.getenv("KALYX_COLLATERAL_TOKEN_ADDRESS", "").strip() or None,
            owner_private_key=os.getenv("KALYX_COLLATERAL_OWNER_KEY", "").strip() or None,
            pledger_private_key=os.getenv("KALYX_COLLATERAL_PLEDGER_KEY", "").strip() or None,
        )


def validate_collateral_config(cfg: Optional[CollateralVaultConfig] = None) -> None:
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
    if not cfg.owner_private_key:
        errors.append("KALYX_COLLATERAL_OWNER_KEY is required when KALYX_COLLATERAL_MODE=live")
    if not cfg.pledger_private_key:
        errors.append("KALYX_COLLATERAL_PLEDGER_KEY is required when KALYX_COLLATERAL_MODE=live")
    if errors:
        raise RuntimeError(f"Collateral vault live configuration error: {'; '.join(errors)}")
