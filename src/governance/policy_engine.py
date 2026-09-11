import hmac
import hashlib
import uuid
from typing import List, Optional, Tuple
from src.domain.entities import ActionProposal, PolicyDecision, Organisation
from src.domain.enums import PolicyResult, OrgState
from src.governance.rules import (
    PolicyRule,
    SpendLimitRule,
    TreasuryBalanceRule,
    RolePermissionRule,
    TargetAllowlistRule,
    OrgPauseRule,
    AgentStatusRule,
)

class PolicyEngine:
    """
    100% Deterministic policy evaluation engine.
    Evaluates ActionProposals against registered rules.
    Issues HMAC-signed cryptographic authorization tokens ONLY upon approval.
    """
    def __init__(
        self,
        rules: Optional[List[PolicyRule]] = None,
        human_approval_threshold: int = 40,
        signing_secret: Optional[str] = None
    ):
        self.rules = rules if rules is not None else [
            OrgPauseRule(),
            AgentStatusRule(),
            TreasuryBalanceRule(),
            SpendLimitRule(),
            RolePermissionRule(),
            TargetAllowlistRule()
        ]
        self.human_approval_threshold = human_approval_threshold
        self._secret = (signing_secret or str(uuid.uuid4())).encode("utf-8")

    def _generate_token(self, proposal: ActionProposal) -> str:
        content_hash = proposal.get_content_hash()
        signature = hmac.new(self._secret, content_hash.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"AUTH-{proposal.id[:8]}-{signature[:32]}"

    def verify_token(self, token: str, proposal: ActionProposal, org: Organisation) -> Tuple[bool, Optional[str]]:
        """
        Deterministically verifies that an authorization token:
        1. Is not empty
        2. Was signed with this PolicyEngine's secret
        3. Matches the exact proposal content hash (prevents post-approval mutation)
        4. Organisation is not in PAUSED state (emergency kill switch)
        """
        if not token:
            return False, "Empty or missing authorization token"
        
        if org.state == OrgState.PAUSED:
            return False, "Organisation is PAUSED by emergency kill switch"

        expected_token = self._generate_token(proposal)
        if not hmac.compare_digest(token, expected_token):
            return False, "Invalid token: signature does not match proposal content or was forged/modified"

        return True, None

    def evaluate(self, proposal: ActionProposal, org: Organisation) -> PolicyDecision:
        agent = org.agents.get(proposal.proposing_agent_id)
        if not agent:
            return PolicyDecision(
                id=str(uuid.uuid4()),
                proposal_id=proposal.id,
                result=PolicyResult.REJECTED,
                violated_rule_id="RULE-AUTH",
                violated_rule_description=f"Agent '{proposal.proposing_agent_id}' does not exist in organisation",
                evaluated_rules=["RULE-AUTH"],
                authorization_token=None
            )

        evaluated_rules: List[str] = []
        for rule in self.rules:
            evaluated_rules.append(rule.rule_id)
            violation = rule.evaluate(proposal, agent, org)
            if violation:
                return PolicyDecision(
                    id=str(uuid.uuid4()),
                    proposal_id=proposal.id,
                    result=PolicyResult.REJECTED,
                    violated_rule_id=rule.rule_id,
                    violated_rule_description=violation,
                    evaluated_rules=evaluated_rules,
                    authorization_token=None
                )

        if proposal.requested_credits >= self.human_approval_threshold:
            return PolicyDecision(
                id=str(uuid.uuid4()),
                proposal_id=proposal.id,
                result=PolicyResult.ESCALATE_TO_HUMAN,
                violated_rule_id="RULE-05",
                violated_rule_description=f"Requested {proposal.requested_credits} credits requires explicit human approval",
                evaluated_rules=evaluated_rules + ["RULE-05"],
                authorization_token=None
            )

        auth_token = self._generate_token(proposal)
        return PolicyDecision(
            id=str(uuid.uuid4()),
            proposal_id=proposal.id,
            result=PolicyResult.APPROVED,
            violated_rule_id=None,
            violated_rule_description=None,
            evaluated_rules=evaluated_rules,
            authorization_token=auth_token
        )
