import pytest

from src.api.bootstrap import policy_secret, validate_production_config


def test_demo_allows_default_policy_secret(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "demo")
    monkeypatch.delenv("KALYX_POLICY_SECRET", raising=False)
    assert policy_secret() == "phase7-demo-policy-secret"


def test_production_rejects_missing_postgres(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.setenv("KALYX_POLICY_SECRET", "a-real-unique-secret-value")
    monkeypatch.setenv("KALYX_OPERATOR_KEY", "op-key")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")
    monkeypatch.delenv("KALYX_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        validate_production_config()


def test_production_rejects_demo_policy_secret(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.setenv("KALYX_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("KALYX_POLICY_SECRET", "phase7-demo-policy-secret")
    monkeypatch.setenv("KALYX_OPERATOR_KEY", "op-key")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")
    with pytest.raises(RuntimeError, match="demo policy"):
        validate_production_config()


def test_production_rejects_missing_identity(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.setenv("KALYX_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("KALYX_POLICY_SECRET", "a-real-unique-secret-value")
    monkeypatch.setenv("KALYX_OPERATOR_KEY", "op-key")
    monkeypatch.delenv("KALYX_IDENTITY_AUTH", raising=False)
    with pytest.raises(RuntimeError, match="IDENTITY"):
        validate_production_config()


def test_production_ok_when_fully_configured(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.setenv("KALYX_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("KALYX_POLICY_SECRET", "a-real-unique-secret-value")
    monkeypatch.setenv("KALYX_OPERATOR_KEY", "op-key")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")
    validate_production_config()
