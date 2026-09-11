from src.agents.base import BaseAgent
from src.agents.schemas import StrategyOutput, ResearchOutput

class StrategistAgent(BaseAgent):
    def evaluate_strategy(self, research: ResearchOutput) -> StrategyOutput:
        prompt = f"Research findings: {research.key_findings}. Rank strategic opportunities by risk/reward."
        return self.adapter.generate_structured(prompt, StrategyOutput)
