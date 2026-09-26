from pydantic import BaseModel

import pytest

from src.orchestration.budget import AgentBudgetExceeded, BudgetedAgentAdapter


class Output(BaseModel):
    ok: bool


class Delegate:
    def __init__(self):
        self.calls = 0

    def generate_structured(self, prompt, schema, system_prompt=None):
        self.calls += 1
        return schema(ok=True)


def test_budgeted_agent_adapter_has_hard_call_ceiling():
    delegate = Delegate()
    adapter = BudgetedAgentAdapter(delegate, max_calls=1)
    assert adapter.generate_structured("x", Output).ok is True
    with pytest.raises(AgentBudgetExceeded):
        adapter.generate_structured("x", Output)
    assert delegate.calls == 1


def test_budgeted_agent_adapter_rejects_oversized_prompt():
    delegate = Delegate()
    adapter = BudgetedAgentAdapter(delegate, max_calls=2, max_prompt_chars=3)
    with pytest.raises(AgentBudgetExceeded):
        adapter.generate_structured("abcd", Output)
    assert delegate.calls == 0
