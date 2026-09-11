import pytest
from pydantic import BaseModel, Field
from src.agents.openrouter_adapter import OpenRouterAgentAdapter
from src.agents.mock_adapter import MockAgentAdapter
from src.domain.exceptions import LLMOutputValidationError

class DummyModel(BaseModel):
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    score: int

def test_openrouter_adapter_success_with_clean_json():
    def mock_transport(prompt: str, payload: dict) -> dict:
        return {
            "choices": [{
                "message": {
                    "content": '{"summary": "Valid synthesis", "confidence": 0.95, "score": 10}'
                }
            }]
        }

    adapter = OpenRouterAgentAdapter(
        api_key="test-key",
        mock_transport=mock_transport
    )
    result = adapter.generate_structured("Analyze data", DummyModel)
    assert result.summary == "Valid synthesis"
    assert result.confidence == 0.95
    assert result.score == 10

def test_openrouter_adapter_strips_markdown_code_fences():
    def mock_transport(prompt: str, payload: dict) -> dict:
        return {
            "choices": [{
                "message": {
                    "content": '```json\n{"summary": "Code fence stripped", "confidence": 0.88, "score": 42}\n```'
                }
            }]
        }

    adapter = OpenRouterAgentAdapter(
        api_key="test-key",
        mock_transport=mock_transport
    )
    result = adapter.generate_structured("Analyze data", DummyModel)
    assert result.summary == "Code fence stripped"
    assert result.score == 42

def test_openrouter_adapter_rejects_malformed_json():
    def mock_transport(prompt: str, payload: dict) -> dict:
        return {
            "choices": [{
                "message": {
                    "content": '{"summary": "Broken json',
                }
            }]
        }

    adapter = OpenRouterAgentAdapter(
        api_key="test-key",
        mock_transport=mock_transport
    )
    with pytest.raises(LLMOutputValidationError) as exc:
        adapter.generate_structured("Analyze data", DummyModel)
    assert "Malformed or schema-invalid" in str(exc.value)

def test_openrouter_adapter_rejects_schema_invalid_fields():
    def mock_transport(prompt: str, payload: dict) -> dict:
        return {
            "choices": [{
                "message": {
                    "content": '{"summary": "Invalid confidence", "confidence": 5.0, "score": 10}'
                }
            }]
        }

    adapter = OpenRouterAgentAdapter(
        api_key="test-key",
        mock_transport=mock_transport
    )
    with pytest.raises(LLMOutputValidationError) as exc:
        adapter.generate_structured("Analyze data", DummyModel)
    assert "Malformed or schema-invalid" in str(exc.value)

def test_openrouter_adapter_fallback_on_error():
    def failing_transport(prompt: str, payload: dict) -> dict:
        raise RuntimeError("Upstream API 500 error")

    mock_fallback = MockAgentAdapter()
    adapter = OpenRouterAgentAdapter(
        api_key="test-key",
        mock_transport=failing_transport,
        fallback_adapter=mock_fallback,
        fallback_on_error=True
    )
    
    # Even when upstream LLM fails, fallback adapter handles it deterministically
    from src.agents.schemas import ResearchOutput
    res = adapter.generate_structured("Run research", ResearchOutput)
    assert isinstance(res, ResearchOutput)
    assert len(res.market_trend) > 0
