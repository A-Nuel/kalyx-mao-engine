import hashlib
import uuid
from typing import Dict, Optional
from src.domain.entities import ActionProposal, PolicyDecision, Organisation
from src.domain.enums import OrgState, PolicyResult

class HumanGate:
    """
    Human intervention controller:
    - Emergency kill switch (instant pause/resume)
    - Review and approve/reject escalated high-risk proposals
    """
    def __init__(self):
        self._pending_escalations: Dict[str, ActionProposal] = {}

    def trigger_kill_switch(self, org: Organisation, reason: str = "Operator intervention") -> None:
        org.state = OrgState.PAUSED

    def resume_from_pause(self, org: Organisation) -> None:
        if org.state == OrgState.PAUSED:
            org.state = OrgState.EXECUTING

    def submit_for_human_review(self, proposal: ActionProposal) -> None:
        self._pending_escalations[proposal.id] = proposal

    def approve_escalation(self, proposal_id: str, operator_id: str = "OPERATOR") -> PolicyDecision:
        proposal = self._pending_escalations.pop(proposal_id, None)
        if not proposal:
            raise KeyError(f"No pending escalation found for proposal {proposal_id}")

        raw = f"HUMAN-AUTH|{proposal_id}|{operator_id}"
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        auth_token = f"HUMAN-AUTH-{proposal_id[:8]}-{token_hash[:16]}"

        return PolicyDecision(
            id=str(uuid.uuid4()),
            proposal_id=proposal_id,
            result=PolicyResult.APPROVED,
            violated_rule_id=None,
            violated_rule_description=f"Approved by operator {operator_id}",
            evaluated_rules=["RULE-05-HUMAN-OVERRIDE"],
            authorization_token=auth_token
        )

    def reject_escalation(self, proposal_id: str, reason: str, operator_id: str = "OPERATOR") -> PolicyDecision:
        proposal = self._pending_escalations.pop(proposal_id, None)
        if not proposal:
            raise KeyError(f"No pending escalation found for proposal {proposal_id}")

        return PolicyDecision(
            id=str(uuid.uuid4()),
            proposal_id=proposal_id,
            result=PolicyResult.REJECTED,
            violated_rule_id="RULE-05-HUMAN-REJECTED",
            violated_rule_description=f"Rejected by operator {operator_id}: {reason}",
            evaluated_rules=["RULE-05-HUMAN-OVERRIDE"],
            authorization_token=None
        )
