"""Deterministic simulated external provider for consequential execution and reconciliation.

Maintains independent provider-side state completely isolated from Kalyx's database
and application memory to model a realistic external boundary.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

from src.domain.entities import ConsequentialOperation
from src.domain.enums import ProviderOutcome
from src.domain.events import canonical_json
from src.settlement.adapter import (
    ConsequentialProviderAdapter,
    ProviderExecutionResult,
    ProviderStatusResult,
)


@dataclass
class ExternalProviderRecord:
    operation_id: str
    idempotency_key: str
    target: str
    amount: int
    outcome: ProviderOutcome
    provider_reference: str
    evidence_hash: str
    submission_count: int
    created_at_ns: int = field(default_factory=time.time_ns)


class SimulatedConsequentialProvider(ConsequentialProviderAdapter):
    """Deterministic simulated provider with independent provider-side state."""

    name = "simulated"

    def __init__(self, external_state: Optional[Dict[str, ExternalProviderRecord]] = None):
        # Isolated provider-side ledger of executed operations
        self._provider_records: Dict[str, ExternalProviderRecord] = (
            external_state if external_state is not None else {}
        )
        self._timeout_keys: Set[str] = set()
        self._timeout_with_provider_success: Set[str] = set()
        self._timeout_with_provider_failure: Set[str] = set()
        self._failure_rules: Dict[str, str] = {}
        self.submission_attempts: Dict[str, int] = {}

    def set_timeout_rule(
        self,
        key: str,
        provider_executes_in_background: bool = False,
        provider_fails_in_background: bool = False,
        failure_reason: Optional[str] = None,
    ) -> None:
        """Configure an operation_id or idempotency_key to time out during dispatch."""
        self._timeout_keys.add(key)
        if provider_executes_in_background:
            self._timeout_with_provider_success.add(key)
        if provider_fails_in_background:
            self._timeout_with_provider_failure.add(key)
            if failure_reason:
                self._failure_rules[key] = failure_reason

    def set_failure_rule(self, key: str, reason: str = "Simulated provider rejection") -> None:
        """Configure an operation_id or idempotency_key to fail explicitly."""
        self._failure_rules[key] = reason

    def prepare(self, operation: ConsequentialOperation) -> bool:
        if operation.amount < 0:
            return False
        return True

    def execute(self, operation: ConsequentialOperation) -> ProviderExecutionResult:
        key = operation.idempotency_key
        op_id = operation.id
        self.submission_attempts[key] = self.submission_attempts.get(key, 0) + 1

        # 1. Idempotency check: provider already processed this key
        if key in self._provider_records:
            existing = self._provider_records[key]
            existing.submission_count += 1
            return ProviderExecutionResult(
                provider_name=self.name,
                operation_id=op_id,
                outcome=existing.outcome.value,
                provider_reference=existing.provider_reference,
                raw_response={
                    "status": existing.outcome.value,
                    "reference": existing.provider_reference,
                    "duplicate_submission": True,
                    "submission_count": existing.submission_count,
                },
                evidence_hash=existing.evidence_hash,
            )

        # 2. Check for configured timeout
        if key in self._timeout_keys or op_id in self._timeout_keys:
            # If simulated provider received and succeeded in background:
            if key in self._timeout_with_provider_success or op_id in self._timeout_with_provider_success:
                ref = self._compute_reference(operation)
                ev = self._compute_evidence(ref, operation)
                self._provider_records[key] = ExternalProviderRecord(
                    operation_id=op_id,
                    idempotency_key=key,
                    target=operation.target,
                    amount=operation.amount,
                    outcome=ProviderOutcome.SUCCESS,
                    provider_reference=ref,
                    evidence_hash=ev,
                    submission_count=1,
                )
            elif key in self._timeout_with_provider_failure or op_id in self._timeout_with_provider_failure:
                ref = self._compute_reference(operation)
                ev = self._compute_evidence(ref, operation)
                self._provider_records[key] = ExternalProviderRecord(
                    operation_id=op_id,
                    idempotency_key=key,
                    target=operation.target,
                    amount=operation.amount,
                    outcome=ProviderOutcome.FAILURE,
                    provider_reference=ref,
                    evidence_hash=ev,
                    submission_count=1,
                )
            return ProviderExecutionResult(
                provider_name=self.name,
                operation_id=op_id,
                outcome=ProviderOutcome.TIMEOUT.value,
                provider_reference=None,
                raw_response={"error": "Simulated upstream transport timeout"},
                error_message="Simulated upstream transport timeout",
                evidence_hash=None,
            )

        # 3. Check for configured explicit failure
        failure_reason = self._failure_rules.get(key) or self._failure_rules.get(op_id)
        if failure_reason:
            ref = self._compute_reference(operation)
            ev = self._compute_evidence(ref, operation)
            rec = ExternalProviderRecord(
                operation_id=op_id,
                idempotency_key=key,
                target=operation.target,
                amount=operation.amount,
                outcome=ProviderOutcome.FAILURE,
                provider_reference=ref,
                evidence_hash=ev,
                submission_count=1,
            )
            self._provider_records[key] = rec
            return ProviderExecutionResult(
                provider_name=self.name,
                operation_id=op_id,
                outcome=ProviderOutcome.FAILURE.value,
                provider_reference=ref,
                raw_response={"error": failure_reason, "status": "REJECTED"},
                error_message=failure_reason,
                evidence_hash=ev,
            )

        # 4. Standard happy path
        ref = self._compute_reference(operation)
        ev = self._compute_evidence(ref, operation)
        rec = ExternalProviderRecord(
            operation_id=op_id,
            idempotency_key=key,
            target=operation.target,
            amount=operation.amount,
            outcome=ProviderOutcome.SUCCESS,
            provider_reference=ref,
            evidence_hash=ev,
            submission_count=1,
        )
        self._provider_records[key] = rec

        return ProviderExecutionResult(
            provider_name=self.name,
            operation_id=op_id,
            outcome=ProviderOutcome.SUCCESS.value,
            provider_reference=ref,
            raw_response={
                "status": "ACCEPTED",
                "reference": ref,
                "target": operation.target,
                "amount": operation.amount,
            },
            evidence_hash=ev,
        )

    def status(self, operation_id: str, idempotency_key: Optional[str] = None) -> ProviderStatusResult:
        # Lookup by idempotency_key first, then by operation_id
        record = None
        if idempotency_key and idempotency_key in self._provider_records:
            record = self._provider_records[idempotency_key]
        else:
            for rec in self._provider_records.values():
                if rec.operation_id == operation_id:
                    record = rec
                    break

        if not record:
            return ProviderStatusResult(
                provider_name=self.name,
                operation_id=operation_id,
                outcome=ProviderOutcome.UNKNOWN.value,
                provider_reference=None,
                raw_status={"error": "Operation unknown to external provider"},
                error_message="Operation not found in external provider records",
                evidence_hash=None,
            )

        return ProviderStatusResult(
            provider_name=self.name,
            operation_id=operation_id,
            outcome=record.outcome.value,
            provider_reference=record.provider_reference,
            raw_status={
                "status": record.outcome.value,
                "reference": record.provider_reference,
                "submission_count": record.submission_count,
            },
            evidence_hash=record.evidence_hash,
        )

    def _compute_reference(self, op: ConsequentialOperation) -> str:
        payload = f"{op.organisation_id}|{op.id}|{op.idempotency_key}|{op.amount}"
        return f"ext-ref-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"

    def _compute_evidence(self, ref: str, op: ConsequentialOperation) -> str:
        data = {
            "ref": ref,
            "op_id": op.id,
            "idem_key": op.idempotency_key,
            "amount": op.amount,
            "target": op.target,
        }
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()

    def record_external_execution(
        self,
        idempotency_key: str,
        operation_id: str,
        outcome: ProviderOutcome = ProviderOutcome.SUCCESS,
        target: str = "sandbox://default",
        amount: int = 0,
        provider_reference: Optional[str] = None,
        evidence_hash: Optional[str] = None,
    ) -> ExternalProviderRecord:
        """Helper to seed isolated external provider records for tests."""
        ref = provider_reference or f"ext-ref-{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()[:16]}"
        ev = evidence_hash or hashlib.sha256(f"{ref}|{amount}".encode("utf-8")).hexdigest()
        rec = ExternalProviderRecord(
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            target=target,
            amount=amount,
            outcome=outcome,
            provider_reference=ref,
            evidence_hash=ev,
            submission_count=1,
        )
        self._provider_records[idempotency_key] = rec
        return rec
