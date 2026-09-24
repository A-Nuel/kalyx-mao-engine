from enum import Enum
import os
from typing import List


class RuntimeMode(str, Enum):
    DEVELOPMENT = "development"
    DEMO = "demo"
    TEST = "test"
    TESTNET = "testnet"
    PRODUCTION = "production"


def runtime_mode() -> RuntimeMode:
    raw = os.getenv("KALYX_ENV", "demo").strip().lower()
    if raw in {"production", "prod"}:
        return RuntimeMode.PRODUCTION
    if raw in {"testnet", "sepolia"}:
        return RuntimeMode.TESTNET
    if raw in {"test", "testing"}:
        return RuntimeMode.TEST
    if raw in {"development", "dev", "local"}:
        return RuntimeMode.DEVELOPMENT
    return RuntimeMode.DEMO


def environment() -> str:
    return runtime_mode().value


def is_production() -> bool:
    return runtime_mode() == RuntimeMode.PRODUCTION


def is_testnet() -> bool:
    return runtime_mode() == RuntimeMode.TESTNET


def is_live_execution() -> bool:
    return runtime_mode() in {RuntimeMode.PRODUCTION, RuntimeMode.TESTNET}


def cors_origins() -> List[str]:
    configured = os.getenv("KALYX_CORS_ORIGINS", "").strip()
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    if is_production():
        return []
    return ["*"]


def operator_key() -> str:
    return os.getenv("KALYX_OPERATOR_KEY", "").strip()


def require_operator_auth() -> bool:
    return is_production() or is_testnet() or bool(operator_key())


def blockchain_enabled() -> bool:
    if is_testnet():
        return True
    return os.getenv("KALYX_BLOCKCHAIN_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def blockchain_rpc_url() -> str:
    return os.getenv("KALYX_BLOCKCHAIN_RPC_URL", "").strip()


def blockchain_private_key() -> str:
    return os.getenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", "").strip()


def blockchain_chain_id() -> int:
    val = os.getenv("KALYX_BLOCKCHAIN_CHAIN_ID", "11155111").strip()
    return int(val) if val else 11155111


def blockchain_network_name() -> str:
    return os.getenv("KALYX_BLOCKCHAIN_NETWORK", "sepolia").strip()


def validate_blockchain_config() -> None:
    """Fail closed if blockchain execution is enabled but configuration is incomplete or invalid."""
    if not blockchain_enabled():
        return

    errors: List[str] = []
    rpc = blockchain_rpc_url()
    if not rpc or not (rpc.startswith("http://") or rpc.startswith("https://")):
        errors.append("KALYX_BLOCKCHAIN_RPC_URL must be a valid HTTP/HTTPS URL when KALYX_BLOCKCHAIN_ENABLED=true")

    key = blockchain_private_key()
    if not key:
        errors.append("KALYX_BLOCKCHAIN_PRIVATE_KEY is required when KALYX_BLOCKCHAIN_ENABLED=true")
    else:
        cleaned = key[2:] if key.startswith("0x") else key
        if len(cleaned) != 64 or not all(c in "0123456789abcdefABCDEF" for c in cleaned):
            errors.append("KALYX_BLOCKCHAIN_PRIVATE_KEY must be a valid 32-byte hex string (64 characters)")

    cid = blockchain_chain_id()
    if cid <= 0:
        errors.append("KALYX_BLOCKCHAIN_CHAIN_ID must be a positive integer")

    if errors:
        raise RuntimeError(f"Blockchain configuration error: {'; '.join(errors)}")
