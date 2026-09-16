"""Governed Orbio adapter: MCP + gateway behind ExternalEconomicProvider.

Agents never receive this client. All calls require Kalyx authorization upstream.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.external.models import (
    ExternalBalance,
    ExternalKeyStatus,
    ExternalProviderMode,
    ExternalProviderOutcome,
    InferenceReceipt,
    InferenceRequest,
    KeyLifecycleIntent,
    KeyLifecycleOperation,
    KeyLifecycleReceipt,
)
from src.external.orbio.config import OrbioConfig, validate_orbio_config
from src.external.orbio.gateway_client import OrbioGatewayClient, OrbioGatewayError
from src.external.orbio.mcp_client import OrbioMCPClient, OrbioMCPError
from src.external.orbio.simulated_provider import SimulatedOrbioProvider


def _parse_balance_payload(payload: Any) -> ExternalBalance:
    if not isinstance(payload, dict):
        payload = {}
    # tools/call may wrap content
    if "content" in payload and isinstance(payload["content"], list):
        for item in payload["content"]:
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    payload = json.loads(item.get("text") or "{}")
                except json.JSONDecodeError:
                    pass
    bal = payload.get("balance") if isinstance(payload.get("balance"), dict) else payload
    available = str(bal.get("available", bal.get("limit_remaining", "0")))
    used = str(bal.get("used", bal.get("usage", "0")))
    currency = str(bal.get("currency", "USD"))
    return ExternalBalance(
        available=available,
        used=used,
        currency=currency,
        timestamp=datetime.now(timezone.utc),
        provider_reference=payload.get("id") or payload.get("key_id"),
        rate_limit=payload.get("rate_limit") if isinstance(payload.get("rate_limit"), dict) else None,
        raw=payload if isinstance(payload, dict) else {},
    )


class OrbioAdapter:
    name = "orbio"

    def __init__(self, config: Optional[OrbioConfig] = None, simulated: Optional[SimulatedOrbioProvider] = None):
        self.config = config or OrbioConfig.from_env()
        self._simulated = simulated
        if self.config.mode == ExternalProviderMode.LIVE:
            validate_orbio_config(self.config)
            self._mcp = OrbioMCPClient(self.config.mcp_url, api_key=self.config.api_key)
            self._gateway = OrbioGatewayClient(self.config.gateway_base, api_key=self.config.api_key or "")
        elif self.config.mode == ExternalProviderMode.SIMULATED:
            self._mcp = None
            self._gateway = None
            self._simulated = simulated or SimulatedOrbioProvider()
        else:
            self._mcp = None
            self._gateway = None
            self._simulated = None

    def _require_enabled(self) -> None:
        if self.config.mode == ExternalProviderMode.DISABLED:
            raise RuntimeError("Orbio integration is disabled (KALYX_ORBIO_MODE=disabled)")

    def get_balance(self, *, tenant_id: str, organisation_id: str) -> ExternalBalance:
        self._require_enabled()
        if self.config.mode == ExternalProviderMode.SIMULATED:
            assert self._simulated is not None
            return self._simulated.get_balance(tenant_id=tenant_id, organisation_id=organisation_id)
        try:
            # Prefer gateway /key (documented response shape) when live
            assert self._gateway is not None
            payload = self._gateway.get_key()
            return _parse_balance_payload(payload)
        except OrbioGatewayError:
            # Fallback to MCP tool if gateway fails
            assert self._mcp is not None
            try:
                result = self._mcp.get_balance()
                return _parse_balance_payload(result)
            except OrbioMCPError as e:
                raise RuntimeError(f"Orbio balance read failed: {e}") from e

    def get_key_status(
        self, *, tenant_id: str, organisation_id: str, key_id: Optional[str] = None
    ) -> ExternalKeyStatus:
        self._require_enabled()
        if self.config.mode == ExternalProviderMode.SIMULATED:
            assert self._simulated is not None
            return self._simulated.get_key_status(
                tenant_id=tenant_id, organisation_id=organisation_id, key_id=key_id
            )
        assert self._mcp is not None
        try:
            result = self._mcp.get_key_status(key_id=key_id)
            payload = result if isinstance(result, dict) else {}
            return ExternalKeyStatus(
                key_id=str(payload.get("key_id") or key_id or "unknown"),
                status=str(payload.get("status") or "unknown"),
                account_id=payload.get("account_id"),
                balance=_parse_balance_payload(payload),
                rate_limit=payload.get("rate_limit") if isinstance(payload.get("rate_limit"), dict) else None,
                provider_reference=payload.get("id"),
                raw=payload,
            )
        except OrbioMCPError as e:
            raise RuntimeError(f"Orbio key status failed: {e}") from e

    def execute_key_lifecycle(self, intent: KeyLifecycleIntent) -> KeyLifecycleReceipt:
        self._require_enabled()
        if self.config.mode == ExternalProviderMode.SIMULATED:
            assert self._simulated is not None
            return self._simulated.execute_key_lifecycle(intent)
        assert self._mcp is not None
        try:
            if intent.operation == KeyLifecycleOperation.CREATE:
                result = self._mcp.create_key()
                payload = result if isinstance(result, dict) else {}
                # Secret may appear once under various documented shapes; extract carefully
                secret = payload.get("api_key") or payload.get("secret") or payload.get("key")
                key_id = payload.get("key_id") or payload.get("id")
                return KeyLifecycleReceipt(
                    operation=KeyLifecycleOperation.CREATE,
                    outcome=ExternalProviderOutcome.SUCCESS,
                    key_id=str(key_id) if key_id else None,
                    one_time_secret=str(secret) if secret else None,
                    provider_reference=str(key_id) if key_id else None,
                    evidence_hash=hashlib.sha256(
                        json.dumps({"op": "create", "key_id": key_id}, sort_keys=True).encode()
                    ).hexdigest(),
                    raw={k: v for k, v in payload.items() if k not in {"api_key", "secret", "key"}},
                )
            if intent.operation == KeyLifecycleOperation.REVOKE:
                if not intent.key_id:
                    return KeyLifecycleReceipt(
                        operation=KeyLifecycleOperation.REVOKE,
                        outcome=ExternalProviderOutcome.FAILURE,
                        key_id=None,
                        error_message="key_id required",
                    )
                result = self._mcp.revoke_key(intent.key_id)
                payload = result if isinstance(result, dict) else {}
                return KeyLifecycleReceipt(
                    operation=KeyLifecycleOperation.REVOKE,
                    outcome=ExternalProviderOutcome.SUCCESS,
                    key_id=intent.key_id,
                    provider_reference=intent.key_id,
                    evidence_hash=hashlib.sha256(f"revoke|{intent.key_id}".encode()).hexdigest(),
                    raw=payload,
                )
            return KeyLifecycleReceipt(
                operation=intent.operation,
                outcome=ExternalProviderOutcome.FAILURE,
                key_id=intent.key_id,
                error_message=f"Unsupported operation {intent.operation}",
            )
        except OrbioMCPError as e:
            outcome = ExternalProviderOutcome.UNKNOWN if e.status_code is None else ExternalProviderOutcome.FAILURE
            if e.status_code and e.status_code >= 500:
                outcome = ExternalProviderOutcome.UNKNOWN
            return KeyLifecycleReceipt(
                operation=intent.operation,
                outcome=outcome,
                key_id=intent.key_id,
                error_message=str(e),
                evidence_hash=hashlib.sha256(f"mcp-err|{intent.operation.value}".encode()).hexdigest(),
            )

    def run_inference(self, request: InferenceRequest) -> InferenceReceipt:
        self._require_enabled()
        if self.config.mode == ExternalProviderMode.SIMULATED:
            assert self._simulated is not None
            return self._simulated.run_inference(request)
        assert self._gateway is not None
        try:
            body, headers = self._gateway.chat_completions(
                model=request.model,
                messages=request.messages,
                max_tokens=request.max_tokens,
                extra=request.parameters or None,
            )
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            bal_header = headers.get("x-orbio-balance")
            ref = body.get("id")
            return InferenceReceipt(
                outcome=ExternalProviderOutcome.SUCCESS,
                provider_reference=str(ref) if ref else None,
                model=body.get("model") or request.model,
                usage=usage,
                balance_before=bal_header,
                evidence_hash=hashlib.sha256(
                    json.dumps({"id": ref, "model": request.model, "idem": request.idempotency_key}, sort_keys=True).encode()
                ).hexdigest(),
                raw={"id": body.get("id"), "model": body.get("model"), "usage": usage},
            )
        except OrbioGatewayError as e:
            if e.status_code is None:
                outcome = ExternalProviderOutcome.UNKNOWN
            elif e.status_code == 402:
                outcome = ExternalProviderOutcome.INSUFFICIENT_BALANCE
            elif e.status_code in {401, 403}:
                outcome = ExternalProviderOutcome.KEY_REVOKED
            elif e.status_code >= 500:
                outcome = ExternalProviderOutcome.UNKNOWN
            else:
                outcome = ExternalProviderOutcome.FAILURE
            return InferenceReceipt(
                outcome=outcome,
                provider_reference=None,
                model=request.model,
                usage={},
                error_message=str(e),
                evidence_hash=hashlib.sha256(f"gw-err|{request.idempotency_key}".encode()).hexdigest(),
            )


def build_orbio_provider(config: Optional[OrbioConfig] = None) -> Optional[OrbioAdapter]:
    cfg = config or OrbioConfig.from_env()
    if cfg.mode == ExternalProviderMode.DISABLED:
        return None
    return OrbioAdapter(config=cfg)
