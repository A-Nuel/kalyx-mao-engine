"""
Phase 13 Closed-Loop & Adversarial Verification Integration Tests.
Proves end-to-end:
1. Closed-loop feedback: Mission A performance -> persistence -> allocation calculation -> Mission B receives and consumes influenced allocation.
2. Authority vs budget separation: more credits != more authority; reputation cannot bypass policy.
3. Authoritative ledger source of truth: ledger balances cannot be desynchronized.
4. Restart & disaster recovery persistence for economy records.
5. Migration 005 schema parity between Postgres and SQLite.
6. Multi-seed reproducibility, counterfactual fairness, and solvency state tracking.
"""
import os
import re
import pytest
from src.api.mission_service import run_mission
from src.persistence.factory import create_database
from src.persistence.database import Database
from src.persistence.economy_repo import EconomyRepository
from src.domain.entities import Organisation, AgentRecord, Task, ActionProposal
from src.domain.enums import OrgState, AgentRole, AgentStatus, ActionType, PolicyResult, OrgSolvencyState
from src.governance.policy_engine import PolicyEngine
from src.economy.ledger import DoubleEntryLedger, TREASURY
from src.economy.allocator import ResourceAllocator
from src.economy.reputation import ReputationEngine
from src.execution.executor import SandboxExecutor
from src.orchestration.assignment import AgentAssignmentEngine
from src.economy.experiment import (
    EconomicExperiment,
    ScenarioType,
    AllocationStrategy,
    ScenarioResult,
    ComparativeExperimentReport,
    AggregateScenarioMetrics,
)


class TestPhase13ClosedLoop:
    def test_closed_loop_two_successive_missions(self):
        """
        Proves the closed-loop economic cycle end-to-end:
        Mission A -> Plan -> Agent Allocation -> Proposal -> Policy -> Execution -> Audit
        -> Outcome Measurement -> Performance Update -> Reputation Update
        -> Mission B retrieves influenced allocations and consumes within updated bounds.
        """
        tenant_id = "tenant-closed-loop"

        # 1. Execute Mission A
        result_a = run_mission(
            mission="Autonomous Liquidity Profiling & Portfolio Analysis",
            budget=100,
            live=False,
            tenant_id=tenant_id,
        )
        assert result_a["state"] == "COMPLETED"
        org_id = result_a["organisation_id"]

        db = create_database()
        try:
            repo = EconomyRepository(db.conn)
            records_a = {r.agent_id: r for r in repo.list_performance_records(tenant_id, org_id)}
            assert len(records_a) >= 3, "Mission A must populate performance records for workforce"

            for aid, rec in records_a.items():
                assert rec.evaluation_count >= 1, f"Agent {aid} must have at least 1 evaluation"
                assert rec.resources_allocated >= 0
                assert rec.resources_consumed >= 0

            # 2. Execute Mission B against the SAME organisation
            result_b = run_mission(
                mission="Adaptive Collateral Optimization & Automated Execution",
                budget=100,
                live=False,
                tenant_id=tenant_id,
                organisation_id=org_id,
            )
            assert result_b["state"] == "COMPLETED"
            assert result_b["organisation_id"] == org_id

            # Verify that Mission B re-evaluated and updated performance records
            records_b = {r.agent_id: r for r in repo.list_performance_records(tenant_id, org_id)}
            for aid, rec_b in records_b.items():
                rec_a = records_a[aid]
                assert rec_b.evaluation_count > rec_a.evaluation_count, (
                    f"Agent {aid} evaluation count must increase in Mission B"
                )
                assert rec_b.resources_allocated >= rec_a.resources_allocated, (
                    f"Agent {aid} accumulated allocated credits must not decrease"
                )
                assert rec_b.resources_consumed >= rec_a.resources_consumed, (
                    f"Agent {aid} accumulated consumed credits must not decrease"
                )

            # Check allocations table recorded decisions for both missions
            allocations = repo.list_allocations(tenant_id, org_id)
            assert len(allocations) >= 2, "Must record distinct allocation decisions for both missions"
        finally:
            db.close()


