"""Unit tests ensuring OpenRouter agent adapter fails closed in PRODUCTION/TESTNET."""

import pytest
from src.agents.openrouter_adapter import OpenRouterAgentAdapter
from src.agents.mock_adapter import MockAgentAdapter
from src.agents.schemas import MissionPlanOutput


def test_openrouter_adapter_mock_fallback_allowed_when_provided(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("KALYX_ENV", "demo")
    mock = MockAgentAdapter()
    adapter = OpenRouterAgentAdapter(
        model="anthropic/claude-3-haiku",
        fallback_adapter=mock,
        allow_mock_fallback=True,
    )
    res = adapter.generate_structured("test prompt", MissionPlanOutput)
    assert res is not None
    assert isinstance(res, MissionPlanOutput)


def test_openrouter_adapter_fails_closed_in_production(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("KALYX_ENV", "production")
    mock = MockAgentAdapter()
    adapter = OpenRouterAgentAdapter(
        model="anthropic/claude-3-haiku",
        fallback_adapter=mock,
        allow_mock_fallback=False,
    )
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is required in PRODUCTION/TESTNET mode"):
        adapter.generate_structured("test prompt", MissionPlanOutput)


def test_openrouter_adapter_fails_closed_in_testnet(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("KALYX_ENV", "testnet")
    mock = MockAgentAdapter()
    adapter = OpenRouterAgentAdapter(
        model="anthropic/claude-3-haiku",
        fallback_adapter=mock,
        allow_mock_fallback=False,
    )
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is required in PRODUCTION/TESTNET mode"):
        adapter.generate_structured("test prompt", MissionPlanOutput)


def test_openrouter_adapter_raises_value_error_in_dev_if_no_fallback_supplied(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("KALYX_ENV", "development")
    adapter = OpenRouterAgentAdapter(
        model="anthropic/claude-3-haiku",
        fallback_adapter=None,
    )
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY is not configured"):
        adapter.generate_structured("test prompt", MissionPlanOutput)
