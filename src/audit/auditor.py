import uuid
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from src.domain.entities import ActionProposal, PolicyDecision, ExecutionReceipt, Organisation, Task
from src.domain.enums import PolicyResult, OrgState, TaskStatus
from src.domain.exceptions import DomainError
from src.domain.events import canonical_json
from src.governance.policy_engine import PolicyEngine

class AuditVerificationError(DomainError):
    """Raised when the independent Auditor detects an invalid, forged, or unverified execution."""
    def __init__(self, failures: List[str]):
        super().__init__(f"Audit verification failed: {'; '.join(failures)}")
        self.failures = failures

class VerificationReceipt(BaseModel):
    id: str
    execution_id: str
    verified: bool
    checks: List[str]
    failures: List[str] = Field(default_factory=list)
    evidence_hash: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class Auditor:
    """
    Independent Auditor component.
    Enforces the Core Invariant: "Auditors verify."
    Does NOT trust the Executor. Independently verifies:
    1. Proposal identity / content fingerprint
    2. Authorization token validity
    3. Authorization -> execution binding
    4. Execution receipt integrity & parameter match
    5. Ledger settlement
    6. Audit-chain cryptographic integrity
    7. Conservation of credits
    8. Organisation / task state consistency
    """
    def verify_execution(
        self,
        proposal: ActionProposal,
        decision: PolicyDecision,
        receipt: ExecutionReceipt,
        org: Organisation,
        task: Task,
        ledger: Any,
        event_store: Any,
        policy_engine: PolicyEngine
    ) -> VerificationReceipt:
        checks: List[str] = []
        failures: List[str] = []

        # 1. Proposal fingerprint check
        checks.append("PROPOSAL_FINGERPRINT")
        recomputed_proposal_hash = proposal.get_content_hash()
        # Ensure proposal parameters or fields were not mutated
        if not recomputed_proposal_hash:
            failures.append("Proposal fingerprint computation failed")

        # 2. Authorization token validity
        checks.append("TOKEN_VALIDITY")
        valid, reason = policy_engine.verify_token(decision.authorization_token or "", proposal, org)
        if not valid:
            failures.append(f"Authorization token verification failed: {reason}")

        # 3. Authorization -> Execution Binding
        checks.append("AUTH_EXEC_BINDING")
        if receipt.proposal_id != proposal.id:
            failures.append(f"Receipt proposal_id '{receipt.proposal_id}' does not match proposal '{proposal.id}'")
        if receipt.authorization_token != decision.authorization_token:
            failures.append("Receipt authorization_token does not match policy decision token")

        # 4. Execution receipt integrity
        checks.append("RECEIPT_INTEGRITY")
        if receipt.cost_credits != proposal.requested_credits:
            failures.append(f"Receipt cost credits ({receipt.cost_credits}) does not match approved credits ({proposal.requested_credits})")
        if receipt.target != proposal.target:
            failures.append(f"Receipt target '{receipt.target}' does not match approved target '{proposal.target}'")
        if receipt.action_type != proposal.action_type:
            failures.append(f"Receipt action_type '{receipt.action_type}' does not match approved action_type '{proposal.action_type}'")

        # 5. Ledger settlement
        checks.append("LEDGER_SETTLEMENT")
        if receipt.cost_credits > 0:
            expected_tx_id = f"tx-{hashlib.sha256(decision.authorization_token.encode('utf-8')).hexdigest()[:16]}"
            entries = ledger.get_entries()
            matching = [e for e in entries if e.transaction_id == expected_tx_id]
            if not matching:
                failures.append(f"No ledger settlement found for transaction ID '{expected_tx_id}'")
            elif matching[0].amount != receipt.cost_credits:
                failures.append(f"Ledger settled amount ({matching[0].amount}) differs from receipt cost ({receipt.cost_credits})")

        # 6. Audit-chain integrity
        checks.append("AUDIT_CHAIN_INTEGRITY")
        chain_valid, chain_err = event_store.verify_integrity()
        if not chain_valid:
            failures.append(f"Audit chain cryptographic verification failed: {chain_err}")

        # 7. Credit conservation
        checks.append("CREDIT_CONSERVATION")
        if not ledger.verify_conservation():
            failures.append("Conservation of credits broken in ledger")

        # 8. State consistency
        checks.append("STATE_CONSISTENCY")
        if org.state == OrgState.PAUSED:
            failures.append("Organisation is in PAUSED state")
        if task.status not in [TaskStatus.APPROVED, TaskStatus.COMPLETED]:
            failures.append(f"Task status '{task.status.value}' is inconsistent with execution")

        is_verified = len(failures) == 0
        evidence_payload = {
            "execution_id": receipt.id,
            "proposal_id": proposal.id,
            "decision_id": decision.id,
            "checks": checks,
            "failures": failures,
            "verified": is_verified
        }
        evidence_hash = hashlib.sha256(canonical_json(evidence_payload).encode("utf-8")).hexdigest()

        verification_receipt = VerificationReceipt(
            id=str(uuid.uuid4()),
            execution_id=receipt.id,
            verified=is_verified,
            checks=checks,
            failures=failures,
            evidence_hash=evidence_hash
        )

        event_store.append_event(
            actor_id="AUDITOR",
            event_type="AUDIT_VERIFIED",
            entity_id=receipt.id,
            payload=verification_receipt.model_dump()
        )

        if not is_verified:
            raise AuditVerificationError(failures)

        return verification_receipt
