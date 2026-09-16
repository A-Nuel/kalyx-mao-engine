"""Orbio external economic resource integration.

Verified contracts (authoritative docs, 2026-09-16):
- MCP endpoint: https://www.orbio.so/api/mcp (HTTP transport)
- MCP tools: orbio_get_balance, orbio_create_key, orbio_get_key_status, orbio_revoke_key
- Inference is NOT an MCP tool; it is an HTTP gateway:
  POST https://www.orbio.so/api/v1/chat/completions
  GET  https://www.orbio.so/api/v1/key
- Auth: Bearer Orbio API key; MCP requires authenticated session (401 without token)

No invented MCP tools. Live mode is fail-closed without credentials.
"""

from src.external.orbio.config import OrbioConfig, orbio_mode, validate_orbio_config
from src.external.orbio.adapter import OrbioAdapter, build_orbio_provider
from src.external.orbio.simulated_provider import SimulatedOrbioProvider

__all__ = [
    "OrbioConfig",
    "orbio_mode",
    "validate_orbio_config",
    "OrbioAdapter",
    "build_orbio_provider",
    "SimulatedOrbioProvider",
]
