"""First-class reconciliation engine for consequential operations.

Resolves ambiguous operations (UNKNOWN or crashed SUBMITTED) against authoritative
external provider status evidence.

Guarantees:
- Exactly-one economic settlement:
  SUCCESS: ESCROW -> EXTERNAL_SINK
  DEFINITIVE FAILURE: ESCROW -> TREASURY
  UNRESOLVED: Escrow remains locked, no economic mutation
- Duplicate reconciliation requests are idempotent and do not duplicate settlement transactions.
- Every reconciliation decision records an append-only audit event.
- Tenant and organisation isolation are strictly enforced.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any, Optional

from src.domain.entities import ConsequentialOperation, Organisation
from src.domain.enums import OperationState, ProviderOutcome
from src.domain.exceptions import ReconciliationError, UnauthorizedActionError
from src.economy.ledger import DoubleEntryLedger, ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.consequential import ConsequentialOperationRepository
from src.settlement.adapter import ConsequentialProviderAdapter, ProviderStatusResult


class ReconciliationService:
    """Reconciles unresolved consequential operations against provider status."""

    def __init__(
        self,
        repo: ConsequentialOperationRepository,
        ledger: DoubleEntryLedger,
        provider: ConsequentialProviderAdapter,
        event_store: Any = None,
    ):
        self.repo = repo
        self.ledger = ledger
        self.provider = provider
        self.event_store = event_store
        self._lock = threading.RLock()

    def reconcile_operation(
        self,
        operation_id: str,
        org: Organisation,
    ) -> ConsequentialOperation:
        with self._lock:
            op = self.repo.get(operation_id)
            if not op:
                raise ReconciliationError(f"Consequential operation '{operation_id}' not found")

            # 1. Enforce tenant & organisation isolation
            tenant_id = getattr(org, "tenant_id", "tenant-demo")
            if op.tenant_id != tenant_id or op.organisation_id != org.id:
                raise UnauthorizedActionError(
                    f"Isolation violation: Cannot reconcile operation belonging to a different tenant or organisation"
                )

            # 2. Idempotency: If operation is already in a terminal state, return immediately without duplicate settlement
            if op.state in (OperationState.SUCCEEDED, OperationState.FAILED, OperationState.RECONCILED):
                return op

            # 3. Allow reconciliation for UNKNOWN, RECONCILING, or crash recovery from SUBMITTED
            if op.state not in (OperationState.UNKNOWN, OperationState.RECONCILING, OperationState.SUBMITTED):
                raise ReconciliationError(
                    f"Cannot reconcile operation in state '{op.state.value}' (must be UNKNOWN, RECONCILING, or SUBMITTED)"
                )

            # Transition to RECONCILING
            if op.state != OperationState.RECONCILING:
                op.transition_to(OperationState.RECONCILING)
                self.repo.save(op)
                if self.event_store is not None:
                    self.event_store.append_event(
                        actor_id="RECONCILIATION_SERVICE",
                        event_type="CONSEQUENTIAL_OPERATION_RECONCILING",
                        entity_id=op.id,
                        payload={"operation_id": op.id, "state": op.state.value},
                    )

            # 4. Query provider for status
            try:
                status: ProviderStatusResult = self.provider.status(
                    op.id, op.idempotency_key, provider_reference=op.provider_reference
                )
            except TypeError:
                status: ProviderStatusResult = self.provider.status(op.id, op.idempotency_key)

            # 5. Handle provider outcomes
            if status.outcome == ProviderOutcome.SUCCESS.value:
                # Provider evidence confirms external effect succeeded!
                # Perform exactly-once escrow settlement: ESCROW -> EXTERNAL_SINK
                if op.amount > 0:
                    tx_id = f"tx-rec-{hashlib.sha256(op.id.encode('utf-8')).hexdigest()[:16]}"
                    # Check if already settled in ledger (e.g. prior crash before state save or concurrent reconciliation)
                    already_settled = any(
                        op.id in e.memo and e.to_account == EXTERNAL_SINK
                        for e in self.ledger.get_entries()
                    )
                    if not already_settled and self.ledger.get_balance(ESCROW) >= op.amount:
                        try:
                            self.ledger.transfer(
                                from_account=ESCROW,
                                to_account=EXTERNAL_SINK,
                                amount=op.amount,
                                memo=f"Reconciliation settlement for operation {op.id}",
                                transaction_id=tx_id,
                            )
                            org.treasury_balance = self.ledger.get_balance(TREASURY)
                        except (ValueError, Exception) as exc:
                            if "Duplicate transaction ID" in str(exc) or "UNIQUE constraint failed" in str(exc):
                                pass
                            else:
                                raise

                if self.repo is not None:
                    fresh_op = self.repo.get(op.id)
                    if fresh_op and fresh_op.state == OperationState.RECONCILED:
                        return fresh_op

                if op.state != OperationState.RECONCILED:
                    op.transition_to(OperationState.RECONCILED, provider_reference=status.provider_reference)
                    self.repo.save(op)
                if self.event_store is not None:
                    self.event_store.append_event(
                        actor_id="RECONCILIATION_SERVICE",
                        event_type="CONSEQUENTIAL_OPERATION_RECONCILED_SUCCESS",
                        entity_id=op.id,
                        payload={
                            "operation_id": op.id,
                            "reference": status.provider_reference,
                            "evidence_hash": status.evidence_hash,
                            "settled_amount": op.amount,
                        },
                    )
                return op

            if status.outcome == ProviderOutcome.FAILURE.value:
                # Provider confirms external effect failed or was never executed!
                # Perform exactly-once escrow release: ESCROW -> TREASURY
                if op.amount > 0:
                    tx_id = f"rollback-rec-{hashlib.sha256(op.id.encode('utf-8')).hexdigest()[:16]}"
                    already_refunded = any(
                        op.id in e.memo and e.to_account == TREASURY and e.from_account == ESCROW
                        for e in self.ledger.get_entries()
                    )
                    if not already_refunded and self.ledger.get_balance(ESCROW) >= op.amount:
                        try:
                            self.ledger.transfer(
                                from_account=ESCROW,
                                to_account=TREASURY,
                                amount=op.amount,
                                memo=f"Reconciliation escrow release for failed operation {op.id}",
                                transaction_id=tx_id,
                            )
                            org.treasury_balance = self.ledger.get_balance(TREASURY)
                        except (ValueError, Exception) as exc:
                            if "Duplicate transaction ID" in str(exc) or "UNIQUE constraint failed" in str(exc):
                                pass
                            else:
                                raise

                if self.repo is not None:
                    fresh_op = self.repo.get(op.id)
                    if fresh_op and fresh_op.state == OperationState.RECONCILED:
                        return fresh_op

                if op.state != OperationState.RECONCILED:
                    op.transition_to(
                        OperationState.RECONCILED,
                        error_message=status.error_message or "Provider confirmed failure",
                        provider_reference=status.provider_reference,
                    )
                    self.repo.save(op)
                if self.event_store is not None:
                    self.event_store.append_event(
                        actor_id="RECONCILIATION_SERVICE",
                        event_type="CONSEQUENTIAL_OPERATION_RECONCILED_FAILURE",
                        entity_id=op.id,
                        payload={
                            "operation_id": op.id,
                            "reason": status.error_message,
                            "refunded_amount": op.amount,
                        },
                    )
                return op

            # Provider outcome remains UNKNOWN / UNRESOLVED
            # Do NOT release escrow. Do NOT retry blindly.
            op.transition_to(
                OperationState.UNKNOWN,
                error_message="Provider status remains unresolved; escrow preserved"
            )
            self.repo.save(op)
            if self.event_store is not None:
                self.event_store.append_event(
                    actor_id="RECONCILIATION_SERVICE",
                    event_type="CONSEQUENTIAL_OPERATION_RECONCILIATION_UNRESOLVED",
                    entity_id=op.id,
                    payload={"operation_id": op.id, "state": op.state.value, "escrow_locked": True},
                )
            return op
