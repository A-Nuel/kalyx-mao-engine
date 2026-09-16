"""Structured logging with automatic secret and credential scrubbing."""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

def scrub_secrets(text: str) -> str:
    """Mask known secrets, private keys, and sensitive credentials in strings."""
    if not text:
        return text

    # Direct environment variable masking
    for env_var in (
        "KALYX_POLICY_SECRET",
        "KALYX_OPERATOR_KEY",
        "KALYX_BLOCKCHAIN_PRIVATE_KEY",
        "OPENROUTER_API_KEY",
    ):
        val = os.getenv(env_var, "").strip()
        if val and len(val) >= 6:
            text = text.replace(val, "[REDACTED]")
            # If 0x prefix was omitted or added
            if val.startswith("0x"):
                text = text.replace(val[2:], "[REDACTED]")

    # Pattern-based scrubbing
    # Private key labels
    text = re.sub(
        r'(?i)(private_key|privkey|signing_key)["\s:=]+["\']?(0x[0-9a-fA-F]{64}|[0-9a-fA-F]{64})["\']?',
        r'\1: "[REDACTED]"',
        text,
    )
    # RPC URLs with embedded basic authentication
    text = re.sub(r'https?://([^:]+:[^@]+@)', 'https://[REDACTED]@', text)
    # Infura / Alchemy API key paths in URLs
    text = re.sub(r'(infura\.io/v3/|alchemy\.com/v2/)[a-zA-Z0-9_\-]+', r'\1[REDACTED]', text)
    # Bearer tokens
    text = re.sub(r'(?i)bearer\s+[a-zA-Z0-9_\.:\-]+', 'Bearer [REDACTED]', text)

    return text


def format_structured_log(
    level: str,
    event: str,
    *,
    request_id: Optional[str] = None,
    organisation_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    operation_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> str:
    """Format an operational event into a structured, secret-scrubbed JSON string."""
    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "level": level.upper(),
        "event": event,
    }
    if request_id:
        record["request_id"] = request_id
    if organisation_id:
        record["organisation_id"] = organisation_id
    if mission_id:
        record["mission_id"] = mission_id
    if operation_id:
        record["operation_id"] = operation_id
    if tenant_id:
        record["tenant_id"] = tenant_id
    if details:
        record["details"] = details

    serialized = json.dumps(record, default=str)
    return scrub_secrets(serialized)


logger = logging.getLogger("kalyx")
