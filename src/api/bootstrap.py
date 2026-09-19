"""Production startup fail-closed gates.

Demo/local retain ergonomic defaults. Production refuses to start with
missing or known-insecure configuration.
"""

from __future__ import annotations

import os
from typing import List

DEMO_POLICY_SECRETS = {
    "phase7-demo-policy-secret",
    "demo",
    "secret",
    "changeme",
    "test",
}


def environment() -> str:
    return os.getenv("KALYX_ENV", "demo").strip().lower()


def is_production() -> bool:
    return environment() in {"production", "prod"}


def policy_secret() -> str:
    """Resolve policy signing secret.

    Demo may use an explicit demo default. Production must supply a real secret
    that is not a known demo value.
    """
    secret = os.getenv("KALYX_POLICY_SECRET", "").strip()
    if is_production():
        if not secret:
            raise RuntimeError("KALYX_ENV=production requires KALYX_POLICY_SECRET")
        if secret.lower() in DEMO_POLICY_SECRETS:
            raise RuntimeError(
                "KALYX_ENV=production rejects known demo policy secrets; set a unique KALYX_POLICY_SECRET"
            )
        return secret
    return secret or "phase7-demo-policy-secret"


def validate_production_config() -> None:
    """Raise RuntimeError if production configuration is incomplete or unsafe."""
    if not is_production():
        return

    errors: List[str] = []

    url = os.getenv("KALYX_DATABASE_URL", "").strip().lower()
    if not (url.startswith("postgres://") or url.startswith("postgresql://")):
        errors.append("KALYX_DATABASE_URL must be a PostgreSQL URL in production (SQLite is not permitted)")

    try:
        policy_secret()
    except RuntimeError as exc:
        errors.append(str(exc))

    operator = os.getenv("KALYX_OPERATOR_KEY", "").strip()
    if not operator:
        errors.append("KALYX_OPERATOR_KEY is required in production")

    identity = os.getenv("KALYX_IDENTITY_AUTH", "").strip().lower()
    if identity not in {"1", "true", "yes", "on", "production"}:
        errors.append("KALYX_IDENTITY_AUTH must be enabled in production")

    if errors:
        raise RuntimeError("Production configuration invalid: " + "; ".join(errors))


def ensure_started() -> None:
    """Call once at application import/startup."""
    validate_production_config()
    from src.api.config import validate_blockchain_config
    validate_blockchain_config()
