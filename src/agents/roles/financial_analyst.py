from src.agents.base import BaseAgent
from src.agents.schemas import FinancialProposalOutput, StrategyOutput

class FinancialAnalystAgent(BaseAgent):
    def formulate_proposal(self, strategy: StrategyOutput) -> FinancialProposalOutput:
        prompt = f"Strategy: {strategy.selected_strategy}. Propose specific resource allocation and target."
        return self.adapter.generate_structured(prompt, FinancialProposalOutput)
