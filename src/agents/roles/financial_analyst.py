import math
from typing import Optional
from src.agents.base import BaseAgent
from src.agents.schemas import FinancialProposalOutput, StrategyOutput, UnitEconomicsEvaluation
from src.domain.work_order import WorkOrder

class FinancialAnalystAgent(BaseAgent):
    def formulate_proposal(self, strategy: StrategyOutput) -> FinancialProposalOutput:
        prompt = f"Strategy: {strategy.selected_strategy}. Propose specific resource allocation and target."
        return self.adapter.generate_structured(prompt, FinancialProposalOutput)

    def evaluate_unit_economics(
        self,
        work_order: WorkOrder,
        current_treasury_usdg: int,
        current_orbio_credits: int,
        micro_credits_per_usdg: int = 1_000_000,
        min_margin_ratio: float = 0.15,
    ) -> UnitEconomicsEvaluation:
        """
        Evaluate whether a WorkOrder is economically viable and profitable.
        
        Requires:
        1. Treasury solvency: Treasury USDG must cover any credit shortfall.
        2. Positive unit economics: Projected net margin >= min_margin_ratio (15%).
        """
        required_credits = work_order.required_orbio_credits
        credit_shortfall = max(0, required_credits - current_orbio_credits)
        
        # Calculate direct USDG cost needed to purchase missing credits (whole USDG)
        projected_direct_cost_usdg = (
            math.ceil(credit_shortfall / micro_credits_per_usdg)
            if credit_shortfall > 0
            else 0
        )
        
        bounty = work_order.bounty_amount
        projected_surplus = bounty - projected_direct_cost_usdg
        
        # Solvency check: do we have enough treasury to buy needed compute?
        if projected_direct_cost_usdg > current_treasury_usdg:
            return UnitEconomicsEvaluation(
                viable=False,
                margin_ratio=0.0,
                projected_direct_cost_usdg=projected_direct_cost_usdg,
                expected_surplus_usdg=projected_surplus,
                reason=f"INSUFFICIENT_TREASURY: Direct compute cost ({projected_direct_cost_usdg} USDG) exceeds treasury balance ({current_treasury_usdg} USDG)",
            )
        
        # Margin check: is margin >= min_margin_ratio?
        if bounty <= 0:
            return UnitEconomicsEvaluation(
                viable=False,
                margin_ratio=0.0,
                projected_direct_cost_usdg=projected_direct_cost_usdg,
                expected_surplus_usdg=projected_surplus,
                reason="ZERO_OR_NEGATIVE_BOUNTY: Work order has no revenue potential",
            )
            
        margin_ratio = round(projected_surplus / bounty, 4)
        if margin_ratio < min_margin_ratio:
            return UnitEconomicsEvaluation(
                viable=False,
                margin_ratio=margin_ratio,
                projected_direct_cost_usdg=projected_direct_cost_usdg,
                expected_surplus_usdg=projected_surplus,
                reason=f"MARGIN_BELOW_THRESHOLD: Projected margin {margin_ratio:.1%} is below minimum required {min_margin_ratio:.1%}",
            )
            
        return UnitEconomicsEvaluation(
            viable=True,
            margin_ratio=margin_ratio,
            projected_direct_cost_usdg=projected_direct_cost_usdg,
            expected_surplus_usdg=projected_surplus,
            reason=f"VIABLE: Projected margin {margin_ratio:.1%} exceeds threshold with {projected_surplus} USDG surplus",
        )
