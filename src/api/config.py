import os
from typing import List


def environment() -> str:
    return os.getenv("KALYX_ENV", "demo").strip().lower()


def cors_origins() -> List[str]:
    configured = os.getenv("KALYX_CORS_ORIGINS", "").strip()
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    if environment() in {"production", "prod"}:
        return []
    return ["*"]


def operator_key() -> str:
    return os.getenv("KALYX_OPERATOR_KEY", "").strip()


def require_operator_auth() -> bool:
    return environment() in {"production", "prod"} or bool(operator_key())
