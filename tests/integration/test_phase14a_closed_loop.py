"""Phase 14A closed-loop: allocation -> Orbio usage -> independent outcome -> performance -> next allocation.

Orbio telemetry must not directly mutate Kalyx performance.
Only independent Kalyx outcome evaluation drives performance updates.
"""
from __future__ import annotations

from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, AgentStatus, OrgState, PolicyResult
from src.domain.economy import AllocationStrategy
from src.economy.allocator import ResourceAllocator
from src.economy.ledger import DoubleEntryLedger
from src.economy.reputation import ReputationEngine
from src.external.models import ExternalProviderMode, ExternalProviderOutcome, InferenceRequest
from src.external.orbio.adapter import OrbioAdapter
from src.external.orbio.config import OrbioConfig
from src.external.orbio.service import ExternalEconomyService
from src.external.orbio.simulated_provider import SimulatedOrbioProvider
from src.governance.policy_engine import PolicyEngine
from src.governance.external_economy_rules import (
    ExternalInferenceAuthorityRule,
    ExternalInferenceTargetRule,
    OrbioKeyLifecycleAuthorityRule,
)
from src.governance.rules import (
    AgentStatusRule,
    OrgPauseRule,
    RolePermissionRule,
    SpendLimitRule,
    TreasuryBalanceRule,
)
from src.persistence.database import Database
from src.persistence.economy_repo import EconomyRepository


def _orbio_policy_engine() -> PolicyEngine:
    return PolicyEngine(
        rules=[
            OrgPauseRule(),
            AgentStatusRule(),
            TreasuryBalanceRule(),
            SpendLimitRule(),
            RolePermissionRule(),
            OrbioKeyLifecycleAuthorityRule(),
            ExternalInferenceAuthorityRule(),
            ExternalInferenceTargetRule(),
        ],
        signing_secret="phase14a-closed-loop",
        human_approval_threshold=1000,
    )


def test_closed_loop_orbio_usage_independent_outcome_changes_next_allocation():
    """
    Mission A:
      allocate -> authorize EXTERNAL_INFERENCE -> consume Orbio (simulated)
      -> Kalyx independently records task SUCCESS -> performance improves
    Mission B:
      PERFORMANCE allocation must reflect updated score (higher share for improved agent)
    """
    db = Database(":memory:")
    try:
        with db.conn:
            db.conn.execute(
                "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
                ("tenant-14a", "T14A", "active"),
            )
            db.conn.execute(
                "INSERT OR IGNORE INTO organisations (id, mission, treasury_balance, state, tenant_id, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                ("org-14a", "Orbio Closed Loop", 100, "EXECUTING", "tenant-14a"),
            )

        repo = EconomyRepository(db.conn)
        ledger = DoubleEntryLedger(initial_treasury=100)
        engine = _orbio_policy_engine()

        org = Organisation(
            id="org-14a",
            mission="Orbio Closed Loop",
            tenant_id="tenant-14a",
            treasury_balance=100,
            state=OrgState.EXECUTING,
        )
        researcher = AgentRecord(
            id="agent-research",
            role=AgentRole.RESEARCHER,
            authority_ceiling=40,
            credit_balance=0,
            reputation_score=70.0,
            performance_score=70.0,
            allowed_action_types=[ActionType.EXTERNAL_INFERENCE, ActionType.INTERNAL_ANALYSIS],
            status=AgentStatus.ACTIVE,
        )
        strategist = AgentRecord(
            id="agent-strategy",
            role=AgentRole.STRATEGIST,
            authority_ceiling=40,
            credit_balance=0,
            reputation_score=70.0,
            performance_score=70.0,
            allowed_action_types=[ActionType.INTERNAL_ANALYSIS],
            status=AgentStatus.ACTIVE,
        )
        org.agents[researcher.id] = researcher
        org.agents[strategist.id] = strategist

        # Identical baseline so Mission A starts fair
        for agent in org.agents.values():
            ReputationEngine.evaluate_agent_performance(
                agent=agent,
                repo=repo,
                tenant_id="tenant-14a",
                organisation_id="org-14a",
                tasks_delta_completed=1,
                tasks_delta_failed=0,
                resources_allocated=10,
                resources_consumed=5,
                value_produced=5.0,
                trigger_event="BASELINE",
            )

        records_a = {r.agent_id: r for r in repo.list_performance_records("tenant-14a", "org-14a")}
        assert records_a[researcher.id].composite_score == records_a[strategist.id].composite_score

        alloc_a = ResourceAllocator.allocate(
            org,
            strategy=AllocationStrategy.PERFORMANCE,
            mission_id="mission-a",
            performance_records=records_a,
        )
        assert alloc_a.total_allocated <= 100
        research_alloc_a = alloc_a.allocations[researcher.id]
        strategy_alloc_a = alloc_a.allocations[strategist.id]
        assert research_alloc_a == strategy_alloc_a

        provider = SimulatedOrbioProvider(initial_available="25.00")
        cfg = OrbioConfig(
            mode=ExternalProviderMode.SIMULATED,
            mcp_url="https://www.orbio.so/api/mcp",
            gateway_base="https://www.orbio.so/api/v1",
            api_key=None,
        )
        adapter = OrbioAdapter(config=cfg, simulated=provider)
        service = ExternalEconomyService(adapter)

        proposal = ActionProposal(
            id="prop-inf-a",
            task_id="task-inf-a",
            proposing_agent_id=researcher.id,
            action_type=ActionType.EXTERNAL_INFERENCE,
            target="orbio://inference",
            parameters={"model": "test/model"},
            requested_credits=min(10, research_alloc_a),
            expected_value_score=0.8,
            risk_assessment="low",
            rationale="Mission A external inference",
        )
        decision = engine.evaluate(proposal, org, ledger=ledger)
        assert decision.result == PolicyResult.APPROVED
        assert decision.authorization_token

        inf_receipt = service.run_authorized_inference(
            InferenceRequest(
                tenant_id="tenant-14a",
                organisation_id="org-14a",
                mission_id="mission-a",
                agent_id=researcher.id,
                model="test/model",
                messages=[{"role": "user", "content": "analyze"}],
                authorization_token=decision.authorization_token,
                idempotency_key="mission-a-inf-1",
            )
        )
        assert inf_receipt.outcome == ExternalProviderOutcome.SUCCESS

        # Independent Kalyx outcome: extreme success for researcher, extreme failure for strategist.
        # Provider telemetry is NOT used as the performance input.
        ReputationEngine.evaluate_agent_performance(
            agent=researcher,
            repo=repo,
            tenant_id="tenant-14a",
            organisation_id="org-14a",
            tasks_delta_completed=20,
            tasks_delta_failed=0,
            resources_allocated=research_alloc_a,
            resources_consumed=max(1, proposal.requested_credits),
            value_produced=200.0,
            trigger_event="MISSION_A_INDEPENDENT_OUTCOME",
        )
        ReputationEngine.evaluate_agent_performance(
            agent=strategist,
            repo=repo,
            tenant_id="tenant-14a",
            organisation_id="org-14a",
            tasks_delta_completed=0,
            tasks_delta_failed=20,
            resources_allocated=strategy_alloc_a,
            resources_consumed=strategy_alloc_a,
            value_produced=0.0,
            trigger_event="MISSION_A_STRATEGIST_STAGNANT",
        )

        records_b = {r.agent_id: r for r in repo.list_performance_records("tenant-14a", "org-14a")}
        assert records_b[researcher.id].composite_score > records_b[strategist.id].composite_score

        alloc_b = ResourceAllocator.allocate(
            org,
            strategy=AllocationStrategy.PERFORMANCE,
            mission_id="mission-b",
            performance_records=records_b,
        )
        research_alloc_b = alloc_b.allocations[researcher.id]
        strategy_alloc_b = alloc_b.allocations[strategist.id]

        # Causal proof: updated performance changes Mission B allocation
        assert research_alloc_b > strategy_alloc_b
        assert research_alloc_b > research_alloc_a
        assert strategy_alloc_b < strategy_alloc_a
        assert not hasattr(provider, "repo")
    finally:
        db.close()


