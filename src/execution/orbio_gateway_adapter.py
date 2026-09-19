"""Real Orbio Gateway Adapter — Phase 16 Milestone 3.

Connects to Orbio compute gateway via OpenAI-compatible `/v1/chat/completions`.
Captures token telemetry, meters credits, and yields verified WorkDeliverables.
Includes mock transport / fallback support for deterministic offline CI.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

from src.domain.exceptions import InsufficientCreditsError
from src.domain.work_order import WorkDeliverable, WorkOrder
from src.execution.work_executor import BaseWorkExecutor, SimulatedWorkExecutor

logger = logging.getLogger(__name__)

DEFAULT_ORBIO_BASE_URL = "https://api.orbio.so/api/v1"
DEFAULT_ORBIO_MODEL = "anthropic/claude-fable-5.1"


class OrbioGatewayAdapter(BaseWorkExecutor):
    """
    Production-grade Orbio Gateway Adapter with telemetry and token-level metering.
    Interchangeable with SimulatedWorkExecutor.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_ORBIO_BASE_URL,
        api_key: Optional[str] = None,
        model: str = DEFAULT_ORBIO_MODEL,
        credit_store: Optional[Dict[str, int]] = None,
        fallback_executor: Optional[BaseWorkExecutor] = None,
        http_client: Optional[httpx.Client] = None,
        transport: Optional[httpx.BaseTransport] = None,
        timeout_seconds: float = 30.0,
        allow_simulated_fallback: Optional[bool] = None,
    ) -> None:
        self.base_url = (os.getenv("ORBIO_GATEWAY_BASE") or os.getenv("ORBIO_API_BASE_URL") or base_url).rstrip("/")
        self.api_key = api_key or os.getenv("ORBIO_API_KEY")
        self.model = os.getenv("ORBIO_MODEL") or model
        if allow_simulated_fallback is None:
            configured = os.getenv("KALYX_ORBIO_ALLOW_SIMULATED_FALLBACK")
            if configured is not None:
                allow_simulated_fallback = configured.strip().lower() in {"1", "true", "yes", "on"}
            else:
                allow_simulated_fallback = os.getenv("KALYX_ENV", "demo").strip().lower() not in {"production", "prod"}
        self.allow_simulated_fallback = bool(allow_simulated_fallback)
        self._credit_store = credit_store if credit_store is not None else {}
        self.fallback_executor = fallback_executor or SimulatedWorkExecutor(credit_store=self._credit_store)
        self.timeout_seconds = timeout_seconds

        if http_client is not None:
            self._client = http_client
        elif transport is not None:
            self._client = httpx.Client(transport=transport, timeout=timeout_seconds)
        else:
            self._client = httpx.Client(timeout=timeout_seconds)

    def set_credit_balance(self, organisation_id: str, credits: int) -> None:
        self._credit_store[organisation_id] = max(0, int(credits))

    def get_credit_balance(self, organisation_id: str) -> int:
        return self._credit_store.get(organisation_id, 0)

    def deduct_credits(self, organisation_id: str, amount: int) -> None:
        current = self.get_credit_balance(organisation_id)
        if current < amount:
            raise InsufficientCreditsError(
                f"Organisation '{organisation_id}' has {current} Orbio credits, but required {amount}"
            )
        self._credit_store[organisation_id] = current - amount

    def execute_work(
        self,
        work_order: WorkOrder,
        producer_agent_id: str,
        organisation_id: str,
        activated_api_key: Optional[str] = None,
    ) -> WorkDeliverable:
        # 1. Resource Preflight: Check Orbio CREDIT quota
        required_credits = work_order.required_orbio_credits
        available_credits = self.get_credit_balance(organisation_id)
        if available_credits < required_credits:
            raise InsufficientCreditsError(
                f"Insufficient Orbio CREDIT: organisation '{organisation_id}' has {available_credits}, "
                f"work order '{work_order.work_order_id}' requires {required_credits}"
            )

        effective_key = activated_api_key or self.api_key

        # Never silently simulate a production execution unless explicitly enabled.
        if not effective_key:
            if not self.allow_simulated_fallback:
                raise RuntimeError("ORBIO_API_KEY is required when simulated fallback is disabled")
            logger.info("No active Orbio API key; using explicit simulated fallback")
            return self._execute_fallback(
                work_order=work_order,
                producer_agent_id=producer_agent_id,
                organisation_id=organisation_id,
                activated_api_key=activated_api_key,
                reason="missing_api_key",
            )

        # 2. Call Orbio /chat/completions endpoint
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {effective_key or 'mock-key'}",
            "Content-Type": "application/json",
            "X-Kalyx-Work-Order": work_order.work_order_id,
            "X-Kalyx-Producer": producer_agent_id,
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": f"You are a specialized enterprise AI performing deliverable generation for {work_order.deliverable_type}. Output clean structured results.",
                },
                {
                    "role": "user",
                    "content": f"Task: {work_order.title}\nDescription: {work_order.description}\nRequired Type: {work_order.deliverable_type}",
                },
            ],
            "temperature": 0.2,
        }

        start_time = time.perf_counter()
        try:
            response = self._client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            if not self._is_valid_chat_response(res_data):
                raise ValueError("Orbio returned a malformed chat completion response")
        except Exception as e:
            logger.warning("Orbio Gateway call failed (%s)", e)
            if self.allow_simulated_fallback and self.fallback_executor:
                return self._execute_fallback(
                    work_order=work_order,
                    producer_agent_id=producer_agent_id,
                    organisation_id=organisation_id,
                    activated_api_key=activated_api_key,
                    reason=f"gateway_error:{type(e).__name__}",
                )
            raise

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        # 3. Parse usage & content
        usage = res_data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        choices = res_data.get("choices", [])
        content_text = choices[0]["message"]["content"] if choices else "Task execution completed."

        # Attempt to parse content as JSON if available, otherwise wrap
        try:
            parsed_content = json.loads(content_text)
            if not isinstance(parsed_content, dict):
                parsed_content = {"result": parsed_content}
        except Exception:
            parsed_content = {
                "work_order_id": work_order.work_order_id,
                "deliverable_type": work_order.deliverable_type,
                "target": work_order.title,
                "result": content_text,
                "summary": f"Delivered work for {work_order.title}.",
            }

        # 4. Deduct compute credits
        self.deduct_credits(organisation_id, required_credits)

        # 5. Build telemetry
        telemetry = {
            "is_simulated": False,
            "provenance": "LIVE_ORBIO",
            "executor": "OrbioGatewayAdapter",
            "model": self.model,
            "endpoint": endpoint,
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "credits_deducted": required_credits,
        }

        return WorkDeliverable.create(
            work_order_id=work_order.work_order_id,
            producer_agent_id=producer_agent_id,
            content_payload=parsed_content,
            orbio_credits_consumed=required_credits,
            execution_telemetry=telemetry,
        )


    @staticmethod
    def _is_valid_chat_response(response: Any) -> bool:
        if not isinstance(response, dict):
            return False
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            return False
        first = choices[0]
        if not isinstance(first, dict):
            return False
        message = first.get("message")
        return isinstance(message, dict) and isinstance(message.get("content"), str) and bool(message.get("content").strip())

    def _execute_fallback(
        self,
        *,
        work_order: WorkOrder,
        producer_agent_id: str,
        organisation_id: str,
        activated_api_key: Optional[str],
        reason: str,
    ) -> WorkDeliverable:
        if self.fallback_executor is None:
            raise RuntimeError("Simulated fallback is enabled but no fallback executor is configured")
        deliv = self.fallback_executor.execute_work(
            work_order=work_order,
            producer_agent_id=producer_agent_id,
            organisation_id=organisation_id,
            activated_api_key=activated_api_key,
        )
        if isinstance(deliv.execution_telemetry, dict):
            deliv.execution_telemetry.update({
                "is_simulated": True,
                "provenance": "SIMULATED",
                "fallback_reason": reason,
                "configured_gateway": self.base_url,
                "configured_model": self.model,
            })
        return deliv

    def close(self) -> None:
        self._client.close()
