"""Phase 17 M1 — live adapter configuration and provenance tests."""

import os

import pytest

from src.domain.work_order import WorkOrder
from src.execution.orbio_gateway_adapter import OrbioGatewayAdapter
from src.execution.work_executor import SimulatedWorkExecutor


def _work_order() -> WorkOrder:
    return WorkOrder(
        tenant_id="tenant-m1",
        organisation_id="org-m1",
        work_order_id="wo-m1",
        client_id="client-m1",
        title="M1 adapter test",
        description="Verify live/simulated provenance",
        deliverable_type="DOCS",
        required_orbio_credits=10,
        bounty_amount=20,
    )


def test_orbio_gateway_uses_official_api_base_and_model(monkeypatch):
    monkeypatch.delenv("ORBIO_GATEWAY_BASE", raising=False)
    monkeypatch.delenv("ORBIO_API_BASE_URL", raising=False)
    monkeypatch.delenv("ORBIO_MODEL", raising=False)
    adapter = OrbioGatewayAdapter(api_key="sk-test", credit_store={"org-m1": 100})
    assert adapter.base_url == "https://api.orbio.so/api/v1"
    assert adapter.model == "anthropic/claude-fable-5.1"


def test_missing_key_can_use_explicit_simulated_fallback(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "demo")
    monkeypatch.delenv("ORBIO_API_KEY", raising=False)
    executor = SimulatedWorkExecutor(credit_store={"org-m1": 100})
    adapter = OrbioGatewayAdapter(
        fallback_executor=executor,
        credit_store={"org-m1": 100},
        allow_simulated_fallback=True,
    )

    deliverable = adapter.execute_work(_work_order(), "agent-m1", "org-m1")

    assert deliverable.execution_telemetry["is_simulated"] is True
    assert deliverable.execution_telemetry["provenance"] == "SIMULATED"
    assert deliverable.execution_telemetry["fallback_reason"] == "missing_api_key"


def test_production_missing_key_fails_closed(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.delenv("ORBIO_API_KEY", raising=False)
    adapter = OrbioGatewayAdapter(
        fallback_executor=SimulatedWorkExecutor(credit_store={"org-m1": 100}),
        credit_store={"org-m1": 100},
    )

    with pytest.raises(RuntimeError, match="ORBIO_API_KEY is required"):
        adapter.execute_work(_work_order(), "agent-m1", "org-m1")


def test_malformed_live_response_falls_back_and_is_marked_simulated():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"invalid_key": "missing choices"})

    transport = httpx.MockTransport(handler)
    adapter = OrbioGatewayAdapter(
        api_key="sk-test",
        fallback_executor=SimulatedWorkExecutor(credit_store={"org-m1": 100}),
        credit_store={"org-m1": 100},
        transport=transport,
        allow_simulated_fallback=True,
    )

    deliverable = adapter.execute_work(_work_order(), "agent-m1", "org-m1")

    assert deliverable.execution_telemetry["is_simulated"] is True
    assert deliverable.execution_telemetry["provenance"] == "SIMULATED"
    assert deliverable.execution_telemetry["fallback_reason"] == "gateway_error:ValueError"


def test_valid_live_response_is_marked_live():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.orbio.so/api/v1/chat/completions")
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-m1",
                "choices": [{"message": {"role": "assistant", "content": "{\"summary\":\"ok\"}"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    transport = httpx.MockTransport(handler)
    adapter = OrbioGatewayAdapter(
        api_key="sk-test",
        credit_store={"org-m1": 100},
        transport=transport,
        allow_simulated_fallback=False,
    )

    deliverable = adapter.execute_work(_work_order(), "agent-m1", "org-m1")

    assert deliverable.execution_telemetry["is_simulated"] is False
    assert deliverable.execution_telemetry["provenance"] == "LIVE_ORBIO"
    assert deliverable.execution_telemetry["total_tokens"] == 5
    assert adapter.get_credit_balance("org-m1") == 90


def test_simulated_telemetry_never_contains_api_key():
    secret = "sk-super-secret-test-key"
    executor = SimulatedWorkExecutor(credit_store={"org-m1": 100})
    adapter = OrbioGatewayAdapter(
        api_key=secret,
        fallback_executor=executor,
        credit_store={"org-m1": 100},
        transport=__import__("httpx").MockTransport(
            lambda request: __import__("httpx").Response(503, json={"error": "upstream"})
        ),
        allow_simulated_fallback=True,
    )

    deliverable = adapter.execute_work(_work_order(), "agent-m1", "org-m1")

    telemetry = deliverable.execution_telemetry
    assert telemetry["is_simulated"] is True
    assert telemetry["activated_api_key_used"] is True
    assert secret not in str(telemetry)
