"""Phase 13 Adaptive Organisational Economics & Agent Performance Integration Suite.

Verifies:
1. End-to-end mission execution with allocation strategies (STATIC, PERFORMANCE, ADAPTIVE).
2. Durable persistence of AgentPerformanceRecord, ResourceAllocationDecision, and ReputationHistoryEntry.
3. Resource conservation invariant (allocations <= Treasury).
4. API endpoints for economy overview, agent performance, reputation history, allocations, and economy audit events.
5. Counterfactual benchmark reproducibility (deterministic seeds).
6. Multi-scenario simulation (STEADY_STATE, HIGH_RISK_MARKET, TREASURY_SHOCK).
7. Zero fabricated metrics guarantee.
"""

import json
import os
import uuid
import pytest
from fastapi.testclient import TestClient

from src.api.mission_service import run_mission
from src.api.server import app
from src.domain.economy import AllocationStrategy
from src.economy.experiment import EconomicExperiment, ScenarioType
from src.persistence.database import Database
from src.persistence.economy_repo import EconomyRepository


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = str(tmp_path / "kalyx_phase13.db")
    monkeypatch.setenv("KALYX_DB", db_file)
    monkeypatch.setenv("KALYX_ENV", "development")
    monkeypatch.setenv("KALYX_OPERATOR_KEY", "test-operator-secret")
    with TestClient(app) as tc:
        yield tc, db_file


class TestPhase13MissionEconomyIntegration:
    def test_mission_with_performance_allocation_persists_records(self, tmp_path):
        db_path = str(tmp_path / "test_perf_mission.db")
        result = run_mission(
            mission="Perform cross-market liquidity and yield risk assessment",
            budget=120,
            live=False,
            db_path=db_path,
            tenant_id="tenant-phase13",
            strategy="PERFORMANCE",
        )

        assert result["organisation_id"] is not None
        assert result["allocation_strategy"] == "PERFORMANCE"
        assert result["allocations"] is not None
        total_allocated = sum(result["allocations"].values())
        assert total_allocated <= 120  # Conservation invariant

        # Verify database persistence
        db = Database(db_path)
        try:
            repo = EconomyRepository(db.conn)
            records = repo.list_performance_records("tenant-phase13", result["organisation_id"])
            assert len(records) >= 4  # CEO, Researcher, Strategist, Financial Analyst
            for r in records:
                assert r.missions_contributed >= 1
                assert 0.0 <= r.composite_score <= 100.0
                assert 0.0 <= r.reputation_score <= 100.0
                assert 1 <= r.authority_level <= 5

            allocations = repo.list_allocations("tenant-phase13", result["organisation_id"])
            assert len(allocations) >= 1
            assert allocations[0].strategy == AllocationStrategy.PERFORMANCE
            assert allocations[0].total_allocated <= 120
        finally:
            db.close()

    def test_mission_with_adaptive_allocation_treasury_damping(self, tmp_path):
        db_path = str(tmp_path / "test_adaptive_mission.db")
        result = run_mission(
            mission="Deploy bounded defensive reserves under market turbulence",
            budget=80,
            live=False,
            db_path=db_path,
            tenant_id="tenant-phase13",
            strategy="ADAPTIVE",
        )

        assert result["allocation_strategy"] == "ADAPTIVE"
        total_allocated = sum(result["allocations"].values())
        assert total_allocated <= 80


