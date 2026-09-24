"""Unit tests for RuntimeMode separation and fail-closed bootstrap validation."""

import pytest
from src.api.config import (
    RuntimeMode,
    runtime_mode,
    is_production,
    is_testnet,
    is_live_execution,
)
from src.api.bootstrap import validate_testnet_config, validate_production_config


def test_runtime_mode_predicates(monkeypatch):
    # Test DEVELOPMENT
    monkeypatch.setenv("KALYX_ENV", "development")
    assert runtime_mode() == RuntimeMode.DEVELOPMENT
    assert not is_production()
    assert not is_testnet()
    assert not is_live_execution()
    
    # Test DEMO
    monkeypatch.setenv("KALYX_ENV", "demo")
    assert runtime_mode() == RuntimeMode.DEMO
    assert not is_production()
    assert not is_testnet()
    assert not is_live_execution()

    # Test TEST
    monkeypatch.setenv("KALYX_ENV", "test")
    assert runtime_mode() == RuntimeMode.TEST
    assert not is_production()
    assert not is_testnet()
    assert not is_live_execution()

    # Test TESTNET
    monkeypatch.setenv("KALYX_ENV", "testnet")
    assert runtime_mode() == RuntimeMode.TESTNET
    assert not is_production()
    assert is_testnet()
    assert is_live_execution()

    # Test PRODUCTION
    monkeypatch.setenv("KALYX_ENV", "production")
    assert runtime_mode() == RuntimeMode.PRODUCTION
    assert is_production()
    assert not is_testnet()
    assert is_live_execution()


def test_validate_testnet_config_dev_and_demo_pass_without_creds(monkeypatch):
    monkeypatch.delenv("KALYX_BLOCKCHAIN_RPC_URL", raising=False)
    monkeypatch.delenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", raising=False)

    for env_name in ["development", "demo", "test"]:
        monkeypatch.setenv("KALYX_ENV", env_name)
        validate_testnet_config()


def test_validate_testnet_config_fails_closed_in_testnet_when_missing_vars(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "testnet")
    monkeypatch.delenv("KALYX_BLOCKCHAIN_RPC_URL", raising=False)
    monkeypatch.delenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="Testnet configuration invalid"):
        validate_testnet_config()


def test_validate_production_config_fails_closed_when_missing_vars(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.delenv("KALYX_DATABASE_URL", raising=False)
    monkeypatch.delenv("KALYX_OPERATOR_KEY", raising=False)

    with pytest.raises(RuntimeError, match="Production configuration invalid"):
        validate_production_config()


def test_validate_testnet_config_succeeds_when_all_present(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "testnet")
    monkeypatch.setenv("KALYX_BLOCKCHAIN_RPC_URL", "https://sepolia.infura.io/v3/fake")
    monkeypatch.setenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", "0x" + "a" * 64)

    validate_testnet_config()
