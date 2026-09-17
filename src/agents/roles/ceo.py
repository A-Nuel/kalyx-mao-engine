import uuid
from typing import List, Dict, Any, Optional
from src.agents.base import BaseAgent
from src.agents.schemas import (
    MissionPlanOutput,
    ReplanOutput,
    MissionReviewOutput,
    FinancialProposalOutput
)
from src.domain.entities import ActionProposal

class CEOAgent(BaseAgent):
    def create_initial_plan(self, mission: str, treasury_budget: int) -> MissionPlanOutput:
        prompt = (
            f"Mission: {mission}\n"
            f"Total Budget: {treasury_budget} credits.\n"
            "Decompose this mission into specialist tasks for Researcher, Strategist, and Financial Analyst."
        )
        return self.adapter.generate_structured(prompt, MissionPlanOutput)

    def formulate_action_proposal(
        self,
        task_id: str,
        finance_output: FinancialProposalOutput
    ) -> ActionProposal:
        return ActionProposal(
            id=f"prop-{uuid.uuid4().hex[:8]}",
            task_id=task_id,
            proposing_agent_id=self.agent_id,
            action_type=finance_output.action_type,
            target=finance_output.target,
            parameters=finance_output.parameters,
            requested_credits=finance_output.requested_credits,
            expected_value_score=finance_output.expected_value_score,
            risk_assessment=finance_output.risk_assessment,
            rationale=finance_output.rationale
        )

    def replan_after_rejection(
        self,
        task_id: str,
        rejected_proposal: ActionProposal,
        violated_rule_id: str,
        violated_rule_description: str,
        attempt_number: int
    ) -> ActionProposal:
        prompt = (
            f"Proposal {rejected_proposal.id} was REJECTED by Policy Engine.\n"
            f"Violated Rule: {violated_rule_id} - {violated_rule_description}\n"
            f"Original Target: {rejected_proposal.target}\n"
            f"Original Credits: {rejected_proposal.requested_credits}\n"
            f"Attempt Number: {attempt_number}\n"
            "Generate a compliant replan adhering strictly to organizational limits."
        )
        replan_result: ReplanOutput = self.adapter.generate_structured(prompt, ReplanOutput)
        new_fin = replan_result.new_proposal
        
        return ActionProposal(
            id=f"prop-{uuid.uuid4().hex[:8]}",
            task_id=task_id,
            proposing_agent_id=self.agent_id,
            action_type=new_fin.action_type,
            target=new_fin.target,
            parameters=new_fin.parameters,
            requested_credits=new_fin.requested_credits,
            expected_value_score=new_fin.expected_value_score,
            risk_assessment=new_fin.risk_assessment,
            rationale=f"Replanned after {violated_rule_id}: {new_fin.rationale}"
        )

    def review_mission(self, mission: str, execution_history: List[Dict[str, Any]]) -> MissionReviewOutput:
        prompt = f"Mission: {mission}\nHistory: {execution_history}\nReview mission outcome."
        return self.adapter.generate_structured(prompt, MissionReviewOutput)

    def select_best_work_order(
        self,
        work_orders: List[Any],
        evaluations: Dict[str, Any],
    ) -> Optional[Any]:
        """
        Prioritize and select the best viable WorkOrder.
        Ranks by highest expected surplus, then highest margin ratio.
        """
        viable_orders = [
            wo for wo in work_orders
            if evaluations.get(wo.work_order_id) and evaluations[wo.work_order_id].viable
        ]
        if not viable_orders:
            return None
        return max(
            viable_orders,
            key=lambda wo: (
                evaluations[wo.work_order_id].expected_surplus_usdg,
                evaluations[wo.work_order_id].margin_ratio,
            ),
        )

