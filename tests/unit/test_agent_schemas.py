import pytest
from pydantic import ValidationError
from src.agents.schemas import (
    TaskPlanItem,
    MissionPlanOutput,
    ResearchOutput,
    StrategyOutput,
    FinancialProposalOutput,
    ReplanOutput,
    MissionReviewOutput
)
from src.domain.enums import AgentRole, ActionType

def test_task_plan_item_validation():
    item = TaskPlanItem(
        task_id="t-01",
        assigned_role=AgentRole.RESEARCHER,
        objective="Gather intel",
        allocated_credits=10
    )
    assert item.allocated_credits == 10
    
    with pytest.raises(ValidationError):
        TaskPlanItem(
            task_id="t-bad",
            assigned_role=AgentRole.RESEARCHER,
            objective="Gather intel",
            allocated_credits=-5
        )

def test_financial_proposal_output_validation():
    fin = FinancialProposalOutput(
        target="sandbox://market_index_fund",
        action_type=ActionType.SIMULATED_ALLOCATION,
        requested_credits=20,
        expected_value_score=0.85,
        risk_assessment="Low",
        rationale="Sound investment"
    )
    assert fin.requested_credits == 20
    assert fin.expected_value_score == 0.85

    with pytest.raises(ValidationError):
        FinancialProposalOutput(
            target="sandbox://market_index_fund",
            action_type=ActionType.SIMULATED_ALLOCATION,
            requested_credits=20,
            expected_value_score=1.5, # Out of range [0.0, 1.0]
            risk_assessment="Low",
            rationale="Invalid score"
        )
