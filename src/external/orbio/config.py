"""Orbio configuration. Fail-closed in live mode."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from src.external.models import ExternalProviderMode

DEFAULT_MCP_URL = "https://www.orbio.so/api/mcp"
DEFAULT_GATEWAY_BASE = "https://www.orbio.so/api/v1"


def orbio_mode() -> ExternalProviderMode:
    raw = os.getenv("KALYX_ORBIO_MODE", "disabled").strip().lower()
    try:
        return ExternalProviderMode(raw)
    except ValueError:
        return ExternalProviderMode.DISABLED


@dataclass(frozen=True)
class OrbioConfig:
    mode: ExternalProviderMode
    mcp_url: str
    gateway_base: str
    api_key: Optional[str]  # env-only; never persisted

    @classmethod
    def from_env(cls) -> "OrbioConfig":
        return cls(
            mode=orbio_mode(),
            mcp_url=os.getenv("ORBIO_MCP_URL", DEFAULT_MCP_URL).strip(),
            gateway_base=os.getenv("ORBIO_GATEWAY_BASE", DEFAULT_GATEWAY_BASE).strip(),
            api_key=os.getenv("ORBIO_API_KEY", "").strip() or None,
        )


def validate_orbio_config(cfg: Optional[OrbioConfig] = None) -> None:
    """Fail closed if live mode is enabled without required credentials."""
    cfg = cfg or OrbioConfig.from_env()
    if cfg.mode != ExternalProviderMode.LIVE:
        return
    errors: List[str] = []
    if not cfg.api_key:
        errors.append("ORBIO_API_KEY is required when KALYX_ORBIO_MODE=live")
    if not cfg.mcp_url.startswith("https://"):
        errors.append("ORBIO_MCP_URL must be HTTPS in live mode")
    if not cfg.gateway_base.startswith("https://"):
        errors.append("ORBIO_GATEWAY_BASE must be HTTPS in live mode")
    if errors:
        raise RuntimeError(f"Orbio live configuration error: {'; '.join(errors)}")
