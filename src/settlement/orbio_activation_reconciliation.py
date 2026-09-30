"""Phase 21 — Reconciliation and ledger settlement for Orbio CREDIT activations.

Reconciles on-chain CREDIT activation evidence with off-chain Orbio API entitlement:

  On-chain Receipt + Activated Event (Phase 20B)
                    ↓
        OrbioCreditActivationVerifier
                    ↓
       OrbioApiBalanceVerifier (Phase 21)
                    ↓
  VERIFIED → Settle Escrow (ESCROW → EXTERNAL_SINK) → OperationState.SUCCEEDED / RECONCILED
  REJECTED → Refund Escrow (ESCROW → TREASURY) → OperationState.FAILED
  PENDING  → Escrow locked, state UNKNOWN / PENDING (no economic mutation)
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional

from src.domain.entities import ConsequentialOperation, Organisation
from src.domain.enums import ActionType, OperationState
from src.domain.exceptions import ReconciliationError, UnauthorizedActionError
from src.domain.orbio_activation import (
    ORBIO_ACTIVATION_CHAIN_ID,
    OrbioCreditActivationIntent,
)
from src.economy.ledger import DoubleEntryLedger, ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.consequential import ConsequentialOperationRepository
from src.settlement.orbio_activation_verifier import (
    ActivationVerificationReport,
    ActivationVerificationResult,
    OrbioCreditActivationVerifier,
)
from src.settlement.orbio_api_verifier import (
    OrbioApiBalanceVerifier,
    OrbioApiStatus,
    OrbioApiVerificationReport,
)


@dataclass(frozen=True)
class ActivationReconciliationOutcome:
    operation: ConsequentialOperation
    on_chain_verification: Optional[ActivationVerificationReport]
    api_verification: Optional[OrbioApiVerificationReport]
    economic_action: str  # settled | refunded | none
    status: str           # VERIFIED | REJECTED | PENDING


class OrbioActivationReconciliation:
    """Reconciles Orbio CREDIT activations across on-chain receipt and off-chain API."""

    def __init__(
        self,
        *,
        ledger: DoubleEntryLedger,
        repo: Optional[ConsequentialOperationRepository] = None,
        on_chain_verifier: Optional[OrbioCreditActivationVerifier] = None,
        api_verifier: Optional[OrbioApiBalanceVerifier] = None,
        event_store: Any = None,
    ) -> None:
        self.ledger = ledger
        self.repo = repo
        self.on_chain_verifier = on_chain_verifier or OrbioCreditActivationVerifier()
        self.api_verifier = api_verifier or OrbioApiBalanceVerifier()
        self.event_store = event_store
        self._lock = threading.RLock()

    def reconcile(
        self,
        operation: ConsequentialOperation,
        intent: OrbioCreditActivationIntent,
        org: Organisation,
        *,
        receipt: Optional[Dict[str, Any]] = None,
        chain_id: int = ORBIO_ACTIVATION_CHAIN_ID,
        expected_sender: Optional[str] = None,
    ) -> ActivationReconciliationOutcome:
        with self._lock:
            return self._reconcile_locked(
                operation=operation,
                intent=intent,
                org=org,
                receipt=receipt,
                chain_id=chain_id,
                expected_sender=expected_sender,
            )

    def _reconcile_locked(
        self,
        operation: ConsequentialOperation,
        intent: OrbioCreditActivationIntent,
        org: Organisation,
        *,
        receipt: Optional[Dict[str, Any]] = None,
        chain_id: int = ORBIO_ACTIVATION_CHAIN_ID,
        expected_sender: Optional[str] = None,
    ) -> ActivationReconciliationOutcome:
        # 1. Enforce tenant & org boundary
        tenant_id = getattr(org, "tenant_id", intent.tenant_id)
        if operation.tenant_id != tenant_id or operation.organisation_id != org.id:
            raise UnauthorizedActionError(
                "Isolation violation: Cannot reconcile operation belonging to a different tenant or organisation"
            )

        if operation.action_type != ActionType.ORBIO_CREDIT_ACTIVATION:
            raise ReconciliationError(
                f"Invalid action_type: expected ORBIO_CREDIT_ACTIVATION, got {operation.action_type.value}"
            )

        # 2. Idempotency: if already terminal, return outcome
        if operation.state in (OperationState.SUCCEEDED, OperationState.RECONCILED):
            return ActivationReconciliationOutcome(
                operation=operation,
                on_chain_verification=None,
                api_verification=None,
                economic_action="none",
                status="VERIFIED",
            )

        # 3. Verify on-chain receipt if provided
        on_chain_report: Optional[ActivationVerificationReport] = None
        if receipt is not None:
            on_chain_report = self.on_chain_verifier.verify(
                intent,
                chain_id=chain_id,
                receipt=receipt,
                expected_sender=expected_sender,
            )
            if on_chain_report.result == ActivationVerificationResult.REJECTED:
                # Definitive failure -> refund escrow
                return self._finalize_rejection(operation, on_chain_report, org)

        # 4. Verify off-chain API balance
        api_report: OrbioApiVerificationReport = self.api_verifier.verify(
            expected_min_credits=0.95,
            expected_account_id=expected_sender,
        )

        # 5. Evaluate combined outcome
        is_on_chain_ok = on_chain_report is not None and on_chain_report.result == ActivationVerificationResult.VERIFIED
        is_api_ok = api_report.is_confirmed

        if is_on_chain_ok:
            # On-chain activation is confirmed!
            # Settle escrow if funds were locked (ESCROW -> EXTERNAL_SINK)
            economic_action = "none"
            if operation.amount > 0:
                settle_amount = int(operation.amount)
                tx_id = f"tx-act-rec-{hashlib.sha256(operation.id.encode()).hexdigest()[:16]}"
                already_settled = any(
                    getattr(e, "transaction_id", None) == tx_id
                    for e in self.ledger.get_entries()
                )
                if not already_settled and self.ledger.get_balance(ESCROW) >= settle_amount:
                    self.ledger.transfer(
                        from_account=ESCROW,
                        to_account=EXTERNAL_SINK,
                        amount=settle_amount,
                        memo=f"Orbio activation settlement for {operation.id}",
                        transaction_id=tx_id,
                    )
                    org.treasury_balance = Decimal(str(self.ledger.get_balance(TREASURY)))
                    economic_action = "settled"

            if operation.state == OperationState.ESCROWED:
                operation.transition_to(OperationState.SUBMITTED)

            tx_ref = on_chain_report.tx_hash if on_chain_report else None
            if is_api_ok:
                operation.transition_to(OperationState.SUCCEEDED, provider_reference=tx_ref)
            else:
                if operation.state != OperationState.RECONCILING:
                    operation.transition_to(OperationState.RECONCILING, provider_reference=tx_ref)
                operation.transition_to(OperationState.RECONCILED, provider_reference=tx_ref)

            if self.repo is not None:
                self.repo.save(operation)

            if self.event_store is not None:
                self.event_store.append_event(
                    actor_id="ORBIO_ACTIVATION_RECONCILIATION",
                    event_type="ORBIO_ACTIVATION_RECONCILED_VERIFIED",
                    entity_id=operation.id,
                    payload={
                        "operation_id": operation.id,
                        "on_chain_verified": True,
                        "api_balance_confirmed": is_api_ok,
                        "tx_hash": on_chain_report.tx_hash if on_chain_report else None,
                        "activation_id": on_chain_report.activation_id if on_chain_report else None,
                        "economic_action": economic_action,
                    },
                )

            return ActivationReconciliationOutcome(
                operation=operation,
                on_chain_verification=on_chain_report,
                api_verification=api_report,
                economic_action=economic_action,
                status="VERIFIED",
            )

        # Receipt pending or missing
        return ActivationReconciliationOutcome(
            operation=operation,
            on_chain_verification=on_chain_report,
            api_verification=api_report,
            economic_action="none",
            status="PENDING",
        )

    def _finalize_rejection(
        self,
        operation: ConsequentialOperation,
        report: ActivationVerificationReport,
        org: Organisation,
    ) -> ActivationReconciliationOutcome:
        economic_action = "none"
        if operation.amount > 0:
            refund_amount = int(operation.amount)
            tx_id = f"rollback-act-rec-{hashlib.sha256(operation.id.encode()).hexdigest()[:16]}"
            already_refunded = any(
                getattr(e, "transaction_id", None) == tx_id
                for e in self.ledger.get_entries()
            )
            if not already_refunded and self.ledger.get_balance(ESCROW) >= refund_amount:
                self.ledger.transfer(
                    from_account=ESCROW,
                    to_account=TREASURY,
                    amount=refund_amount,
                    memo=f"Orbio activation refund for {operation.id}",
                    transaction_id=tx_id,
                )
                org.treasury_balance = Decimal(str(self.ledger.get_balance(TREASURY)))
                economic_action = "refunded"

        if operation.state == OperationState.ESCROWED:
            operation.transition_to(OperationState.SUBMITTED)
        operation.transition_to(
            OperationState.FAILED,
            error_message="; ".join(report.reasons) if report.reasons else "On-chain activation rejected",
        )
        if self.repo is not None:
            self.repo.save(operation)

        if self.event_store is not None:
            self.event_store.append_event(
                actor_id="ORBIO_ACTIVATION_RECONCILIATION",
                event_type="ORBIO_ACTIVATION_RECONCILED_REJECTED",
                entity_id=operation.id,
                payload={
                    "operation_id": operation.id,
                    "reasons": report.reasons,
                    "economic_action": economic_action,
                },
            )

        return ActivationReconciliationOutcome(
            operation=operation,
            on_chain_verification=report,
            api_verification=None,
            economic_action=economic_action,
            status="REJECTED",
        )
