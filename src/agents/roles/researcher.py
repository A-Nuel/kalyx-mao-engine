from src.agents.base import BaseAgent
from src.agents.schemas import ResearchOutput

class ResearcherAgent(BaseAgent):
    def conduct_research(self, objective: str) -> ResearchOutput:
        prompt = f"Objective: {objective}. Conduct market investigation and identify targets."
        return self.adapter.generate_structured(prompt, ResearchOutput)
