"""Governed model/tool budget boundary for the organisational orchestrator."""
from __future__ import annotations
from typing import Optional, Type, TypeVar
from pydantic import BaseModel
from src.agents.base import IAgentAdapter

T = TypeVar("T", bound=BaseModel)

class AgentBudgetExceeded(RuntimeError):
    pass

class BudgetedAgentAdapter(IAgentAdapter):
    """Caps orchestration calls before an external provider is contacted."""
    def __init__(self, delegate: IAgentAdapter, *, max_calls: int = 8, max_prompt_chars: int = 20_000) -> None:
        self.delegate = delegate
        self.max_calls = max(0, int(max_calls))
        self.max_prompt_chars = max(1, int(max_prompt_chars))
        self.calls_used = 0

    def generate_structured(self, prompt: str, schema: Type[T], system_prompt: Optional[str] = None) -> T:
        if self.calls_used >= self.max_calls:
            raise AgentBudgetExceeded(f"Orchestrator compute budget exhausted: {self.calls_used}/{self.max_calls} calls")
        if len(prompt) > self.max_prompt_chars:
            raise AgentBudgetExceeded("Orchestrator prompt budget exceeded")
        self.calls_used += 1
        return self.delegate.generate_structured(prompt, schema, system_prompt)

    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.calls_used)
