import base64
import json
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from src.domain.enums import OrgState, AgentRole, AgentStatus, TaskStatus, PolicyResult, ActionType
from src.domain.events import compute_payload_hash, canonical_json

class AuthorizationTokenClaims(BaseModel):
    org_id: str
    proposal_id: str
    proposal_content_hash: str
    decision_id: str
    policy_version_hash: str
    issued_at: float
    expires_at: float
    nonce: str

    def to_b64(self) -> str:
        claims_dict = {
            "org_id": self.org_id,
            "proposal_id": self.proposal_id,
            "proposal_content_hash": self.proposal_content_hash,
            "decision_id": self.decision_id,
            "policy_version_hash": self.policy_version_hash,
            "issued_at": round(self.issued_at, 3),
            "expires_at": round(self.expires_at, 3),
            "nonce": self.nonce
        }
        canonical = canonical_json(claims_dict)
        return base64.urlsafe_b64encode(canonical.encode("utf-8")).decode("utf-8")

    @classmethod
    def from_b64(cls, b64_str: str) -> "AuthorizationTokenClaims":
        raw = base64.urlsafe_b64decode(b64_str.encode("utf-8")).decode("utf-8")
        data = json.loads(raw)
        return cls(**data)

class LedgerEntry(BaseModel):
    id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    transaction_id: str
    from_account: str
    to_account: str
    amount: int = Field(gt=0)
    memo: str

class AgentRecord(BaseModel):
    id: str
    role: AgentRole
    model_name: str = "gemini-2.5-flash"
    credit_balance: int = 0
    reputation_score: float = 100.0
    authority_ceiling: int = 25
    allowed_action_types: List[ActionType] = [ActionType.INTERNAL_ANALYSIS]
    status: AgentStatus = AgentStatus.ACTIVE
    successful_tasks: int = 0
    failed_tasks: int = 0
    policy_violations: int = 0
    performance_score: float = 100.0
    risk_score: float = 0.0
    resource_efficiency: float = 1.0
    reliability_score: float = 100.0
    task_history: List[str] = Field(default_factory=list)

class ActionProposal(BaseModel):
    id: str
    task_id: str
    proposing_agent_id: str
    action_type: ActionType
    target: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    requested_credits: int = Field(ge=0)
    expected_value_score: float = Field(ge=0.0, le=1.0)
    risk_assessment: str
    rationale: str
    evidence_hashes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def get_content_hash(self) -> str:
        raw = (
            f"{self.id}|{self.task_id}|{self.proposing_agent_id}|"
            f"{self.action_type.value}|{self.target}|{self.requested_credits}|"
            f"{compute_payload_hash(self.parameters)}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

class PolicyDecision(BaseModel):
    id: str
    proposal_id: str
    result: PolicyResult
    violated_rule_id: Optional[str] = None
    violated_rule_description: Optional[str] = None
    evaluated_rules: List[str] = Field(default_factory=list)
    authorization_token: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class ExecutionReceipt(BaseModel):
    id: str
    proposal_id: str
    authorization_token: str
    action_type: ActionType
    target: str
    http_status: Optional[int] = None
    raw_response_hash: str
    raw_output: Dict[str, Any] = Field(default_factory=dict)
    cost_credits: int = Field(ge=0)
    executed_at: datetime = Field(default_factory=datetime.utcnow)

class Task(BaseModel):
    id: str
    mission_id: str
    assigned_agent_id: Optional[str] = None
    objective: str
    allocated_credits: int = Field(ge=0)
    status: TaskStatus = TaskStatus.PENDING
    proposals: List[ActionProposal] = Field(default_factory=list)
    output_evidence: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Organisation(BaseModel):
    id: str
    mission: str
    tenant_id: str = "tenant-demo"
    treasury_balance: int = 100
    state: OrgState = OrgState.INITIALIZING
    agents: Dict[str, AgentRecord] = Field(default_factory=dict)
    active_policy_ids: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
