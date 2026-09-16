"""Deterministic simulated Orbio provider for tests.

Provider state is isolated from Kalyx performance/reputation/treasury.
Never mutates Kalyx domain state.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from src.external.models import (
    ExternalBalance,
    ExternalKeyStatus,
    ExternalProviderOutcome,
    InferenceReceipt,
    InferenceRequest,
    KeyLifecycleIntent,
    KeyLifecycleOperation,
    KeyLifecycleReceipt,
)


class SimulatedOrbioProvider:
    name = "orbio-simulated"

    def __init__(
        self,
        *,
        initial_available: str = "100.00",
        initial_used: str = "0.00",
        force_outcome: Optional[ExternalProviderOutcome] = None,
    ):
        self._available = initial_available
        self._used = initial_used
        self._keys: Dict[str, Dict] = {}
        self._force_outcome = force_outcome
        self._call_log: list = []

    def get_balance(self, *, tenant_id: str, organisation_id: str) -> ExternalBalance:
        self._call_log.append(("get_balance", tenant_id, organisation_id))
        return ExternalBalance(
            available=self._available,
            used=self._used,
            currency="USD",
            timestamp=datetime.now(timezone.utc),
            provider_reference=f"sim-bal-{tenant_id}-{organisation_id}",
            rate_limit={"requests_per_minute": 120, "concurrent": 32},
            raw={"object": "key", "simulated": True},
        )

    def get_key_status(
        self, *, tenant_id: str, organisation_id: str, key_id: Optional[str] = None
    ) -> ExternalKeyStatus:
        self._call_log.append(("get_key_status", tenant_id, organisation_id, key_id))
        if key_id and key_id in self._keys:
            meta = self._keys[key_id]
            return ExternalKeyStatus(
                key_id=key_id,
                status=meta["status"],
                account_id=meta.get("account_id"),
                balance=self.get_balance(tenant_id=tenant_id, organisation_id=organisation_id),
                rate_limit={"requests_per_minute": 120, "concurrent": 32},
                created_at=meta.get("created_at"),
                revoked_at=meta.get("revoked_at"),
                provider_reference=key_id,
                raw={"simulated": True},
            )
        # Default synthetic active key observation when none specified
        kid = key_id or f"sim-key-{organisation_id}"
        return ExternalKeyStatus(
            key_id=kid,
            status="active",
            account_id=f"acct-{organisation_id}",
            balance=self.get_balance(tenant_id=tenant_id, organisation_id=organisation_id),
            rate_limit={"requests_per_minute": 120, "concurrent": 32},
            provider_reference=kid,
            raw={"simulated": True},
        )

    def execute_key_lifecycle(self, intent: KeyLifecycleIntent) -> KeyLifecycleReceipt:
        self._call_log.append(("execute_key_lifecycle", intent.operation.value, intent.tenant_id, intent.organisation_id))
        if self._force_outcome in {
            ExternalProviderOutcome.FAILURE,
            ExternalProviderOutcome.TIMEOUT,
            ExternalProviderOutcome.UNKNOWN,
        }:
            return KeyLifecycleReceipt(
                operation=intent.operation,
                outcome=self._force_outcome,
                key_id=intent.key_id,
                error_message=f"Simulated {self._force_outcome.value}",
                evidence_hash=hashlib.sha256(f"sim-fail|{intent.operation.value}".encode()).hexdigest(),
            )

        if intent.operation == KeyLifecycleOperation.CREATE:
            key_id = f"sim-key-{uuid.uuid4().hex[:12]}"
            secret = f"sk-orbio-sim-{uuid.uuid4().hex}"
            self._keys[key_id] = {
                "status": "active",
                "account_id": f"acct-{intent.organisation_id}",
                "created_at": datetime.now(timezone.utc),
                "revoked_at": None,
                # Secret is held only transiently in receipt; not stored on provider after return.
            }
            return KeyLifecycleReceipt(
                operation=KeyLifecycleOperation.CREATE,
                outcome=ExternalProviderOutcome.SUCCESS,
                key_id=key_id,
                one_time_secret=secret,
                provider_reference=key_id,
                evidence_hash=hashlib.sha256(f"create|{key_id}|{intent.organisation_id}".encode()).hexdigest(),
                raw={"simulated": True},
            )

        if intent.operation == KeyLifecycleOperation.REVOKE:
            if not intent.key_id:
                return KeyLifecycleReceipt(
                    operation=KeyLifecycleOperation.REVOKE,
                    outcome=ExternalProviderOutcome.FAILURE,
                    key_id=None,
                    error_message="key_id required for revoke",
                )
            meta = self._keys.get(intent.key_id)
            if meta is None:
                # Treat unknown key as already-revoked for deterministic simulation
                return KeyLifecycleReceipt(
                    operation=KeyLifecycleOperation.REVOKE,
                    outcome=ExternalProviderOutcome.KEY_REVOKED,
                    key_id=intent.key_id,
                    provider_reference=intent.key_id,
                    evidence_hash=hashlib.sha256(f"revoke-missing|{intent.key_id}".encode()).hexdigest(),
                )
            meta["status"] = "revoked"
            meta["revoked_at"] = datetime.now(timezone.utc)
            return KeyLifecycleReceipt(
                operation=KeyLifecycleOperation.REVOKE,
                outcome=ExternalProviderOutcome.SUCCESS,
                key_id=intent.key_id,
                provider_reference=intent.key_id,
                evidence_hash=hashlib.sha256(f"revoke|{intent.key_id}".encode()).hexdigest(),
                raw={"simulated": True},
            )

        return KeyLifecycleReceipt(
            operation=intent.operation,
            outcome=ExternalProviderOutcome.FAILURE,
            key_id=intent.key_id,
            error_message=f"Unsupported operation {intent.operation}",
        )

    def run_inference(self, request: InferenceRequest) -> InferenceReceipt:
        self._call_log.append(("run_inference", request.tenant_id, request.organisation_id, request.model))
        if self._force_outcome is not None:
            outcome = self._force_outcome
            if outcome != ExternalProviderOutcome.SUCCESS:
                return InferenceReceipt(
                    outcome=outcome,
                    provider_reference=None,
                    model=request.model,
                    usage={},
                    error_message=f"Simulated {outcome.value}",
                    evidence_hash=hashlib.sha256(f"inf-fail|{request.idempotency_key}".encode()).hexdigest(),
                )

        # Deterministic usage from idempotency key
        digest = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        prompt_tokens = 10 + (int(digest[:4], 16) % 40)
        completion_tokens = 5 + (int(digest[4:8], 16) % 20)
        # Simulated cost in USD decimal string (not Kalyx internal CR)
        cost = f"{(prompt_tokens + completion_tokens) * 0.0001:.4f}"

        try:
            avail = float(self._available)
            used = float(self._used)
            cost_f = float(cost)
            if cost_f > avail:
                return InferenceReceipt(
                    outcome=ExternalProviderOutcome.INSUFFICIENT_BALANCE,
                    provider_reference=None,
                    model=request.model,
                    usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
                    balance_before=self._available,
                    error_message="Insufficient Orbio inference balance",
                    evidence_hash=hashlib.sha256(f"inf-nsf|{request.idempotency_key}".encode()).hexdigest(),
                )
            balance_before = self._available
            self._available = f"{avail - cost_f:.4f}"
            self._used = f"{used + cost_f:.4f}"
        except ValueError:
            balance_before = self._available

        ref = f"sim-inf-{digest[:16]}"
        return InferenceReceipt(
            outcome=ExternalProviderOutcome.SUCCESS,
            provider_reference=ref,
            model=request.model,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "cost_usd": cost,
            },
            balance_before=balance_before,
            balance_after=self._available,
            evidence_hash=hashlib.sha256(f"inf-ok|{ref}|{request.idempotency_key}".encode()).hexdigest(),
            raw={
                "simulated": True,
                "choices": [{"message": {"role": "assistant", "content": f"[sim] response for {request.model}"}}],
            },
        )
