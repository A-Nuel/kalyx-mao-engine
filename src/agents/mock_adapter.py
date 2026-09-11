from typing import Type, TypeVar, Optional
from pydantic import BaseModel
from src.agents.base import IAgentAdapter
from src.agents.schemas import (
    MissionPlanOutput,
    TaskPlanItem,
    ResearchOutput,
    StrategyOutput,
    FinancialProposalOutput,
    ReplanOutput,
    MissionReviewOutput
)
from src.domain.enums import AgentRole, ActionType

T = TypeVar("T", bound=BaseModel)

class MockAgentAdapter(IAgentAdapter):
    """
    Deterministic mock agent adapter reproducing the exact 3-minute demo script scenario:
    1. CEO creates 3 tasks (Research, Strategy, Finance)
    2. Researcher yields market evidence
    3. Strategist ranks conservative vs speculative options
    4. Finance proposes an aggressive over-ceiling action (50 credits) to trigger policy rejection
    5. CEO receives rejection and replans with a compliant 20-credit action
    6. CEO outputs final mission review
    Can be configured to inject repeated failures for circuit-breaker testing.
    """
    def __init__(self, force_repeated_rejection: bool = False):
        self.force_repeated_rejection = force_repeated_rejection
        self.replan_call_count = 0

    def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        system_prompt: Optional[str] = None
    ) -> T:
        if schema == MissionPlanOutput:
            return MissionPlanOutput(
                plan_summary="Decompose 100-credit mission into market research, strategy comparison, and simulated execution.",
                rationale="Systematic division of labour ensures intelligence precedes capital allocation.",
                tasks=[
                    TaskPlanItem(task_id="task-01", assigned_role=AgentRole.RESEARCHER, objective="Research market opportunities", allocated_credits=10),
                    TaskPlanItem(task_id="task-02", assigned_role=AgentRole.STRATEGIST, objective="Rank strategy options", allocated_credits=10),
                    TaskPlanItem(task_id="task-03", assigned_role=AgentRole.FINANCIAL_ANALYST, objective="Formulate capital allocation proposal", allocated_credits=30)
                ]
            )

        elif schema == ResearchOutput:
            return ResearchOutput(
                market_trend="Volatile high-frequency liquidity pools show high slip; verified index funds show stable yield.",
                key_findings=[
                    "Unvetted crypto liquidity pool target.xyz has 42% risk profile",
                    "Regulated Sandbox Market Index Fund yields steady 12% risk-adjusted EV"
                ],
                recommended_focus="Focus allocation on verified sandbox assets",
                evidence_data={"liquidity_depth": "medium", "volatility_score": 0.35}
            )

        elif schema == StrategyOutput:
            return StrategyOutput(
                opportunities_ranked=[
                    "1. Speculative high-slippage arbitrage (High Return / Extreme Risk)",
                    "2. Conservative sandbox index allocation (Predictable Return / Minimal Risk)"
                ],
                risk_analysis="Aggressive option risks breaching single-action credit limits.",
                selected_strategy="Compare aggressive allocation first to probe limits, fallback to index.",
                expected_roi_score=0.85
            )

        elif schema == FinancialProposalOutput:
            return FinancialProposalOutput(
                target="http://unvetted-random-crypto-pool.xyz",
                action_type=ActionType.SIMULATED_ALLOCATION,
                requested_credits=50,  # Exceeds 25 ceiling AND target not on allowlist!
                expected_value_score=0.95,
                risk_assessment="Extreme volatility",
                rationale="Testing maximum upside allocation",
                parameters={"tier": "aggressive_arbitrage"}
            )

        elif schema == ReplanOutput:
            self.replan_call_count += 1
            if self.force_repeated_rejection:
                # Returns persistent invalid proposal to trigger circuit breaker
                return ReplanOutput(
                    rejection_analysis="Policy rejected proposal again.",
                    policy_rule_addressed="RULE-01",
                    remedy_description="Trying 48 credits instead (still invalid).",
                    new_proposal=FinancialProposalOutput(
                        target="http://unvetted-random-crypto-pool.xyz",
                        action_type=ActionType.SIMULATED_ALLOCATION,
                        requested_credits=48,
                        expected_value_score=0.90,
                        risk_assessment="Extreme",
                        rationale="Persistent over-budget proposal",
                        parameters={"tier": "aggressive_retry"}
                    )
                )

            return ReplanOutput(
                rejection_analysis="Initial proposal breached RULE-01 (SpendLimit > 25) and RULE-04 (Target not in allowlist).",
                policy_rule_addressed="RULE-01 & RULE-04",
                remedy_description="Downscale allocation to 20 credits and reroute to approved Sandbox Market Index Fund.",
                new_proposal=FinancialProposalOutput(
                    target="sandbox://market_index_fund",
                    action_type=ActionType.SIMULATED_ALLOCATION,
                    requested_credits=20,  # <= 25 authority ceiling!
                    expected_value_score=0.85,
                    risk_assessment="Low risk",
                    rationale="Safe, compliant allocation respecting organizational boundaries",
                    parameters={"tier": "conservative_index"}
                )
            )

        elif schema == MissionReviewOutput:
            return MissionReviewOutput(
                mission_success=True,
                summary="Mission completed with full governance compliance: 1 rejection caught, 1 replan executed successfully.",
                lessons_learned=[
                    "Policy boundaries successfully prevented rogue 50-credit drain.",
                    "CEO recovered deterministically within 1 replan cycle."
                ]
            )

        raise ValueError(f"MockAgentAdapter received unsupported schema: {schema}")
