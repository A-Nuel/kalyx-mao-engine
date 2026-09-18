from typing import List, Dict, Any
from pydantic import BaseModel, Field
from src.domain.enums import AgentRole, ActionType

class TaskPlanItem(BaseModel):
    task_id: str
    assigned_role: AgentRole
    objective: str
    allocated_credits: int = Field(ge=0)

class MissionPlanOutput(BaseModel):
    plan_summary: str
    rationale: str
    tasks: List[TaskPlanItem]

class ResearchOutput(BaseModel):
    market_trend: str
    key_findings: List[str]
    recommended_focus: str
    evidence_data: Dict[str, Any] = Field(default_factory=dict)

class StrategyOutput(BaseModel):
    opportunities_ranked: List[str]
    risk_analysis: str
    selected_strategy: str
    expected_roi_score: float = Field(ge=0.0, le=1.0)

class FinancialProposalOutput(BaseModel):
    target: str
    action_type: ActionType
    requested_credits: int = Field(ge=0)
    expected_value_score: float = Field(ge=0.0, le=1.0)
    risk_assessment: str
    rationale: str
    parameters: Dict[str, Any] = Field(default_factory=dict)

class ReplanOutput(BaseModel):
    rejection_analysis: str
    policy_rule_addressed: str
    remedy_description: str
    new_proposal: FinancialProposalOutput

class MissionReviewOutput(BaseModel):
    mission_success: bool
    summary: str
    lessons_learned: List[str]

class UnitEconomicsEvaluation(BaseModel):
    viable: bool
    margin_ratio: float
    projected_direct_cost_usdg: int
    expected_surplus_usdg: int
    reason: str

