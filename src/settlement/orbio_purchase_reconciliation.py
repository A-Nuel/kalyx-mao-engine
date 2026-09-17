"""Phase 14B.8 — UNKNOWN reconciliation for Orbio CREDIT purchases.

Extends Phase 10 reconciliation with purchase-intent verification:

  UNKNOWN / SUBMITTED
        ↓
  provider.status
        ↓
  OrbioPurchaseVerifier (14B.7)
        ↓
  VERIFIED  → settle ESCROW → EXTERNAL_SINK → RECONCILED
  REJECTED  → refund ESCROW → TREASURY → RECONCILED (failed)
  PENDING   → remain UNKNOWN, escrow locked (no economic mutation)

Never promotes TIMEOUT/UNKNOWN to SUCCESS without verified evidence.
Does not create a parallel escrow or settlement engine.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Any, Optional

from src.domain.blockchain import OrbioPurchaseIntent
from src.domain.entities import ConsequentialOperation, Organisation
from src.domain.enums import ActionType, OperationState, ProviderOutcome
from src.domain.exceptions import ReconciliationError, UnauthorizedActionError
from src.economy.ledger import DoubleEntryLedger, ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.consequential import ConsequentialOperationRepository
from src.settlement.adapter import ConsequentialProviderAdapter, ProviderStatusResult
from src.settlement.orbio_purchase_verifier import (
    OrbioPurchaseVerifier,
    PurchaseVerificationReport,
    PurchaseVerificationResult,
)


@dataclass(frozen=True)
class PurchaseReconciliationOutcome:
    operation: ConsequentialOperation
    verification: Optional[PurchaseVerificationReport]
    provider_status: Optional[ProviderStatusResult]
    economic_action: str  # settled | refunded | none


class OrbioPurchaseReconciliation:
    """Reconcile ambiguous Orbio purchase operations with intent-bound verification."""

    def __init__(
        self,
        *,
        provider: ConsequentialProviderAdapter,
        ledger: DoubleEntryLedger,
        repo: Optional[ConsequentialOperationRepository] = None,
        verifier: Optional[OrbioPurchaseVerifier] = None,
        event_store: Any = None,
    ):
        self.provider = provider
        self.ledger = ledger
        self.repo = repo
        self.verifier = verifier or OrbioPurchaseVerifier()
        self.event_store = event_store
        self._lock = threading.RLock()

    def reconcile(
        self,
        operation: ConsequentialOperation,
        intent: OrbioPurchaseIntent,
        org: Organisation,
    ) -> PurchaseReconciliationOutcome:
        with self._lock:
            return self._reconcile_locked(operation, intent, org)

    def reconcile_by_id(
        self,
        operation_id: str,
        intent: OrbioPurchaseIntent,
        org: Organisation,
    ) -> PurchaseReconciliationOutcome:
        if self.repo is None:
            raise ReconciliationError("No operation repository configured")
        op = self.repo.get(operation_id)
        if not op:
            raise ReconciliationError(f"Operation '{operation_id}' not found")
        return self.reconcile(op, intent, org)

    def _reconcile_locked(
        self,
        op: ConsequentialOperation,
        intent: OrbioPurchaseIntent,
        org: Organisation,
    ) -> PurchaseReconciliationOutcome:
        tenant_id = getattr(org, "tenant_id", "tenant-demo")
        if op.tenant_id != tenant_id or op.organisation_id != org.id:
            raise UnauthorizedActionError(
                "Isolation violation: cannot reconcile purchase for another tenant/org"
            )
        if intent.organisation_id != org.id or intent.tenant_id != tenant_id:
            raise UnauthorizedActionError(
                "Purchase intent tenant/org does not match reconciliation organisation"
            )
        if op.action_type not in {
            ActionType.ORBIO_CREDIT_PURCHASE,
            ActionType.BLOCKCHAIN_TRANSACTION,
        }:
            raise ReconciliationError(
                f"Orbio purchase reconciliation does not apply to action_type={op.action_type.value}"
            )

        # Terminal states: idempotent no-op
        if op.state in {
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.RECONCILED,
        }:
            return PurchaseReconciliationOutcome(
                operation=op,
                verification=None,
                provider_status=None,
                economic_action="none",
            )

        if op.state not in {
            OperationState.UNKNOWN,
            OperationState.RECONCILING,
            OperationState.SUBMITTED,
        }:
            raise ReconciliationError(
                f"Cannot reconcile purchase in state '{op.state.value}'"
            )

        # Intent must match operation binding
        stored_hash = (op.parameters or {}).get("purchase_intent_hash")
        expected_hash = intent.compute_purchase_intent_hash()
        if stored_hash and stored_hash != expected_hash:
            raise ReconciliationError(
                "Operation purchase_intent_hash does not match provided intent"
            )

        if op.state != OperationState.RECONCILING:
            op.transition_to(OperationState.RECONCILING)
            self._save(op)
            self._event(
                "ORBIO_PURCHASE_RECONCILING",
                op.id,
                {"operation_id": op.id, "intent_hash": expected_hash},
            )

        # Independent status query
        try:
            status = self.provider.status(
                op.id,
                op.idempotency_key,
                provider_reference=op.provider_reference,
            )
        except TypeError:
            status = self.provider.status(op.id, op.idempotency_key)

        verification = self.verifier.verify_status(intent, status, operation=op)

        if verification.result == PurchaseVerificationResult.VERIFIED:
            self._settle_success(op, org, status)
            self._event(
                "ORBIO_PURCHASE_RECONCILED_VERIFIED",
                op.id,
                {
                    "operation_id": op.id,
                    "intent_hash": expected_hash,
                    "provider_reference": status.provider_reference,
                    "verification": verification.to_audit_dict(),
                },
            )
            return PurchaseReconciliationOutcome(
                operation=op,
                verification=verification,
                provider_status=status,
                economic_action="settled",
            )

        if verification.result == PurchaseVerificationResult.REJECTED:
            self._settle_failure(op, org, status, verification)
            self._event(
                "ORBIO_PURCHASE_RECONCILED_REJECTED",
                op.id,
                {
                    "operation_id": op.id,
                    "intent_hash": expected_hash,
                    "codes": verification.codes,
                    "reasons": verification.reasons,
                },
            )
            return PurchaseReconciliationOutcome(
                operation=op,
                verification=verification,
                provider_status=status,
                economic_action="refunded",
            )

        # PENDING_EVIDENCE — leave UNKNOWN, escrow locked
        op.transition_to(
            OperationState.UNKNOWN,
            error_message="Purchase evidence pending; escrow preserved",
            provider_reference=status.provider_reference or op.provider_reference,
        )
        self._save(op)
        self._event(
            "ORBIO_PURCHASE_RECONCILIATION_PENDING",
            op.id,
            {
                "operation_id": op.id,
                "intent_hash": expected_hash,
                "provider_outcome": status.outcome,
                "escrow_locked": True,
            },
        )
        return PurchaseReconciliationOutcome(
            operation=op,
            verification=verification,
            provider_status=status,
            economic_action="none",
        )

    def _settle_success(
        self,
        op: ConsequentialOperation,
        org: Organisation,
        status: ProviderStatusResult,
    ) -> None:
        if op.amount > 0:
            tx_id = f"tx-orbio-rec-{hashlib.sha256(op.id.encode()).hexdigest()[:16]}"
            already = any(
                op.id in e.memo and e.to_account == EXTERNAL_SINK
                for e in self.ledger.get_entries()
            )
            if not already and self.ledger.get_balance(ESCROW) >= op.amount:
                try:
                    self.ledger.transfer(
                        from_account=ESCROW,
                        to_account=EXTERNAL_SINK,
                        amount=op.amount,
                        memo=f"Orbio purchase reconciliation settlement for {op.id}",
                        transaction_id=tx_id,
                    )
                    org.treasury_balance = self.ledger.get_balance(TREASURY)
                except (ValueError, Exception) as exc:
                    if "Duplicate transaction ID" in str(exc) or "UNIQUE constraint" in str(exc):
                        pass
                    else:
                        raise

        if op.state != OperationState.RECONCILED:
            op.transition_to(
                OperationState.RECONCILED,
                provider_reference=status.provider_reference,
            )
            self._save(op)

    def _settle_failure(
        self,
        op: ConsequentialOperation,
        org: Organisation,
        status: ProviderStatusResult,
        verification: PurchaseVerificationReport,
    ) -> None:
        if op.amount > 0:
            tx_id = f"rollback-orbio-rec-{hashlib.sha256(op.id.encode()).hexdigest()[:16]}"
            already = any(
                op.id in e.memo and e.to_account == TREASURY and e.from_account == ESCROW
                for e in self.ledger.get_entries()
            )
            if not already and self.ledger.get_balance(ESCROW) >= op.amount:
                try:
                    self.ledger.transfer(
                        from_account=ESCROW,
                        to_account=TREASURY,
                        amount=op.amount,
                        memo=f"Orbio purchase reconciliation refund for {op.id}",
                        transaction_id=tx_id,
                    )
                    org.treasury_balance = self.ledger.get_balance(TREASURY)
                except (ValueError, Exception) as exc:
                    if "Duplicate transaction ID" in str(exc) or "UNIQUE constraint" in str(exc):
                        pass
                    else:
                        raise

        reason = "; ".join(verification.reasons) or status.error_message or "verification rejected"
        if op.state != OperationState.RECONCILED:
            op.transition_to(
                OperationState.RECONCILED,
                error_message=reason,
                provider_reference=status.provider_reference,
            )
            self._save(op)

    def _save(self, op: ConsequentialOperation) -> None:
        if self.repo is not None:
            self.repo.save(op)

    def _event(self, event_type: str, entity_id: str, payload: dict) -> None:
        if self.event_store is not None:
            self.event_store.append_event(
                actor_id="ORBIO_PURCHASE_RECONCILIATION",
                event_type=event_type,
                entity_id=entity_id,
                payload=payload,
            )
