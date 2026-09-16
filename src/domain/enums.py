from enum import Enum

class OrgState(str, Enum):
    INITIALIZING = "INITIALIZING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"

class AgentRole(str, Enum):
    CEO = "CEO"
    RESEARCHER = "RESEARCHER"
    STRATEGIST = "STRATEGIST"
    FINANCIAL_ANALYST = "FINANCIAL_ANALYST"

class AgentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PROBATION = "PROBATION"
    RESTRICTED = "RESTRICTED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"

class TaskStatus(str, Enum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class PolicyResult(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"

class ActionType(str, Enum):
    INTERNAL_ANALYSIS = "INTERNAL_ANALYSIS"
    DATA_FETCH = "DATA_FETCH"
    EXTERNAL_API_CALL = "EXTERNAL_API_CALL"
    SIMULATED_ALLOCATION = "SIMULATED_ALLOCATION"
    REPLAN = "REPLAN"
    BLOCKCHAIN_TRANSACTION = "BLOCKCHAIN_TRANSACTION"
    # Phase 14A — external economic resources (Orbio)
    ORBIO_KEY_LIFECYCLE = "ORBIO_KEY_LIFECYCLE"
    EXTERNAL_INFERENCE = "EXTERNAL_INFERENCE"

class OperationState(str, Enum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    ESCROWED = "escrowed"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILING = "reconciling"
    RECONCILED = "reconciled"

class ProviderOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"

class OrgSolvencyState(str, Enum):
    SOLVENT = "SOLVENT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    INSOLVENT = "INSOLVENT"
