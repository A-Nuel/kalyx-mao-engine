import uuid
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional, Set
from src.domain.entities import ActionProposal, PolicyDecision, ExecutionReceipt, Organisation
from src.domain.enums import PolicyResult
from src.domain.exceptions import UnauthorizedActionError, PolicyViolationError
from src.governance.policy_engine import PolicyEngine
from src.economy.ledger import DoubleEntryLedger, TREASURY, EXTERNAL_SINK

class SandboxExecutor:
    """
    Deterministic execution service.
    Enforces the Critical Invariant:
    1. Only executes if PolicyDecision is APPROVED
    2. Validates unforgeable HMAC authorization token
    3. Prevents token reuse / replay
    4. Enforces emergency kill switch (PAUSED check)
    5. Deducts approved credits atomically from ledger
    6. Produces verifiable ExecutionReceipt
    """
    def __init__(self, policy_engine: PolicyEngine, ledger: DoubleEntryLedger):
        self.policy_engine = policy_engine
        self.ledger = ledger
        self._consumed_tokens: Set[str] = set()

    def execute(
        self,
        proposal: ActionProposal,
        decision: PolicyDecision,
        org: Organisation
    ) -> ExecutionReceipt:
        # Check 1: Must be explicitly approved
        if decision.result != PolicyResult.APPROVED:
            raise PolicyViolationError(
                f"Execution rejected: Decision is '{decision.result.value}' (Rule: {decision.violated_rule_id})"
            )

        token = decision.authorization_token
        if not token:
            raise UnauthorizedActionError("Execution rejected: Missing authorization token")

        # Check 2: Token replay protection
        if token in self._consumed_tokens:
            raise UnauthorizedActionError(
                f"Execution rejected: Token '{token}' has already been consumed (replay attack detected)"
            )

        # Check 3: Cryptographic signature & proposal payload integrity & kill-switch status
        valid, reason = self.policy_engine.verify_token(token, proposal, org)
        if not valid:
            raise UnauthorizedActionError(f"Execution rejected: {reason}")

        # Mark token consumed BEFORE side effects
        self._consumed_tokens.add(token)

        # Transfer credits if required
        if proposal.requested_credits > 0:
            tx_id = f"tx-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(
                from_account=TREASURY,
                to_account=EXTERNAL_SINK,
                amount=proposal.requested_credits,
                memo=f"Execution fee for proposal {proposal.id}",
                transaction_id=tx_id
            )
            org.treasury_balance = self.ledger.get_balance(TREASURY)

        # Simulate deterministic sandboxed execution output
        output_payload = {
            "status": "SUCCESS",
            "target": proposal.target,
            "action_type": proposal.action_type.value,
            "simulated_value": round(proposal.requested_credits * proposal.expected_value_score * 1.5, 2),
            "executed_parameters": proposal.parameters
        }
        raw_hash = hashlib.sha256(str(output_payload).encode("utf-8")).hexdigest()

        return ExecutionReceipt(
            id=str(uuid.uuid4()),
            proposal_id=proposal.id,
            authorization_token=token,
            action_type=proposal.action_type,
            target=proposal.target,
            http_status=200,
            raw_response_hash=raw_hash,
            raw_output=output_payload,
            cost_credits=proposal.requested_credits,
            executed_at=datetime.utcnow()
        )
