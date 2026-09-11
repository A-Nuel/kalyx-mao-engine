import os
import re
import json
from typing import Type, TypeVar, Optional, Callable, Any
import httpx
from pydantic import BaseModel, ValidationError
from src.agents.base import IAgentAdapter
from src.domain.exceptions import LLMOutputValidationError

T = TypeVar("T", bound=BaseModel)

class OpenRouterAgentAdapter(IAgentAdapter):
    """
    Pluggable OpenRouter LLM adapter.
    Uses OpenAI-compatible chat completions endpoint with structured JSON output enforcement.
    Safely validates LLM responses through typed Pydantic schemas and converts
    malformed outputs or upstream errors into LLMOutputValidationError.
    Supports seamless fallback to MockAgentAdapter for offline deterministic replay.
    """
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "openai/gpt-4o-mini",
        base_url: str = "https://openrouter.ai/api/v1",
        fallback_adapter: Optional[IAgentAdapter] = None,
        fallback_on_error: bool = False,
        mock_transport: Optional[Callable[[str, dict], dict]] = None
    ):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.model = model
        self.base_url = base_url
        self.fallback_adapter = fallback_adapter
        self.fallback_on_error = fallback_on_error
        self.mock_transport = mock_transport

    def _clean_json_content(self, text: str) -> str:
        """Strip markdown code fence blocks if present."""
        text = text.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            return match.group(1).strip()
        return text

    def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        system_prompt: Optional[str] = None
    ) -> T:
        if not self.api_key and not self.mock_transport:
            if self.fallback_adapter:
                return self.fallback_adapter.generate_structured(prompt, schema, system_prompt)
            raise ValueError(
                "OPENROUTER_API_KEY is not configured. Set the environment variable or supply a fallback_adapter."
            )

        schema_json = json.dumps(schema.model_json_schema())
        system_instruction = (
            (system_prompt or "You are an autonomous organizational agent.")
            + f"\nYou must strictly reply with valid JSON conforming to this JSON Schema:\n{schema_json}\n"
            "Do NOT include markdown formatting (no ```json code blocks), only raw parseable JSON."
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2
        }

        try:
            if self.mock_transport:
                data = self.mock_transport(prompt, payload)
            else:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/autonomous-org",
                    "X-Title": "Autonomous Org"
                }
                with httpx.Client(timeout=45.0) as client:
                    response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()

            raw_content = data["choices"][0]["message"]["content"]
            cleaned_content = self._clean_json_content(raw_content)
            
            # Strict schema validation
            return schema.model_validate_json(cleaned_content)

        except (ValidationError, json.JSONDecodeError, KeyError) as e:
            if self.fallback_adapter and self.fallback_on_error:
                return self.fallback_adapter.generate_structured(prompt, schema, system_prompt)
            raise LLMOutputValidationError(f"Malformed or schema-invalid LLM response: {str(e)}") from e
        except Exception as e:
            if self.fallback_adapter and self.fallback_on_error:
                return self.fallback_adapter.generate_structured(prompt, schema, system_prompt)
            raise LLMOutputValidationError(f"Upstream LLM execution failed: {str(e)}") from e
