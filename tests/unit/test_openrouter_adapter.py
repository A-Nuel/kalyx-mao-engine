import os
import pytest
from unittest.mock import patch, MagicMock
from pydantic import ValidationError
from src.agents.openrouter_adapter import OpenRouterAgentAdapter
from src.agents.schemas import ResearchOutput

def test_openrouter_adapter_missing_api_key():
    with patch.dict(os.environ, {}, clear=True):
        adapter = OpenRouterAgentAdapter(api_key=None)
        with pytest.raises(ValueError) as exc:
            adapter.generate_structured("prompt", ResearchOutput)
        assert "OPENROUTER_API_KEY is not configured" in str(exc.value)

def test_openrouter_adapter_successful_response():
    fake_json = """{
        "market_trend": "Bullish",
        "key_findings": ["Strong liquidity", "Low volatility"],
        "recommended_focus": "Index funds",
        "evidence_data": {"score": 0.9}
    }"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": fake_json}}]
    }
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.Client.post", return_value=mock_resp):
        adapter = OpenRouterAgentAdapter(api_key="sk-or-fake-key-for-unit-testing")
        result = adapter.generate_structured("prompt", ResearchOutput)
        assert result.market_trend == "Bullish"
        assert len(result.key_findings) == 2
        assert result.evidence_data["score"] == 0.9

def test_openrouter_adapter_schema_validation_error():
    bad_json = """{"unrelated_field": 123}"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": bad_json}}]
    }
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.Client.post", return_value=mock_resp):
        adapter = OpenRouterAgentAdapter(api_key="sk-or-fake-key")
        with pytest.raises(ValidationError):
            adapter.generate_structured("prompt", ResearchOutput)
