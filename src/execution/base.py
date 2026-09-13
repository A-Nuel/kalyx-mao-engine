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
from src.security.capabilities import enforce_agent_capability


class BaseExecutor(ABC):
    """Trusted execution boundary between untrusted agents and resources."""

    def __init__(self, policy_engine: PolicyEngine, ledger: DoubleEntryLedger):
        self.policy_engine = policy_engine
        self.ledger = ledger
        self._consumed_tokens: Set[str] = set()

    def _verify_preconditions(
        self,
        proposal: ActionProposal,
        decision: PolicyDecision,
        org: Organisation,
    ) -> str:
        if decision.result != PolicyResult.APPROVED:
            raise PolicyViolationError(
                f"Execution rejected: Decision is '{decision.result.value}' (Rule: {decision.violated_rule_id})"
            )
        # Least privilege is checked independently of the LLM and policy prompt.
        enforce_agent_capability(proposal, org)
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
                transaction_id=tx_id,
            )
            org.treasury_balance = self.ledger.get_balance(TREASURY)

    def _reserve_credits(self, proposal: ActionProposal, token: str, org: Organisation) -> Optional[str]:
        if proposal.requested_credits > 0:
            tx_id = f"res-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(
                from_account=TREASURY,
                to_account=ESCROW,
                amount=proposal.requested_credits,
                memo=f"Escrow reservation for proposal {proposal.id}",
                transaction_id=tx_id,
            )
            org.treasury_balance = self.ledger.get_balance(TREASURY)
            return tx_id
        return None

    def _commit_reservation(self, proposal: ActionProposal, token: str, org: Organisation) -> None:
        if proposal.requested_credits > 0:
            tx_id = f"tx-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(
                from_account=ESCROW,
                to_account=EXTERNAL_SINK,
                amount=proposal.requested_credits,
                memo=f"Settlement commit for proposal {proposal.id}",
                transaction_id=tx_id,
            )
            org.treasury_balance = self.ledger.get_balance(TREASURY)

    def _rollback_reservation(self, proposal: ActionProposal, token: str, org: Organisation) -> None:
        if proposal.requested_credits > 0:
            tx_id = f"rollback-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(
                from_account=ESCROW,
                to_account=TREASURY,
                amount=proposal.requested_credits,
                memo=f"Escrow rollback for failed proposal {proposal.id}",
                transaction_id=tx_id,
            )
            org.treasury_balance = self.ledger.get_balance(TREASURY)

    @abstractmethod
    def execute(self, proposal: ActionProposal, decision: PolicyDecision, org: Organisation) -> ExecutionReceipt:
        pass