def test_inference_success_with_task_failure_does_not_boost_performance():
    """Orbio SUCCESS + Kalyx task FAILURE must not improve efficiency/performance."""
    db = Database(":memory:")
    try:
        with db.conn:
            db.conn.execute(
                "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
                ("tenant-14a-f", "T", "active"),
            )
            db.conn.execute(
                "INSERT OR IGNORE INTO organisations (id, mission, treasury_balance, state, tenant_id, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                ("org-14a-f", "Fail path", 100, "EXECUTING", "tenant-14a-f"),
            )
        repo = EconomyRepository(db.conn)
        agent = AgentRecord(
            id="agent-r",
            role=AgentRole.RESEARCHER,
            authority_ceiling=25,
            performance_score=80.0,
            reputation_score=80.0,
            allowed_action_types=[ActionType.EXTERNAL_INFERENCE],
        )
        ReputationEngine.evaluate_agent_performance(
            agent=agent,
            repo=repo,
            tenant_id="tenant-14a-f",
            organisation_id="org-14a-f",
            tasks_delta_completed=1,
            tasks_delta_failed=0,
            resources_allocated=10,
            resources_consumed=5,
            value_produced=10.0,
            trigger_event="BASE",
        )
        before = repo.get_performance_record("tenant-14a-f", "org-14a-f", agent.id)
        assert before is not None

        provider = SimulatedOrbioProvider()
        service = ExternalEconomyService(
            OrbioAdapter(
                config=OrbioConfig(
                    mode=ExternalProviderMode.SIMULATED,
                    mcp_url="https://www.orbio.so/api/mcp",
                    gateway_base="https://www.orbio.so/api/v1",
                    api_key=None,
                ),
                simulated=provider,
            )
        )
        receipt = service.run_authorized_inference(
            InferenceRequest(
                tenant_id="tenant-14a-f",
                organisation_id="org-14a-f",
                mission_id="m",
                agent_id=agent.id,
                model="m",
                messages=[{"role": "user", "content": "x"}],
                authorization_token="AUTH-dummy",
                idempotency_key="fail-path-1",
            )
        )
        assert receipt.outcome == ExternalProviderOutcome.SUCCESS

        ReputationEngine.evaluate_agent_performance(
            agent=agent,
            repo=repo,
            tenant_id="tenant-14a-f",
            organisation_id="org-14a-f",
            tasks_delta_completed=0,
            tasks_delta_failed=1,
            resources_allocated=10,
            resources_consumed=8,
            value_produced=0.0,
            trigger_event="TASK_FAILED_AFTER_PROVIDER_SUCCESS",
        )
        after = repo.get_performance_record("tenant-14a-f", "org-14a-f", agent.id)
        assert after is not None
        assert after.tasks_failed >= before.tasks_failed + 1
        assert after.composite_score <= before.composite_score + 0.01
    finally:
        db.close()
