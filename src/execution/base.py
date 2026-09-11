import uuid
import hashlib
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Set, Optional
from src.domain.entities import ActionProposal, PolicyDecision, ExecutionReceipt, Organisation
from src.domain.enums import PolicyResult
from src.domain.exceptions import UnauthorizedActionError, PolicyViolationError
from src.governance.policy_engine import PolicyEngine
from src.economy.ledger import DoubleEntryLedger, TREASURY, ESCROW, EXTERNAL_SINK

class BaseExecutor(ABC):
    """
    Abstract Base Executor.
    Enforces the Core Invariant: "Executors execute only what Policies authorize."
    1. Validates PolicyDecision is APPROVED
    2. Validates cryptographic authorization token integrity, TTL, org, and policy version
    3. Prevents token reuse / replay attack
    4. Enforces emergency kill switch (PAUSED check)
    5. Manages atomic credit reservation, commit, and rollback via Escrow
    6. Produces verifiable ExecutionReceipt
    """
    def __init__(self, policy_engine: PolicyEngine, ledger: DoubleEntryLedger):
        self.policy_engine = policy_engine
        self.ledger = ledger
        self._consumed_tokens: Set[str] = set()

    def _verify_preconditions(
        self,
        proposal: ActionProposal,
        decision: PolicyDecision,
        org: Organisation
    ) -> str:
        if decision.result != PolicyResult.APPROVED:
            raise PolicyViolationError(
                f"Execution rejected: Decision is '{decision.result.value}' (Rule: {decision.violated_rule_id})"
            )

        token = decision.authorization_token
        if not token:
            raise UnauthorizedActionError("Execution rejected: Missing authorization token")

        if token in self._consumed_tokens:
            raise UnauthorizedActionError(
                f"Execution rejected: Token '{token}' has already been consumed (replay attack detected)"
            )

        valid, reason = self.policy_engine.verify_token(token, proposal, org, decision=decision)
        if not valid:
            raise UnauthorizedActionError(f"Execution rejected: {reason}")

        return token

    def _settle_ledger_fee(self, proposal: ActionProposal, token: str, org: Organisation) -> None:
        """Legacy atomic single-step settlement for sandbox execution."""
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

    def _reserve_credits(self, proposal: ActionProposal, token: str, org: Organisation) -> Optional[str]:
        """Atomically lock requested credits in ESCROW before external dispatch."""
        if proposal.requested_credits > 0:
            tx_id = f"res-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(
                from_account=TREASURY,
                to_account=ESCROW,
                amount=proposal.requested_credits,
                memo=f"Escrow reservation for proposal {proposal.id}",
                transaction_id=tx_id
            )
            org.treasury_balance = self.ledger.get_balance(TREASURY)
            return tx_id
        return None

    def _commit_reservation(self, proposal: ActionProposal, token: str, org: Organisation) -> None:
        """Atomically commit escrowed credits to EXTERNAL_SINK upon verified successful execution."""
        if proposal.requested_credits > 0:
            tx_id = f"tx-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(
                from_account=ESCROW,
                to_account=EXTERNAL_SINK,
                amount=proposal.requested_credits,
                memo=f"Settlement commit for proposal {proposal.id}",
                transaction_id=tx_id
            )
            org.treasury_balance = self.ledger.get_balance(TREASURY)

    def _rollback_reservation(self, proposal: ActionProposal, token: str, org: Organisation) -> None:
        """Safely release escrowed credits back to TREASURY on execution failure."""
        if proposal.requested_credits > 0:
            tx_id = f"rollback-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(
                from_account=ESCROW,
                to_account=TREASURY,
                amount=proposal.requested_credits,
                memo=f"Escrow rollback for failed proposal {proposal.id}",
                transaction_id=tx_id
            )
            org.treasury_balance = self.ledger.get_balance(TREASURY)

    @abstractmethod
    def execute(
        self,
        proposal: ActionProposal,
        decision: PolicyDecision,
        org: Organisation
    ) -> ExecutionReceipt:
        pass
