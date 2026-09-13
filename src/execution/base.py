import hashlib
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional, Set
from src.domain.entities import ActionProposal, PolicyDecision, ExecutionReceipt, Organisation, AuthorizationTokenClaims
from src.domain.enums import PolicyResult
from src.domain.exceptions import UnauthorizedActionError, PolicyViolationError
from src.governance.policy_engine import PolicyEngine
from src.economy.ledger import DoubleEntryLedger, TREASURY, ESCROW, EXTERNAL_SINK
from src.security.capabilities import enforce_agent_capability
from src.security.token_consumption import AuthorizationTokenJournal, TokenAlreadyConsumed


class BaseExecutor(ABC):
    """Trusted execution boundary between untrusted agents and resources."""

    def __init__(self, policy_engine: PolicyEngine, ledger: DoubleEntryLedger, db_conn: Any = None):
        self.policy_engine = policy_engine
        self.ledger = ledger
        # Process-local cache only; authoritative barrier is durable when db_conn is set.
        self._consumed_tokens: Set[str] = set()
        self._token_journal: Optional[AuthorizationTokenJournal] = (
            AuthorizationTokenJournal(db_conn) if db_conn is not None else None
        )

    def _verify_preconditions(self, proposal: ActionProposal, decision: PolicyDecision, org: Organisation) -> str:
        if decision.result != PolicyResult.APPROVED:
            raise PolicyViolationError(
                f"Execution rejected: Decision is '{decision.result.value}' (Rule: {decision.violated_rule_id})"
            )
        token = decision.authorization_token
        if not token:
            raise UnauthorizedActionError("Execution rejected: Missing authorization token")
        if token in self._consumed_tokens:
            raise UnauthorizedActionError(
                f"Execution rejected: Token has already been consumed (replay attack detected)"
            )
        valid, reason = self.policy_engine.verify_token(token, proposal, org, decision=decision)
        if not valid:
            raise UnauthorizedActionError(f"Execution rejected: {reason}")
        enforce_agent_capability(proposal, org)
        return token

    def _mark_token_consumed(self, token: str, proposal: ActionProposal, decision: PolicyDecision, org: Organisation) -> None:
        """Record single-use consumption durably when a journal is available."""
        if token in self._consumed_tokens:
            raise UnauthorizedActionError("Execution rejected: Token has already been consumed (replay attack detected)")
        if self._token_journal is not None:
            try:
                parts = token.split(".")
                claims = AuthorizationTokenClaims.from_b64(parts[1])
                fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
                self._token_journal.consume(
                    nonce=claims.nonce,
                    org_id=org.id,
                    proposal_id=proposal.id,
                    proposal_content_hash=proposal.get_content_hash(),
                    decision_id=decision.id,
                    policy_version_hash=claims.policy_version_hash,
                    token_fingerprint=fingerprint,
                )
            except TokenAlreadyConsumed as exc:
                raise UnauthorizedActionError(
                    "Execution rejected: Token has already been consumed (durable replay barrier)"
                ) from exc
        self._consumed_tokens.add(token)

    def _settle_ledger_fee(self, proposal: ActionProposal, token: str, org: Organisation) -> None:
        if proposal.requested_credits > 0:
            tx_id = f"tx-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(TREASURY, EXTERNAL_SINK, proposal.requested_credits, f"Execution fee for proposal {proposal.id}", transaction_id=tx_id)
            org.treasury_balance = self.ledger.get_balance(TREASURY)

    def _reserve_credits(self, proposal: ActionProposal, token: str, org: Organisation) -> Optional[str]:
        if proposal.requested_credits > 0:
            tx_id = f"res-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(TREASURY, ESCROW, proposal.requested_credits, f"Escrow reservation for proposal {proposal.id}", transaction_id=tx_id)
            org.treasury_balance = self.ledger.get_balance(TREASURY)
            return tx_id
        return None

    def _commit_reservation(self, proposal: ActionProposal, token: str, org: Organisation) -> None:
        if proposal.requested_credits > 0:
            tx_id = f"tx-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(ESCROW, EXTERNAL_SINK, proposal.requested_credits, f"Settlement commit for proposal {proposal.id}", transaction_id=tx_id)
            org.treasury_balance = self.ledger.get_balance(TREASURY)

    def _rollback_reservation(self, proposal: ActionProposal, token: str, org: Organisation) -> None:
        if proposal.requested_credits > 0:
            tx_id = f"rollback-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(ESCROW, TREASURY, proposal.requested_credits, f"Escrow rollback for failed proposal {proposal.id}", transaction_id=tx_id)
            org.treasury_balance = self.ledger.get_balance(TREASURY)

    @abstractmethod
    def execute(self, proposal: ActionProposal, decision: PolicyDecision, org: Organisation) -> ExecutionReceipt:
        pass
