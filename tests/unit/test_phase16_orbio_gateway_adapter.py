"""Unit tests for Phase 16 Milestone 3: OrbioGatewayAdapter.

Verifies:
1. OrbioGatewayAdapter execution using httpx.MockTransport.
2. Accurate telemetry capture (prompt_tokens, completion_tokens, total_tokens, latency).
3. Credit quota enforcement (raises InsufficientCreditsError if insufficient credits).
4. Graceful fallback to SimulatedWorkExecutor on network error or missing credentials.
"""

import json
import httpx
import pytest

from src.domain.enums import CurrencyAsset, WorkOrderStatus
from src.domain.exceptions import InsufficientCreditsError
from src.domain.work_order import WorkOrder
from src.execution.orbio_gateway_adapter import OrbioGatewayAdapter
from src.execution.work_executor import SimulatedWorkExecutor


@pytest.fixture
def sample_work_order():
    return WorkOrder(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        work_order_id="wo-gateway-01",
        client_id="client-enterprise",
        title="Smart Contract Security Verification",
        description="Verify formal invariants of the swap router",
        deliverable_type="SECURITY_AUDIT",
        required_orbio_credits=250_000,
        bounty_amount=500,
    )


def test_orbio_gateway_successful_execution(sample_work_order):
    mock_response_data = {
        "id": "chatcmpl-test-123",
        "object": "chat.completion",
        "created": 1726500000,
        "model": "orbio-compute-v1",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "findings": [{"id": "SEC-01", "severity": "CLEAN"}],
                        "summary": "Router contracts pass all invariants",
                    }),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 85,
            "total_tokens": 205,
        },
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers.get("Authorization") == "Bearer orbio-secret-test-key"
        assert request.headers.get("X-Kalyx-Work-Order") == "wo-gateway-01"
        return httpx.Response(200, json=mock_response_data)

    transport = httpx.MockTransport(mock_handler)
    adapter = OrbioGatewayAdapter(
        base_url="https://api.orbio.net/v1",
        api_key="orbio-secret-test-key",
        transport=transport,
    )
    adapter.set_credit_balance("org-alpha", 500_000)

    deliverable = adapter.execute_work(
        work_order=sample_work_order,
        producer_agent_id="agent-engineer",
        organisation_id="org-alpha",
    )

    assert deliverable is not None
    assert deliverable.work_order_id == "wo-gateway-01"
    assert deliverable.producer_agent_id == "agent-engineer"
    assert deliverable.orbio_credits_consumed == 250_000
    assert adapter.get_credit_balance("org-alpha") == 250_000

    # Verify telemetry
    telemetry = deliverable.execution_telemetry
    assert telemetry["executor"] == "OrbioGatewayAdapter"
    assert telemetry["prompt_tokens"] == 120
    assert telemetry["completion_tokens"] == 85
    assert telemetry["total_tokens"] == 205
    assert "latency_ms" in telemetry
    assert telemetry["latency_ms"] >= 0

    # Content payload structure
    assert deliverable.content_payload["summary"] == "Router contracts pass all invariants"


def test_orbio_gateway_insufficient_credits(sample_work_order):
    adapter = OrbioGatewayAdapter(
        base_url="https://api.orbio.net/v1",
        api_key="orbio-secret-test-key",
    )
    adapter.set_credit_balance("org-alpha", 10_000)  # Requires 250_000

    with pytest.raises(InsufficientCreditsError) as exc_info:
        adapter.execute_work(
            work_order=sample_work_order,
            producer_agent_id="agent-engineer",
            organisation_id="org-alpha",
        )
    assert "Insufficient Orbio CREDIT" in str(exc_info.value)


def test_orbio_gateway_fallback_on_network_error(sample_work_order):
    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "Bad Gateway"})

    transport = httpx.MockTransport(failing_handler)
    fallback = SimulatedWorkExecutor()
    adapter = OrbioGatewayAdapter(
        base_url="https://api.orbio.net/v1",
        api_key="orbio-secret-test-key",
        transport=transport,
        fallback_executor=fallback,
    )
    adapter.set_credit_balance("org-alpha", 500_000)
    fallback._credit_store["org-alpha"] = 500_000

    deliverable = adapter.execute_work(
        work_order=sample_work_order,
        producer_agent_id="agent-engineer",
        organisation_id="org-alpha",
    )

    assert deliverable is not None
    # Fallback to SimulatedWorkExecutor succeeded
    assert deliverable.execution_telemetry["executor"] == "SimulatedWorkExecutor"
    assert deliverable.work_order_id == "wo-gateway-01"


def test_orbio_gateway_fallback_on_missing_key(sample_work_order):
    fallback = SimulatedWorkExecutor()
    adapter = OrbioGatewayAdapter(
        base_url="https://api.orbio.net/v1",
        api_key=None,
        fallback_executor=fallback,
    )
    adapter.set_credit_balance("org-alpha", 500_000)
    fallback._credit_store["org-alpha"] = 500_000

    deliverable = adapter.execute_work(
        work_order=sample_work_order,
        producer_agent_id="agent-engineer",
        organisation_id="org-alpha",
    )

    assert deliverable is not None
    assert deliverable.execution_telemetry["executor"] == "SimulatedWorkExecutor"
