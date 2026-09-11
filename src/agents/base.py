from abc import ABC, abstractmethod
from typing import Type, TypeVar, Optional
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class IAgentAdapter(ABC):
    """
    Port for LLM reasoning.
    Takes prompt and Pydantic schema, returns validated model instance.
    Never has access to executors, ledger, or system state directly.
    """
    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        system_prompt: Optional[str] = None
    ) -> T:
        pass

class BaseAgent(ABC):
    def __init__(self, agent_id: str, adapter: IAgentAdapter):
        self.agent_id = agent_id
        self.adapter = adapter
