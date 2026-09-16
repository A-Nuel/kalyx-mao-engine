from abc import ABC, abstractmethod
from typing import Optional, Set, Any
from src.domain.entities import ActionProposal, Organisation, AgentRecord
from src.domain.enums import OrgState, AgentStatus, ActionType

class PolicyRule(ABC):
    rule_id: str
    description: str

    @abstractmethod
    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        """Returns None if passed, or rejection reason if violated."""
        pass

class SpendLimitRule(PolicyRule):
    rule_id = "RULE-01"
    description = "Requested credits cannot exceed agent authority ceiling"

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if proposal.requested_credits > agent.authority_ceiling:
            return f"Requested {proposal.requested_credits} credits exceeds authority ceiling of {agent.authority_ceiling}"
        return None

class TreasuryBalanceRule(PolicyRule):
    rule_id = "RULE-02"
    description = "Requested credits cannot exceed organisation treasury balance"

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        ledger = kwargs.get("ledger")
        available = ledger.get_balance("TREASURY") if ledger is not None else org.treasury_balance
        if proposal.requested_credits > available:
            return f"Requested {proposal.requested_credits} credits exceeds available treasury ({available})"
        return None

class RolePermissionRule(PolicyRule):
    rule_id = "RULE-03"
    description = "Action type must be in agent allowed action types"

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if proposal.action_type not in agent.allowed_action_types:
            return f"Action type '{proposal.action_type.value}' is not permitted for role '{agent.role.value}'"
        return None

class TargetAllowlistRule(PolicyRule):
    rule_id = "RULE-04"
    description = "Target endpoint must be on the approved allowlist"

    def __init__(self, approved_targets: Optional[Set[str]] = None):
        self.approved_targets = approved_targets or {
            "sandbox://market_index_fund",
            "sandbox://verified_bonds",
            "api://market_data/v1/summary",
            "internal://research_synthesis"
        }

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if proposal.action_type == ActionType.BLOCKCHAIN_TRANSACTION or proposal.target.startswith(("blockchain://", "evm://", "sepolia://")):
            return None
        if proposal.target not in self.approved_targets:
            return f"Target '{proposal.target}' is not on the approved destination allowlist"
        return None

class OrgPauseRule(PolicyRule):
    rule_id = "RULE-06"
    description = "No actions may execute when organisation is paused"

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if org.state == OrgState.PAUSED:
            return "Organisation is PAUSED by emergency kill switch"
        return None

class AgentStatusRule(PolicyRule):
    rule_id = "RULE-07"
    description = "Suspended or retired agents cannot propose actions; restricted agents can only propose internal analysis with zero credits"

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if agent.status in {AgentStatus.SUSPENDED, AgentStatus.RETIRED}:
            return f"Agent '{agent.id}' is {agent.status.value} and cannot propose actions"
        if agent.status == AgentStatus.RESTRICTED:
            if proposal.action_type != ActionType.INTERNAL_ANALYSIS:
                return f"Agent '{agent.id}' is RESTRICTED and can only perform INTERNAL_ANALYSIS"
            if proposal.requested_credits > 0:
                return f"Agent '{agent.id}' is RESTRICTED and cannot request credits (>0)"
        return None