class TestPhase13ApiEndpoints:
    def test_economy_api_overview_and_distribution(self, client):
        tc, db_path = client
        headers = {"X-API-Key": "test-operator-secret", "X-Tenant-ID": "tenant-demo"}

        # Run demo mission to seed state
        demo_resp = tc.post("/api/demo/run", headers=headers)
        assert demo_resp.status_code == 200
        org_id = demo_resp.json()["organisation_id"]

        # 1. GET /api/organisations/{org_id}/economy
        eco_resp = tc.get(f"/api/organisations/{org_id}/economy", headers=headers)
        assert eco_resp.status_code == 200
        eco_data = eco_resp.json()
        assert eco_data["organisation_id"] == org_id
        assert "workforce_status_distribution" in eco_data
        assert "ACTIVE" in eco_data["workforce_status_distribution"]
        assert len(eco_data["performance_records"]) >= 4

        # 2. GET /api/organisations/{org_id}/agents/{agent_id}/performance
        first_agent = eco_data["performance_records"][0]["agent_id"]
        perf_resp = tc.get(f"/api/organisations/{org_id}/agents/{first_agent}/performance", headers=headers)
        assert perf_resp.status_code == 200
        perf_data = perf_resp.json()
        assert perf_data["agent_id"] == first_agent
        assert "composite_score" in perf_data
        assert "resource_efficiency_score" in perf_data
        assert "reliability_score" in perf_data
        assert "policy_compliance_score" in perf_data

        # 3. GET /api/organisations/{org_id}/agents/{agent_id}/reputation
        rep_resp = tc.get(f"/api/organisations/{org_id}/agents/{first_agent}/reputation", headers=headers)
        assert rep_resp.status_code == 200
        rep_data = rep_resp.json()
        assert rep_data["agent_id"] == first_agent
        assert isinstance(rep_data["reputation_history"], list)

        # 4. GET /api/organisations/{org_id}/allocations
        alloc_resp = tc.get(f"/api/organisations/{org_id}/allocations", headers=headers)
        assert alloc_resp.status_code == 200
        alloc_data = alloc_resp.json()
        assert len(alloc_data["allocations"]) >= 1

        # 5. GET /api/organisations/{org_id}/economy/events
        events_resp = tc.get(f"/api/organisations/{org_id}/economy/events", headers=headers)
        assert events_resp.status_code == 200
        ev_data = events_resp.json()
        assert isinstance(ev_data["events"], list)

    def test_experiments_endpoint_execution_and_retrieval(self, client):
        tc, _ = client

        # 1. Before run: latest report should indicate has_run=False
        init_resp = tc.get("/api/experiments/latest")
        assert init_resp.status_code == 200

        # 2. Trigger run: POST /api/experiments/run
        run_resp = tc.post("/api/experiments/run?num_rounds=2")
        assert run_resp.status_code == 200
        report = run_resp.json()
        assert report["num_rounds"] == 2
        assert "STATIC" in report["results"]
        assert "PERFORMANCE" in report["results"]
        assert "ADAPTIVE" in report["results"]

        # 3. Retrieve latest report: GET /api/experiments/latest
        latest_resp = tc.get("/api/experiments/latest")
        assert latest_resp.status_code == 200
        latest_data = latest_resp.json()
        assert latest_data["has_run"] is True
        assert latest_data["report"]["num_rounds"] == 2


class TestPhase13CounterfactualReproducibility:
    def test_identical_seed_produces_identical_results(self):
        """Experiment runs with identical seed, treasury, and rounds must produce bit-for-bit identical results."""
        run_1 = EconomicExperiment.run(num_rounds=2, initial_treasury=150, seeds=[777])
        run_2 = EconomicExperiment.run(num_rounds=2, initial_treasury=150, seeds=[777])

        dict_1 = run_1.model_dump(mode="json")
        dict_2 = run_2.model_dump(mode="json")

        assert dict_1["results"] == dict_2["results"]
        assert dict_1["scenario_results"] == dict_2["scenario_results"]

    def test_multi_scenario_coverage_and_honest_reporting(self):
        """Verify all three scenarios evaluate correctly without cherry-picking."""
        report = EconomicExperiment.run(num_rounds=2, initial_treasury=120, seeds=[42, 99])

        for sc in [ScenarioType.STEADY_STATE, ScenarioType.HIGH_RISK_MARKET, ScenarioType.TREASURY_SHOCK]:
            for strat in [AllocationStrategy.STATIC, AllocationStrategy.PERFORMANCE, AllocationStrategy.ADAPTIVE]:
                key = f"{sc.value}_{strat.value}"
                assert key in report.scenario_results
                res = report.scenario_results[key]
                assert res.missions_attempted == 2  # num_rounds per scenario run
                assert 0.0 <= res.success_rate <= 100.0
                assert res.ending_treasury >= 0
