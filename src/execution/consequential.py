"""Authoritative Consequential Execution Boundary for Phase 10.

Enforces:
  Agent Proposal
  → Policy Authorization
  → Cryptographic Authorization / Capability Check
  → Consequential Execution Boundary (Durable State Machine + Escrow)
  → Provider Adapter
  → Independent Auditor

Crucial failure semantics:
- On timeout / ambiguous network transport failure:
  Operation transitions to UNKNOWN.
  Credits remain safely locked in ESCROW.
  Escrow is NOT rolled back.
  Reconciliation is required before retry.
- On explicit provider rejection / failure:
  Operation transitions to FAILED.
  Credits are safely rolled back from ESCROW to TREASURY.
- On provider success:
  Operation transitions to SUCCEEDED.
  Credits are committed from ESCROW to EXTERNAL_SINK.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.domain.entities import (
    ActionProposal,
    AuthorizationTokenClaims,
    ConsequentialOperation,
    ExecutionReceipt,
    Organisation,
    PolicyDecision,
)
from src.domain.enums import ActionType, OperationState, PolicyResult, ProviderOutcome
from src.domain.events import canonical_json
from src.domain.exceptions import (
    ExternalExecutionError,
    IdempotencyConflict,
    InvalidStateTransitionError,
    PolicyViolationError,
    UnauthorizedActionError,
)
from src.economy.ledger import DoubleEntryLedger, ESCROW, EXTERNAL_SINK, TREASURY
from src.governance.policy_engine import PolicyEngine
from src.security.capabilities import enforce_agent_capability
from src.security.token_consumption import AuthorizationTokenJournal, TokenAlreadyConsumed
from src.settlement.adapter import ConsequentialProviderAdapter, ProviderExecutionResult


class ConsequentialOperationRepository:
    """Backend-neutral repository for ConsequentialOperation entities."""

    def __init__(self, conn: Any):
        self.conn = conn

    def save(self, op: ConsequentialOperation) -> None:
        params = (
            op.id,
            op.tenant_id,
            op.organisation_id,
            op.proposal_id,
            op.decision_id,
            op.idempotency_key,
            op.action_type.value,
            op.target,
            json.dumps(op.parameters),
            op.amount,
            op.provider_name,
            op.provider_reference,
            op.state.value,
            op.error_message,
            op.created_at.isoformat(),
            op.updated_at.isoformat(),
        )
        with self.conn:
            # Upsert compatible with SQLite and PostgreSQL
            row = self.conn.execute(
                "SELECT id FROM consequential_operations WHERE id = ?", (op.id,)
            ).fetchone()
            if row:
                self.conn.execute(
                    """UPDATE consequential_operations SET
                        tenant_id = ?, organisation_id = ?, proposal_id = ?, decision_id = ?,
                        idempotency_key = ?, action_type = ?, target = ?, parameters = ?,
                        amount = ?, provider_name = ?, provider_reference = ?, state = ?,
                        error_message = ?, updated_at = ?
                        WHERE id = ?""",
                    (
                        op.tenant_id,
                        op.organisation_id,
                        op.proposal_id,
                        op.decision_id,
                        op.idempotency_key,
                        op.action_type.value,
                        op.target,
                        json.dumps(op.parameters),
                        op.amount,
                        op.provider_name,
                        op.provider_reference,
                        op.state.value,
                        op.error_message,
                        op.updated_at.isoformat(),
                        op.id,
                    ),
                )
            else:
                self.conn.execute(
                    """INSERT INTO consequential_operations (
                        id, tenant_id, organisation_id, proposal_id, decision_id,
                        idempotency_key, action_type, target, parameters, amount,
                        provider_name, provider_reference, state, error_message,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    params,
                )

    def get(self, op_id: str) -> Optional[ConsequentialOperation]:
        row = self.conn.execute(
            "SELECT * FROM consequential_operations WHERE id = ?", (op_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_op(row)

    def get_by_idempotency_key(self, key: str) -> Optional[ConsequentialOperation]:
        row = self.conn.execute(
            "SELECT * FROM consequential_operations WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_op(row)

    def list_for_org(self, org_id: str, state: Optional[str] = None) -> List[ConsequentialOperation]:
        if state:
            rows = self.conn.execute(
                "SELECT * FROM consequential_operations WHERE organisation_id = ? AND state = ? ORDER BY created_at DESC",
                (org_id, state),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM consequential_operations WHERE organisation_id = ? ORDER BY created_at DESC",
                (org_id,),
            ).fetchall()
        return [self._row_to_op(r) for r in rows]

    def _row_to_op(self, row: Any) -> ConsequentialOperation:
        raw_params = row["parameters"]
        params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
        return ConsequentialOperation(
            id=row["id"],
            tenant_id=row["tenant_id"],
            organisation_id=row["organisation_id"],
            proposal_id=row["proposal_id"],
            decision_id=row["decision_id"],
            idempotency_key=row["idempotency_key"],
            action_type=ActionType(row["action_type"]),
            target=row["target"],
            parameters=params,
            amount=row["amount"],
            provider_name=row["provider_name"],
            provider_reference=row["provider_reference"],
            state=OperationState(row["state"]),
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class ConsequentialExecutionManager:
    """Authoritative consequential execution boundary.

    Orchestrates the durable lifecycle:
    CREATED -> AUTHORIZED -> ESCROWED -> SUBMITTED -> SUCCEEDED / UNKNOWN / FAILED
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        ledger: DoubleEntryLedger,
        provider: ConsequentialProviderAdapter,
        event_store: Any = None,
        db_conn: Any = None,
    ):
        self.policy_engine = policy_engine
        self.ledger = ledger
        self.provider = provider
        self.event_store = event_store
        self.db_conn = db_conn
        self.token_journal = AuthorizationTokenJournal(db_conn) if db_conn is not None else None
        self.repo = ConsequentialOperationRepository(db_conn) if db_conn is not None else None

    def create_operation(
        self,
        proposal: ActionProposal,
        decision: PolicyDecision,
        org: Organisation,
        idempotency_key: Optional[str] = None,
    ) -> ConsequentialOperation:
        if decision.result != PolicyResult.APPROVED:
            raise PolicyViolationError(
                f"Execution rejected: Decision is '{decision.result.value}' (Rule: {decision.violated_rule_id})"
            )
        token = decision.authorization_token
        if not token:
            raise UnauthorizedActionError("Execution rejected: Missing authorization token")

        valid, reason = self.policy_engine.verify_token(token, proposal, org, decision=decision)
        if not valid:
            raise UnauthorizedActionError(f"Execution rejected: {reason}")
        enforce_agent_capability(proposal, org)

        key = idempotency_key or f"{org.id}:{proposal.id}"
        tenant_id = getattr(org, "tenant_id", "tenant-demo")

        if self.repo is not None:
            existing = self.repo.get_by_idempotency_key(key)
            if existing:
                # Validate tenant isolation
                if existing.tenant_id != tenant_id or existing.organisation_id != org.id:
                    raise UnauthorizedActionError("Cross-tenant or cross-organisation operation key conflict")
                # Fingerprint match check
                data = {
                    "tenant_id": tenant_id,
                    "organisation_id": org.id,
                    "proposal_id": proposal.id,
                    "decision_id": decision.id,
                    "idempotency_key": key,
                    "action_type": proposal.action_type.value,
                    "target": proposal.target,
                    "parameters": proposal.parameters,
                    "amount": proposal.requested_credits,
                    "provider_name": self.provider.name,
                }
                fingerprint = hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
                if existing.get_fingerprint() != fingerprint:
                    raise IdempotencyConflict(
                        f"Operation key '{key}' was already registered with different parameters"
                    )
                return existing

        op = ConsequentialOperation(
            id=f"cop-{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            organisation_id=org.id,
            proposal_id=proposal.id,
            decision_id=decision.id,
            idempotency_key=key,
            action_type=proposal.action_type,
            target=proposal.target,
            parameters=proposal.parameters,
            amount=proposal.requested_credits,
            provider_name=self.provider.name,
            state=OperationState.CREATED,
        )
        if self.repo is not None:
            self.repo.save(op)
        if self.event_store is not None:
            self.event_store.append_event(
                actor_id="CONSEQUENTIAL_EXECUTOR",
                event_type="CONSEQUENTIAL_OPERATION_CREATED",
                entity_id=op.id,
                payload=op.model_dump(),
            )
        return op

    def authorize_operation(
        self,
        operation: ConsequentialOperation,
        proposal: ActionProposal,
        decision: PolicyDecision,
        org: Organisation,
    ) -> None:
        token = decision.authorization_token
        if not token:
            raise UnauthorizedActionError("Missing authorization token")

        if self.token_journal is not None:
            try:
                parts = token.split(".")
                claims = AuthorizationTokenClaims.from_b64(parts[1])
                fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
                self.token_journal.consume(
                    nonce=claims.nonce,
                    org_id=org.id,
                    proposal_id=proposal.id,
                    proposal_content_hash=proposal.get_content_hash(),
                    decision_id=decision.id,
                    policy_version_hash=claims.policy_version_hash,
                    token_fingerprint=fingerprint,
                )
            except TokenAlreadyConsumed as exc:
                operation.transition_to(OperationState.FAILED, error_message="Authorization token already consumed")
                if self.repo is not None:
                    self.repo.save(operation)
                raise UnauthorizedActionError(
                    "Execution rejected: Token has already been consumed (replay barrier)"
                ) from exc

        operation.transition_to(OperationState.AUTHORIZED)
        if self.repo is not None:
            self.repo.save(operation)
        if self.event_store is not None:
            self.event_store.append_event(
                actor_id="CONSEQUENTIAL_EXECUTOR",
                event_type="CONSEQUENTIAL_OPERATION_AUTHORIZED",
                entity_id=operation.id,
                payload={"operation_id": operation.id, "state": operation.state.value},
            )

    def escrow_operation(self, operation: ConsequentialOperation, org: Organisation) -> None:
        if operation.state != OperationState.AUTHORIZED:
            raise InvalidStateTransitionError(
                f"Cannot escrow operation in state {operation.state.value} (must be AUTHORIZED)"
            )
        if operation.amount > 0:
            tx_id = f"res-{hashlib.sha256(operation.id.encode('utf-8')).hexdigest()[:16]}"
            self.ledger.transfer(
                from_account=TREASURY,
                to_account=ESCROW,
                amount=operation.amount,
                memo=f"Escrow lock for consequential operation {operation.id}",
                transaction_id=tx_id,
            )
            org.treasury_balance = self.ledger.get_balance(TREASURY)

        operation.transition_to(OperationState.ESCROWED)
        if self.repo is not None:
            self.repo.save(operation)
        if self.event_store is not None:
            self.event_store.append_event(
                actor_id="CONSEQUENTIAL_EXECUTOR",
                event_type="CONSEQUENTIAL_OPERATION_ESCROWED",
                entity_id=operation.id,
                payload={"operation_id": operation.id, "amount": operation.amount, "state": operation.state.value},
            )

    def submit_operation(
        self,
        operation: ConsequentialOperation,
        proposal: ActionProposal,
        decision: PolicyDecision,
        org: Organisation,
    ) -> ExecutionReceipt:
        if operation.state != OperationState.ESCROWED:
            raise InvalidStateTransitionError(
                f"Cannot submit operation in state {operation.state.value} (must be ESCROWED)"
            )

        operation.transition_to(OperationState.SUBMITTED)
        if self.repo is not None:
            self.repo.save(operation)
        if self.event_store is not None:
            self.event_store.append_event(
                actor_id="CONSEQUENTIAL_EXECUTOR",
                event_type="CONSEQUENTIAL_OPERATION_SUBMITTED",
                entity_id=operation.id,
                payload={"operation_id": operation.id, "target": operation.target, "state": operation.state.value},
            )

        # Dispatch to provider adapter
        try:
            result: ProviderExecutionResult = self.provider.execute(operation)
        except Exception as exc:
            # An unhandled transport exception during transit is AMBIGUOUS
            operation.transition_to(
                OperationState.UNKNOWN,
                error_message=f"Transport exception during dispatch: {exc}"
            )
            if self.repo is not None:
                self.repo.save(operation)
            if self.event_store is not None:
                self.event_store.append_event(
                    actor_id="CONSEQUENTIAL_EXECUTOR",
                    event_type="CONSEQUENTIAL_OPERATION_UNKNOWN",
                    entity_id=operation.id,
                    payload={"operation_id": operation.id, "error": str(exc), "escrow_locked": True},
                )
            raise ExternalExecutionError(
                f"Consequential execution outcome is UNKNOWN ({exc}); escrow remains locked pending reconciliation"
            ) from exc

        # 1. SUCCESS
        if result.outcome == ProviderOutcome.SUCCESS.value:
            if operation.amount > 0:
                token_hash = hashlib.sha256((decision.authorization_token or operation.id).encode("utf-8")).hexdigest()[:16]
                tx_id = f"tx-{token_hash}"
                self.ledger.transfer(
                    from_account=ESCROW,
                    to_account=EXTERNAL_SINK,
                    amount=operation.amount,
                    memo=f"Settlement commit for consequential operation {operation.id}",
                    transaction_id=tx_id,
                )
                org.treasury_balance = self.ledger.get_balance(TREASURY)

            operation.transition_to(OperationState.SUCCEEDED, provider_reference=result.provider_reference)
            if self.repo is not None:
                self.repo.save(operation)
            if self.event_store is not None:
                self.event_store.append_event(
                    actor_id="CONSEQUENTIAL_EXECUTOR",
                    event_type="CONSEQUENTIAL_OPERATION_SUCCEEDED",
                    entity_id=operation.id,
                    payload={
                        "operation_id": operation.id,
                        "reference": result.provider_reference,
                        "amount": operation.amount,
                        "evidence_hash": result.evidence_hash,
                    },
                )
            raw_hash = result.evidence_hash or hashlib.sha256(canonical_json(result.raw_response).encode("utf-8")).hexdigest()
            return ExecutionReceipt(
                id=str(uuid.uuid4()),
                proposal_id=proposal.id,
                authorization_token=decision.authorization_token or "TOKEN-MOCK",
                action_type=proposal.action_type,
                target=proposal.target,
                http_status=200,
                raw_response_hash=raw_hash,
                raw_output=result.raw_response,
                cost_credits=operation.amount,
                executed_at=datetime.utcnow(),
            )

        # 2. DEFINITIVE FAILURE
        if result.outcome == ProviderOutcome.FAILURE.value:
            if operation.amount > 0:
                token_hash = hashlib.sha256((decision.authorization_token or operation.id).encode("utf-8")).hexdigest()[:16]
                tx_id = f"rollback-{token_hash}"
                self.ledger.transfer(
                    from_account=ESCROW,
                    to_account=TREASURY,
                    amount=operation.amount,
                    memo=f"Escrow rollback for failed consequential operation {operation.id}",
                    transaction_id=tx_id,
                )
                org.treasury_balance = self.ledger.get_balance(TREASURY)

            operation.transition_to(OperationState.FAILED, error_message=result.error_message)
            if self.repo is not None:
                self.repo.save(operation)
            if self.event_store is not None:
                self.event_store.append_event(
                    actor_id="CONSEQUENTIAL_EXECUTOR",
                    event_type="CONSEQUENTIAL_OPERATION_FAILED",
                    entity_id=operation.id,
                    payload={"operation_id": operation.id, "reason": result.error_message, "escrow_refunded": True},
                )
            raise ExternalExecutionError(f"External execution rejected by provider: {result.error_message}")

        # 3. TIMEOUT / UNKNOWN
        # Escrow remains locked! Zero silent loss, zero double spend.
        operation.transition_to(
            OperationState.UNKNOWN,
            error_message=result.error_message or "Provider returned timeout/unknown outcome",
            provider_reference=result.provider_reference,
        )
        if self.repo is not None:
            self.repo.save(operation)
        if self.event_store is not None:
            self.event_store.append_event(
                actor_id="CONSEQUENTIAL_EXECUTOR",
                event_type="CONSEQUENTIAL_OPERATION_UNKNOWN",
                entity_id=operation.id,
                payload={"operation_id": operation.id, "error": result.error_message, "escrow_locked": True},
            )
        raise ExternalExecutionError(
            f"External execution outcome is UNKNOWN ({result.error_message}); escrow remains locked pending reconciliation"
        )

    def execute_proposal(
        self,
        proposal: ActionProposal,
        decision: PolicyDecision,
        org: Organisation,
        idempotency_key: Optional[str] = None,
    ) -> ExecutionReceipt:
        """High-level atomic coordinator for consequential proposal execution."""
        op = self.create_operation(proposal, decision, org, idempotency_key=idempotency_key)

        # Handle already processed operations (Idempotency)
        if op.state == OperationState.SUCCEEDED:
            return ExecutionReceipt(
                id=str(uuid.uuid4()),
                proposal_id=proposal.id,
                authorization_token=decision.authorization_token or "TOKEN-MOCK",
                action_type=proposal.action_type,
                target=proposal.target,
                http_status=200,
                raw_response_hash=hashlib.sha256(op.idempotency_key.encode("utf-8")).hexdigest(),
                raw_output={"idempotent_replay": True, "reference": op.provider_reference},
                cost_credits=op.amount,
                executed_at=datetime.utcnow(),
            )
        if op.state in (OperationState.UNKNOWN, OperationState.RECONCILING, OperationState.SUBMITTED):
            raise ExternalExecutionError(
                f"Operation '{op.id}' is unresolved ({op.state.value}); must be reconciled before retry"
            )
        if op.state == OperationState.FAILED:
            raise ExternalExecutionError(
                f"Operation '{op.id}' previously failed: {op.error_message}"
            )

        if op.state == OperationState.CREATED:
            self.authorize_operation(op, proposal, decision, org)
        if op.state == OperationState.AUTHORIZED:
            self.escrow_operation(op, org)
        if op.state == OperationState.ESCROWED:
            return self.submit_operation(op, proposal, decision, org)

        raise InvalidStateTransitionError(f"Unexpected operation state: {op.state.value}")
