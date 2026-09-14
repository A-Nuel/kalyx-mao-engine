import base64
import json
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field
from src.domain.enums import OrgState, AgentRole, AgentStatus, TaskStatus, PolicyResult, ActionType, OperationState, ProviderOutcome
from src.domain.events import compute_payload_hash, canonical_json
from src.domain.exceptions import InvalidStateTransitionError

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

VALID_OPERATION_TRANSITIONS: Dict[OperationState, Set[OperationState]] = {
    OperationState.CREATED: {OperationState.AUTHORIZED, OperationState.FAILED},
    OperationState.AUTHORIZED: {OperationState.ESCROWED, OperationState.FAILED},
    OperationState.ESCROWED: {OperationState.SUBMITTED, OperationState.FAILED},
    OperationState.SUBMITTED: {OperationState.SUCCEEDED, OperationState.FAILED, OperationState.UNKNOWN, OperationState.RECONCILING},
    OperationState.UNKNOWN: {OperationState.RECONCILING},
    OperationState.RECONCILING: {OperationState.RECONCILED, OperationState.UNKNOWN},
    OperationState.SUCCEEDED: set(),
    OperationState.FAILED: set(),
    OperationState.RECONCILED: set(),
}

class ConsequentialOperation(BaseModel):
    id: str
    tenant_id: str = "tenant-demo"
    organisation_id: str
    proposal_id: str
    decision_id: str
    idempotency_key: str
    action_type: ActionType
    target: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    amount: int = Field(ge=0, default=0)
    provider_name: str
    provider_reference: Optional[str] = None
    state: OperationState = OperationState.CREATED
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def transition_to(
        self,
        new_state: OperationState,
        error_message: Optional[str] = None,
        provider_reference: Optional[str] = None
    ) -> None:
        allowed = VALID_OPERATION_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise InvalidStateTransitionError(
                f"Invalid operation state transition: {self.state.value} -> {new_state.value}"
            )
        self.state = new_state
        if error_message is not None:
            self.error_message = error_message
        if provider_reference is not None:
            self.provider_reference = provider_reference
        self.updated_at = datetime.utcnow()

    def get_fingerprint(self) -> str:
        data = {
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "proposal_id": self.proposal_id,
            "decision_id": self.decision_id,
            "idempotency_key": self.idempotency_key,
            "action_type": self.action_type.value,
            "target": self.target,
            "parameters": self.parameters,
            "amount": self.amount,
            "provider_name": self.provider_name,
        }
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()