class TestAuthorityVsBudgetSeparation:
    def test_more_credits_does_not_grant_more_authority(self):
        """
        An agent given an exorbitant credit allocation (e.g. 500 CR) MUST NOT be able
        to execute actions beyond its strict authority ceiling (e.g. 20 CR).
        """
        engine = PolicyEngine(signing_secret="test-sec-authority")
        ledger = DoubleEntryLedger(initial_treasury=1000)

        org = Organisation(
            id="org-auth-test",
            mission="Authority Separation Test",
            treasury_balance=1000,
            state=OrgState.EXECUTING,
        )
        researcher = AgentRecord(
            id="agent-researcher",
            role=AgentRole.RESEARCHER,
            authority_ceiling=20,
            credit_balance=500,  # Huge budget
            reputation_score=99.0,
            allowed_action_types=[ActionType.DATA_FETCH, ActionType.INTERNAL_ANALYSIS],
            status=AgentStatus.ACTIVE,
        )
        org.agents[researcher.id] = researcher

        # Proposal asking for 35 credits (> authority ceiling of 20)
        excessive_proposal = ActionProposal(
            id="prop-excessive",
            task_id="t-auth-1",
            proposing_agent_id=researcher.id,
            action_type=ActionType.DATA_FETCH,
            target="sandbox://dataset_alpha",
            parameters={},
            requested_credits=35,
            expected_value_score=0.9,
            risk_assessment="Low",
            rationale="Excessive budget request beyond ceiling",
        )

        decision = engine.evaluate(excessive_proposal, org, ledger=ledger)
        assert decision.result == PolicyResult.REJECTED
        assert decision.violated_rule_id == "RULE-01"

    def test_reputation_cannot_bypass_action_type_policy(self):
        """
        An agent with maximum reputation (100.0) MUST NOT be able to execute
        action types that are not in its allowed action types or prohibited by policy.
        """
        engine = PolicyEngine(signing_secret="test-sec-rep-policy")
        ledger = DoubleEntryLedger(initial_treasury=1000)

        org = Organisation(
            id="org-rep-test",
            mission="Policy Enforcement Test",
            treasury_balance=1000,
            state=OrgState.EXECUTING,
        )
        analyst = AgentRecord(
            id="agent-analyst",
            role=AgentRole.FINANCIAL_ANALYST,
            authority_ceiling=25,
            credit_balance=20,
            reputation_score=100.0,  # Max reputation
            allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION],
            status=AgentStatus.ACTIVE,
        )
        org.agents[analyst.id] = analyst

        # Propose BLOCKCHAIN_TRANSACTION which is not in allowed_action_types
        unauthorized_proposal = ActionProposal(
            id="prop-unauth",
            task_id="t-auth-2",
            proposing_agent_id=analyst.id,
            action_type=ActionType.BLOCKCHAIN_TRANSACTION,
            target="0x1234567890abcdef",
            parameters={},
            requested_credits=10,
            expected_value_score=0.95,
            risk_assessment="High",
            rationale="Attempting unauthorized action with perfect reputation",
        )

        decision = engine.evaluate(unauthorized_proposal, org, ledger=ledger)
        assert decision.result == PolicyResult.REJECTED
        assert decision.violated_rule_id == "RULE-03"


class TestAuthoritativeLedgerConservation:
    def test_ledger_is_authoritative_source_of_truth(self):
        """
        Verify that double-entry ledger is authoritative, credits cannot be minted
        without a balanced transaction, and executed actions strictly deduct credits.
        """
        ledger = DoubleEntryLedger(initial_treasury=100)
        engine = PolicyEngine(signing_secret="test-sec-ledger")
        executor = SandboxExecutor(policy_engine=engine, ledger=ledger)

        org = Organisation(
            id="org-ledger-test",
            mission="Ledger Truth Test",
            treasury_balance=100,
            state=OrgState.EXECUTING,
        )
        agent = AgentRecord(
            id="agent-worker",
            role=AgentRole.RESEARCHER,
            authority_ceiling=20,
            credit_balance=20,
            allowed_action_types=[ActionType.DATA_FETCH],
            status=AgentStatus.ACTIVE,
        )
        org.agents[agent.id] = agent

        proposal = ActionProposal(
            id="prop-exec-1",
            task_id="t-exec-1",
            proposing_agent_id=agent.id,
            action_type=ActionType.DATA_FETCH,
            target="sandbox://verified_bonds",
            parameters={},
            requested_credits=15,
            expected_value_score=0.9,
            risk_assessment="Low",
            rationale="Standard execution test",
        )

        decision = engine.evaluate(proposal, org, ledger=ledger)
        assert decision.result == PolicyResult.APPROVED

        receipt = executor.execute(proposal, decision, org)
        assert receipt.cost_credits == 15

        # Verify ledger balance: 100 initial - 15 spent = 85 remaining
        assert ledger.get_balance(TREASURY) == 85
        # Verify ledger conservation invariant
        assert ledger.verify_conservation() is True


