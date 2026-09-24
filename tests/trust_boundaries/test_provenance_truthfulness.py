"""Tests ensuring provenance truthfulness and simulated vs live integrity."""

import pytest
from src.domain.work_order import WorkDeliverable


def test_simulated_cannot_claim_live_provenance():
    with pytest.raises(ValueError, match="Provenance conflict: Simulated execution cannot be tagged as LIVE"):
        WorkDeliverable.create(
            work_order_id="wo-123",
            producer_agent_id="agent-1",
            content_payload={"summary": "Fake live result"},
            orbio_credits_consumed=1,
            execution_telemetry={
                "is_simulated": True,
                "provenance": "LIVE",
            },
        )


def test_simulated_cannot_claim_live_orbio_provenance():
    with pytest.raises(ValueError, match="Provenance conflict: Simulated execution cannot be tagged as LIVE"):
        WorkDeliverable.create(
            work_order_id="wo-123",
            producer_agent_id="agent-1",
            content_payload={"summary": "Fake live orbio result"},
            orbio_credits_consumed=1,
            execution_telemetry={
                "is_simulated": True,
                "provenance": "LIVE_ORBIO",
            },
        )


def test_simulated_with_simulated_provenance_passes():
    deliverable = WorkDeliverable.create(
        work_order_id="wo-123",
        producer_agent_id="agent-1",
        content_payload={"summary": "Legitimate simulated result"},
        orbio_credits_consumed=1,
        execution_telemetry={
            "is_simulated": True,
            "provenance": "SIMULATED",
        },
    )
    assert deliverable.execution_telemetry["is_simulated"] is True
    assert deliverable.execution_telemetry["provenance"] == "SIMULATED"


def test_live_execution_with_live_provenance_passes():
    deliverable = WorkDeliverable.create(
        work_order_id="wo-123",
        producer_agent_id="agent-1",
        content_payload={"summary": "Legitimate live execution"},
        orbio_credits_consumed=1,
        execution_telemetry={
            "is_simulated": False,
            "provenance": "LIVE",
            "model": "anthropic/claude-3-haiku",
        },
    )
    assert deliverable.execution_telemetry["is_simulated"] is False
    assert deliverable.execution_telemetry["provenance"] == "LIVE"
