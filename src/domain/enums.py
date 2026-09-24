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
    # Phase 14B — governed on-chain purchase + activation of Orbio CREDIT
    ORBIO_CREDIT_PURCHASE = "ORBIO_CREDIT_PURCHASE"
    # Phase 20B — governed activation of already-held CREDIT (no purchase)
    ORBIO_CREDIT_ACTIVATION = "ORBIO_CREDIT_ACTIVATION"
    # Phase 15 — productive work execution and revenue collection
    EXECUTE_WORK_ORDER = "EXECUTE_WORK_ORDER"
    COLLECT_REVENUE = "COLLECT_REVENUE"
    # Phase 17 — B2B marketplace and dynamic capability expansion
    PROPOSE_CAPABILITY_EXPANSION = "PROPOSE_CAPABILITY_EXPANSION"
    PUBLISH_MARKETPLACE_ORDER = "PUBLISH_MARKETPLACE_ORDER"
    CLAIM_MARKETPLACE_ORDER = "CLAIM_MARKETPLACE_ORDER"
    RELEASE_MARKETPLACE_ESCROW = "RELEASE_MARKETPLACE_ESCROW"

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

class CurrencyAsset(str, Enum):
    ORG_CREDIT = "ORG_CREDIT"
    USDG = "USDG"
    ORBIO_CREDIT = "ORBIO_CREDIT"

class WorkOrderStatus(str, Enum):
    PROPOSED = "PROPOSED"
    AUTHORIZED = "AUTHORIZED"
    IN_PROGRESS = "IN_PROGRESS"
    DELIVERED = "DELIVERED"
    VERIFIED = "VERIFIED"
    SETTLED = "SETTLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

class DeliverableStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"

class SolvencyRegime(str, Enum):
    EXPANSION = "EXPANSION"
    AUSTERE = "AUSTERE"
    STANDBY = "STANDBY"
