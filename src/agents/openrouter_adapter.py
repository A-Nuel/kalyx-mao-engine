import os
import json
from typing import Type, TypeVar, Optional
import httpx
from pydantic import BaseModel
from src.agents.base import IAgentAdapter

T = TypeVar("T", bound=BaseModel)

class OpenRouterAgentAdapter(IAgentAdapter):
    """
    Pluggable OpenRouter LLM adapter.
    Uses OpenAI-compatible chat completions endpoint with structured JSON output enforcement.
    Requires OPENROUTER_API_KEY environment variable.
    """
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "openai/gpt-4o-mini",
        base_url: str = "https://openrouter.ai/api/v1"
    ):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.model = model
        self.base_url = base_url

    def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        system_prompt: Optional[str] = None
    ) -> T:
        if not self.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is not configured. Set the environment variable or pass api_key to the adapter."
            )

        schema_json = json.dumps(schema.model_json_schema())
        system_instruction = (
            (system_prompt or "You are an autonomous organizational agent.")
            + f"\nYou must strictly reply with valid JSON conforming to this JSON Schema:\n{schema_json}\n"
            "Do NOT include markdown formatting (no ```json code blocks), only raw parseable JSON."
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/autonomous-org",
            "X-Title": "Autonomous Org"
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2
        }

        with httpx.Client(timeout=45.0) as client:
            response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            raw_content = data["choices"][0]["message"]["content"]
            
            # Validate through Pydantic
            return schema.model_validate_json(raw_content)
