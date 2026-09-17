"""Phase 14B.4 — Simulated Orbio Exchange / USDG provider.

Offline stand-in for Robinhood Chain Exchange.buyAndActivate.
Implements ConsequentialProviderAdapter so Phase 10 can run:

  AUTHORIZE → ESCROW → SUBMIT → SUCCESS/FAILURE/TIMEOUT

without RPC, keys, or real funds.

Rules:
- Deterministic credit_out from usdg_in + fill model
- Idempotent on operation.idempotency_key
- Provider state isolated from Kalyx ledger/reputation
- Never treats simulation as mainnet truth (name + raw.simulated flag)
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Tuple

from src.domain.entities import ConsequentialOperation
from src.domain.enums import ActionType, ProviderOutcome
from src.domain.events import canonical_json
from src.settlement.adapter import (
    ConsequentialProviderAdapter,
    ProviderExecutionResult,
    ProviderStatusResult,
)

# Face conversion for simulation only (1 USDG → 1 CREDIT at full fill).
# Real market pricing is out of scope for the simulator.
DEFAULT_FILL_RATE_BPS = 9800  # 98% of usdg_in as CREDIT out (2% simulated spread)


@dataclass
class SimulatedExchangeRecord:
    operation_id: str
    idempotency_key: str
    tenant_id: str
    organisation_id: str
    usdg_spent: int
    credit_out: int
    beneficiary: str
    exchange_contract: str
    outcome: ProviderOutcome
    provider_reference: str
    evidence_hash: str
    activation_id: str
    submission_count: int = 1
    created_at_ns: int = field(default_factory=time.time_ns)
    raw: Dict[str, Any] = field(default_factory=dict)


class SimulatedOrbioExchangeProvider(ConsequentialProviderAdapter):
    """Deterministic simulated Exchange for ORBIO_CREDIT_PURCHASE operations."""

    name = "orbio-exchange-simulated"

    def __init__(
        self,
        *,
        initial_usdg_balances: Optional[Dict[str, int]] = None,
        fill_rate_bps: int = DEFAULT_FILL_RATE_BPS,
        require_purchase_action_type: bool = True,
    ):
        # org_id -> USDG native units (6 decimals)
        self._usdg: Dict[str, int] = dict(initial_usdg_balances or {})
        # org_id -> CREDIT native units after activation
        self._credit: Dict[str, int] = {}
        self._records: Dict[str, SimulatedExchangeRecord] = {}
        self._timeout_keys: Set[str] = set()
        self._timeout_background_success: Set[str] = set()
        self._timeout_background_failure: Set[str] = set()
        self._failure_rules: Dict[str, str] = {}
        self.fill_rate_bps = fill_rate_bps
        self.require_purchase_action_type = require_purchase_action_type
        self.submission_attempts: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Account helpers (test seeding)
    # ------------------------------------------------------------------

    def set_usdg_balance(self, organisation_id: str, amount: int) -> None:
        self._usdg[organisation_id] = max(0, int(amount))

    def get_usdg_balance(self, organisation_id: str) -> int:
        return self._usdg.get(organisation_id, 0)

    def get_credit_balance(self, organisation_id: str) -> int:
        return self._credit.get(organisation_id, 0)

    def set_timeout_rule(
        self,
        key: str,
        *,
        provider_executes_in_background: bool = False,
        provider_fails_in_background: bool = False,
    ) -> None:
        self._timeout_keys.add(key)
        if provider_executes_in_background:
            self._timeout_background_success.add(key)
        if provider_fails_in_background:
            self._timeout_background_failure.add(key)

    def set_failure_rule(self, key: str, reason: str = "Simulated exchange rejection") -> None:
        self._failure_rules[key] = reason

    # ------------------------------------------------------------------
    # ConsequentialProviderAdapter
    # ------------------------------------------------------------------

    def prepare(self, operation: ConsequentialOperation) -> bool:
        if operation.amount < 0:
            return False
        params = operation.parameters or {}
        usdg_in = int(params.get("usdg_in", 0))
        if usdg_in < 0:
            return False
        return True

    def execute(self, operation: ConsequentialOperation) -> ProviderExecutionResult:
        key = operation.idempotency_key
        op_id = operation.id
        self.submission_attempts[key] = self.submission_attempts.get(key, 0) + 1
        params = operation.parameters if isinstance(operation.parameters, dict) else {}

        # Idempotent replay
        if key in self._records:
            rec = self._records[key]
            rec.submission_count += 1
            return self._result_from_record(rec, op_id, duplicate=True)

        if self.require_purchase_action_type and operation.action_type not in {
            ActionType.ORBIO_CREDIT_PURCHASE,
            ActionType.BLOCKCHAIN_TRANSACTION,
        }:
            return ProviderExecutionResult(
                provider_name=self.name,
                operation_id=op_id,
                outcome=ProviderOutcome.FAILURE.value,
                provider_reference=None,
                raw_response={"error": "unsupported_action_type", "simulated": True},
                error_message=f"Simulated exchange rejects action_type={operation.action_type.value}",
            )

        # Timeout path (ambiguous to caller; may settle in background)
        if key in self._timeout_keys or op_id in self._timeout_keys:
            if key in self._timeout_background_success or op_id in self._timeout_background_success:
                self._settle_success(operation, params)
            elif key in self._timeout_background_failure or op_id in self._timeout_background_failure:
                self._settle_failure(operation, params, "Simulated background failure")
            return ProviderExecutionResult(
                provider_name=self.name,
                operation_id=op_id,
                outcome=ProviderOutcome.TIMEOUT.value,
                provider_reference=None,
                raw_response={"error": "Simulated exchange transport timeout", "simulated": True},
                error_message="Simulated exchange transport timeout",
                evidence_hash=None,
            )

        failure_reason = self._failure_rules.get(key) or self._failure_rules.get(op_id)
        if failure_reason:
            rec = self._settle_failure(operation, params, failure_reason)
            return self._result_from_record(rec, op_id)

        # Economic preflight
        usdg_in = int(params.get("usdg_in", 0))
        min_credit_out = int(params.get("min_credit_out", 0))
        org_id = operation.organisation_id
        available = self.get_usdg_balance(org_id)

        if usdg_in <= 0:
            rec = self._settle_failure(operation, params, "usdg_in must be positive")
            return self._result_from_record(rec, op_id)

        if available < usdg_in:
            rec = self._settle_failure(
                operation,
                params,
                f"Insufficient USDG: available={available}, required={usdg_in}",
            )
            return self._result_from_record(rec, op_id)

        credit_out = self._compute_credit_out(usdg_in)
        if credit_out < min_credit_out:
            rec = self._settle_failure(
                operation,
                params,
                f"Slippage: credit_out={credit_out} < min_credit_out={min_credit_out}",
            )
            return self._result_from_record(rec, op_id)

        rec = self._settle_success(operation, params, credit_out=credit_out, usdg_spent=usdg_in)
        return self._result_from_record(rec, op_id)

    def status(
        self,
        operation_id: str,
        idempotency_key: Optional[str] = None,
        provider_reference: Optional[str] = None,
        **kwargs: Any,
    ) -> ProviderStatusResult:
        record = None
        if idempotency_key and idempotency_key in self._records:
            record = self._records[idempotency_key]
        elif provider_reference:
            for rec in self._records.values():
                if rec.provider_reference == provider_reference:
                    record = rec
                    break
        if record is None:
            for rec in self._records.values():
                if rec.operation_id == operation_id:
                    record = rec
                    break

        if record is None:
            return ProviderStatusResult(
                provider_name=self.name,
                operation_id=operation_id,
                outcome=ProviderOutcome.UNKNOWN.value,
                provider_reference=None,
                raw_status={"error": "unknown_to_exchange", "simulated": True},
                error_message="Operation not found in simulated exchange records",
            )

        return ProviderStatusResult(
            provider_name=self.name,
            operation_id=operation_id,
            outcome=record.outcome.value,
            provider_reference=record.provider_reference,
            raw_status={
                "status": record.outcome.value,
                "reference": record.provider_reference,
                "activation_id": record.activation_id,
                "usdg_spent": record.usdg_spent,
                "credit_out": record.credit_out,
                "submission_count": record.submission_count,
                "simulated": True,
            },
            evidence_hash=record.evidence_hash,
        )

    # ------------------------------------------------------------------
    # Internal settlement
    # ------------------------------------------------------------------

    def _compute_credit_out(self, usdg_in: int) -> int:
        return (usdg_in * self.fill_rate_bps) // 10_000

    def _compute_reference(self, operation: ConsequentialOperation) -> str:
        payload = (
            f"{operation.organisation_id}|{operation.id}|{operation.idempotency_key}|"
            f"{(operation.parameters or {}).get('usdg_in', 0)}"
        )
        return f"sim-xchg-{hashlib.sha256(payload.encode()).hexdigest()[:20]}"

    def _settle_success(
        self,
        operation: ConsequentialOperation,
        params: Dict[str, Any],
        *,
        credit_out: Optional[int] = None,
        usdg_spent: Optional[int] = None,
    ) -> SimulatedExchangeRecord:
        usdg = int(usdg_spent if usdg_spent is not None else params.get("usdg_in", 0))
        credit = int(credit_out if credit_out is not None else self._compute_credit_out(usdg))
        org_id = operation.organisation_id

        # Debit USDG / credit CREDIT (provider-side only)
        self._usdg[org_id] = self.get_usdg_balance(org_id) - usdg
        self._credit[org_id] = self.get_credit_balance(org_id) + credit

        ref = self._compute_reference(operation)
        activation_id = f"act-{hashlib.sha256(ref.encode()).hexdigest()[:16]}"
        beneficiary = str(params.get("beneficiary_bytes32") or params.get("beneficiary") or "")
        exchange = str(params.get("exchange_contract") or params.get("recipient") or "")

        raw = {
            "simulated": True,
            "status": "confirmed",
            "function": "buyAndActivate",
            "usdg_spent": usdg,
            "credit_out": credit,
            "min_credit_out": int(params.get("min_credit_out", 0)),
            "beneficiary": beneficiary,
            "exchange_contract": exchange,
            "activation_id": activation_id,
            "events": [
                {
                    "name": "Activated",
                    "activation_id": activation_id,
                    "beneficiary": beneficiary,
                    "credit_amount": credit,
                }
            ],
            "balances": {
                "usdg_after": self.get_usdg_balance(org_id),
                "credit_after": self.get_credit_balance(org_id),
            },
            "purchase_intent_hash": params.get("purchase_intent_hash"),
            "data_payload": params.get("data_payload"),
        }
        evidence = hashlib.sha256(canonical_json({
            "ref": ref,
            "activation_id": activation_id,
            "usdg": usdg,
            "credit": credit,
            "beneficiary": beneficiary,
            "op_id": operation.id,
            "idem": operation.idempotency_key,
        }).encode()).hexdigest()

        rec = SimulatedExchangeRecord(
            operation_id=operation.id,
            idempotency_key=operation.idempotency_key,
            tenant_id=operation.tenant_id,
            organisation_id=org_id,
            usdg_spent=usdg,
            credit_out=credit,
            beneficiary=beneficiary,
            exchange_contract=exchange,
            outcome=ProviderOutcome.SUCCESS,
            provider_reference=ref,
            evidence_hash=evidence,
            activation_id=activation_id,
            raw=raw,
        )
        self._records[operation.idempotency_key] = rec
        return rec

    def _settle_failure(
        self,
        operation: ConsequentialOperation,
        params: Dict[str, Any],
        reason: str,
    ) -> SimulatedExchangeRecord:
        ref = self._compute_reference(operation)
        evidence = hashlib.sha256(f"fail|{ref}|{reason}".encode()).hexdigest()
        rec = SimulatedExchangeRecord(
            operation_id=operation.id,
            idempotency_key=operation.idempotency_key,
            tenant_id=operation.tenant_id,
            organisation_id=operation.organisation_id,
            usdg_spent=0,
            credit_out=0,
            beneficiary=str(params.get("beneficiary", "")),
            exchange_contract=str(params.get("exchange_contract", "")),
            outcome=ProviderOutcome.FAILURE,
            provider_reference=ref,
            evidence_hash=evidence,
            activation_id="",
            raw={"simulated": True, "error": reason, "status": "reverted"},
        )
        self._records[operation.idempotency_key] = rec
        return rec

    def _result_from_record(
        self,
        rec: SimulatedExchangeRecord,
        operation_id: str,
        *,
        duplicate: bool = False,
    ) -> ProviderExecutionResult:
        raw = dict(rec.raw)
        raw["duplicate_submission"] = duplicate
        raw["submission_count"] = rec.submission_count
        return ProviderExecutionResult(
            provider_name=self.name,
            operation_id=operation_id,
            outcome=rec.outcome.value,
            provider_reference=rec.provider_reference,
            raw_response=raw,
            error_message=None if rec.outcome == ProviderOutcome.SUCCESS else str(raw.get("error")),
            evidence_hash=rec.evidence_hash,
        )