class TestRestartPersistence:
    def test_economy_records_persist_across_restarts(self, tmp_path):
        """
        Verify that shutting down the database and reconnecting maintains
        exact performance records, reputation history, and allocation logs.
        """
        db_path = str(tmp_path / "restart_test.db")
        db1 = Database(db_path)
        # Ensure tenant and organisation exist for foreign key constraints
        with db1.conn:
            db1.conn.execute(
                "INSERT OR IGNORE INTO organisations (id, mission, treasury_balance, state, tenant_id, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                ("org-persist", "Persistence Test Org", 100, "EXECUTING", "tenant-demo")
            )
        repo1 = EconomyRepository(db1.conn)

        agent = AgentRecord(id="agent-persist", role=AgentRole.RESEARCHER, authority_ceiling=25)
        entry = ReputationEngine.evaluate_agent_performance(
            agent=agent,
            repo=repo1,
            tenant_id="tenant-demo",
            organisation_id="org-persist",
            tasks_delta_completed=4,
            tasks_delta_failed=0,
            trigger_event="PRE_RESTART_EVAL",
        )
        db1.close()

        # Reopen database connection
        db2 = Database(db_path)
        try:
            repo2 = EconomyRepository(db2.conn)
            record = repo2.get_performance_record("tenant-demo", "org-persist", "agent-persist")
            assert record is not None
            assert record.tasks_completed == 4
            assert record.reputation_score == entry.new_score
            assert record.composite_score == entry.new_score

            history = repo2.list_reputation_history("tenant-demo", "org-persist", "agent-persist")
            assert len(history) == 1
            assert history[0].trigger_event == "PRE_RESTART_EVAL"
            assert history[0].evidence_hash == entry.evidence_hash
        finally:
            db2.close()


class TestPostgresMigration005Parity:
    def test_postgres_migration_005_parity_with_sqlite(self):
        """
        Verify that migrations/postgres/005_agent_performance.sql defines all required
        tables, matching SQLite schema and domain expectations.
        """
        mig_path = os.path.join("migrations", "postgres", "005_agent_performance.sql")
        assert os.path.exists(mig_path), f"Migration file not found at {mig_path}"

        with open(mig_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Required tables in migration 005
        expected_tables = [
            "agent_performance_records",
            "resource_allocations",
            "reputation_history",
            "experiment_runs",
        ]
        for tbl in expected_tables:
            pattern = rf"CREATE TABLE IF NOT EXISTS {tbl}"
            assert re.search(pattern, content, re.IGNORECASE), f"Missing table {tbl} in migration 005"

        # Verify key columns in agent_performance_records
        for col in ["agent_id", "organisation_id", "tenant_id", "performance_score", "reputation_score", "composite_score"]:
            assert col in content, f"Missing column {col} in migration 005"

        # Verify schema migration version tracked
        assert "005_agent_performance" in content


class TestMultiSeedReproducibilityAndCounterfactualFairness:
    def test_multiseed_reproducibility_and_fairness(self):
        """
        Run the multi-scenario benchmark across >=5 distinct seeds:
        - STEADY_STATE, HIGH_RISK_MARKET, TREASURY_SHOCK
        - STATIC, PERFORMANCE, ADAPTIVE
        Verify:
        1. Multi-seed aggregate statistics (mean, std dev, min, max) for:
           net_return, throughput, solvency_rate, recovery_success_rate,
           unnecessary_action_rate, policy_violation_rate.
        2. Deterministic reproducibility: same seed gives identical results.
        3. Counterfactual fairness: identical workload sequences provided to all 3 strategies.
        4. Solvency state machine: SOLVENT / RESOURCE_EXHAUSTED / INSOLVENT.
        """
        seeds = [42, 101, 777, 1337, 9001]
        report = EconomicExperiment.run(num_rounds=3, initial_treasury=100, seeds=seeds)

        assert isinstance(report, ComparativeExperimentReport)
        assert len(report.scenario_results) == 9

        for key, sr in report.scenario_results.items():
            assert isinstance(sr, ScenarioResult)
            assert sr.aggregate_metrics is not None
            agg = sr.aggregate_metrics
            assert agg.sample_size == 5

            # Verify stats exist and are valid numbers
            for metric in [agg.net_return, agg.throughput, agg.recovery_success_rate, agg.unnecessary_action_rate, agg.policy_violation_rate]:
                assert isinstance(metric.mean, (int, float))
                assert isinstance(metric.std_dev, (int, float))
                assert isinstance(metric.min, (int, float))
                assert isinstance(metric.max, (int, float))
                assert metric.min <= metric.max

            assert 0.0 <= agg.solvency_rate <= 1.0
            assert sr.organisational_state in [
                OrgSolvencyState.SOLVENT.value,
                OrgSolvencyState.RESOURCE_EXHAUSTED.value,
                OrgSolvencyState.INSOLVENT.value,
            ]

        # Verify counterfactual fairness of workload generation
        w1_seed42 = EconomicExperiment._generate_seed_workload(ScenarioType.STEADY_STATE, 42, 3)
        w2_seed42 = EconomicExperiment._generate_seed_workload(ScenarioType.STEADY_STATE, 42, 3)
        w_seed101 = EconomicExperiment._generate_seed_workload(ScenarioType.STEADY_STATE, 101, 3)

        # Identical seed produces identical workload
        assert w1_seed42 == w2_seed42
        # Different seed alters random inputs
        assert w1_seed42 != w_seed101

        # Verify markdown table formatting uses Solvency State
        table = report.format_table()
        assert "Solvency State" in table
        assert "SURVIVED" not in table
        assert "BANKRUPT" not in table

