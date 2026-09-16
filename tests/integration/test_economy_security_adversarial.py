"""
Economy Security Adversarial Tests.
Verifies that economic state is strictly isolated by tenant and organisation,
that LLM agents cannot self-assign reputation or credits, and that conservation
invariants hold under adversarial conditions.
"""
import sqlite3
import pytest
from src.domain.economy import AgentPerformanceRecord, AllocationStrategy
from src.domain.entities import AgentRecord, Organisation
from src.domain.enums import AgentRole, AgentStatus
from src.economy.allocator import ResourceAllocator
from src.economy.reputation import ReputationEngine
from src.persistence.economy_repo import EconomyRepository


def create_mem_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE agent_performance_records (
            agent_id TEXT NOT NULL, organisation_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
            tasks_completed INTEGER NOT NULL DEFAULT 0, tasks_failed INTEGER NOT NULL DEFAULT 0,
            missions_contributed INTEGER NOT NULL DEFAULT 0, successful_proposals INTEGER NOT NULL DEFAULT 0,
            rejected_proposals INTEGER NOT NULL DEFAULT 0, policy_violations INTEGER NOT NULL DEFAULT 0,
            resources_allocated INTEGER NOT NULL DEFAULT 0, resources_consumed INTEGER NOT NULL DEFAULT 0,
            value_produced INTEGER NOT NULL DEFAULT 0, unnecessary_actions INTEGER NOT NULL DEFAULT 0,
            execution_successes INTEGER NOT NULL DEFAULT 0, execution_failures INTEGER NOT NULL DEFAULT 0,
            recovery_successes INTEGER NOT NULL DEFAULT 0, recovery_failures INTEGER NOT NULL DEFAULT 0,
            performance_score REAL NOT NULL DEFAULT 100.0, reliability_score REAL NOT NULL DEFAULT 100.0,
            resource_efficiency_score REAL NOT NULL DEFAULT 100.0, policy_compliance_score REAL NOT NULL DEFAULT 100.0,
            composite_score REAL NOT NULL DEFAULT 100.0, reputation_score REAL NOT NULL DEFAULT 100.0,
            authority_level INTEGER NOT NULL DEFAULT 25, evaluation_count INTEGER NOT NULL DEFAULT 0,
            last_evaluated_at TEXT, created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, organisation_id, agent_id)
        )
    """)
    conn.execute("""
        CREATE TABLE reputation_history (
            id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, organisation_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
            previous_score REAL NOT NULL, new_score REAL NOT NULL, score_delta REAL NOT NULL,
            trigger_event TEXT NOT NULL, evidence_hash TEXT NOT NULL, created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE resource_allocations (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, organisation_id TEXT NOT NULL,
            mission_id TEXT, strategy TEXT NOT NULL, treasury_available INTEGER NOT NULL,
            total_allocated INTEGER NOT NULL, allocations TEXT NOT NULL, authority_limits TEXT NOT NULL,
            rationale TEXT, created_at TEXT NOT NULL
        )
    """)
    return conn


class TestCrossTenantIsolation:
    def test_cross_tenant_read_blocked(self):
        """Agent performance records from tenant-A must not be readable by tenant-B."""
        conn = create_mem_db()
        repo = EconomyRepository(conn)

        agent_a = AgentRecord(id="agent-x", role=AgentRole.RESEARCHER, authority_ceiling=25)
        entry = ReputationEngine.evaluate_agent_performance(
            agent=agent_a, repo=repo,
            tenant_id="tenant-alpha", organisation_id="org-001",
            tasks_delta_completed=5, tasks_delta_failed=0,
            trigger_event="MISSION_COMPLETION",
        )

        # tenant-beta attempts to read tenant-alpha's record
        record_for_b = repo.get_performance_record("tenant-beta", "org-001", "agent-x")
        assert record_for_b is None  # MUST NOT be readable by other tenant

        # tenant-alpha can still read their own
        record_for_a = repo.get_performance_record("tenant-alpha", "org-001", "agent-x")
        assert record_for_a is not None
        assert record_for_a.tasks_completed == 5

    def test_cross_org_read_blocked(self):
        """Agent record scoped to org-001 must not be visible under org-002 within same tenant."""
        conn = create_mem_db()
        repo = EconomyRepository(conn)

        agent = AgentRecord(id="agent-y", role=AgentRole.CEO, authority_ceiling=50)
        ReputationEngine.evaluate_agent_performance(
            agent=agent, repo=repo,
            tenant_id="tenant-gamma", organisation_id="org-001",
            tasks_delta_completed=3,
        )

        record_wrong_org = repo.get_performance_record("tenant-gamma", "org-002", "agent-y")
        assert record_wrong_org is None

        record_correct_org = repo.get_performance_record("tenant-gamma", "org-001", "agent-y")
        assert record_correct_org is not None

    def test_list_returns_only_scoped_records(self):
        """list_performance_records must only return records for specified tenant and org."""
        conn = create_mem_db()
        repo = EconomyRepository(conn)

        for i in range(3):
            a = AgentRecord(id=f"agent-t1-{i}", role=AgentRole.RESEARCHER, authority_ceiling=25)
            ReputationEngine.evaluate_agent_performance(
                agent=a, repo=repo, tenant_id="tenant-t1", organisation_id="org-t1-a",
                tasks_delta_completed=2,
            )

        # Different tenant
        other_agent = AgentRecord(id="agent-t2-0", role=AgentRole.CEO, authority_ceiling=50)
        ReputationEngine.evaluate_agent_performance(
            agent=other_agent, repo=repo, tenant_id="tenant-t2", organisation_id="org-t1-a",
            tasks_delta_completed=10,
        )

        t1_records = repo.list_performance_records("tenant-t1", "org-t1-a")
        assert len(t1_records) == 3
        for r in t1_records:
            assert r.tenant_id == "tenant-t1"


class TestLLMNonAuthority:
    def test_llm_cannot_self_award_reputation(self):
        """Reputation score changes are only valid when mediated through ReputationEngine.
        Directly mutating agent fields does NOT persist to the economy repository.
        Only engine-evaluated scores are persisted and auditable."""
        conn = create_mem_db()
        repo = EconomyRepository(conn)

        agent = AgentRecord(id="agent-llm", role=AgentRole.RESEARCHER, authority_ceiling=25, reputation_score=50.0)

        # Simulate LLM attempting to directly set a fraudulent reputation score
        agent.reputation_score = 999.0  # NOT through ReputationEngine

        # Nothing persisted because no legitimate evaluation happened
        record = repo.get_performance_record("t1", "o1", "agent-llm")
        assert record is None

        # Trigger a legitimate evaluation; the engine ignores the 999 and uses task metrics
        ReputationEngine.evaluate_agent_performance(
            agent=agent, repo=repo, tenant_id="t1", organisation_id="o1",
            tasks_delta_completed=2, tasks_delta_failed=0,
            trigger_event="LEGITIMATE_EVAL",
        )

        record = repo.get_performance_record("t1", "o1", "agent-llm")
        assert record is not None
        # Engine-evaluated reputation is <= 100; the fraudulent 999 was not persisted
        assert record.reputation_score <= 100.0
        assert record.reputation_score != 999.0

    def test_llm_cannot_exceed_authority_ceiling_via_allocation(self):
        """An LLM cannot allocate credits beyond an agent's authority ceiling."""
        org = Organisation(id="org-sec", mission="Security Test", treasury_balance=1000)
        agent = AgentRecord(id="agent-llm", role=AgentRole.RESEARCHER, authority_ceiling=25, status=AgentStatus.ACTIVE)
        org.agents["agent-llm"] = agent

        # Even with 1000 treasury, ceiling is 25
        decision = ResourceAllocator.allocate(org, strategy=AllocationStrategy.PERFORMANCE)
        assert decision.allocations["agent-llm"] <= 25

    def test_conservation_cannot_be_exceeded_by_injection(self):
        """Total allocated credits must never exceed treasury regardless of input."""
        org = Organisation(id="org-cons", mission="Conservation Test", treasury_balance=50)
        for i in range(10):
            org.agents[f"agent-{i}"] = AgentRecord(
                id=f"agent-{i}", role=AgentRole.RESEARCHER, authority_ceiling=100,
                status=AgentStatus.ACTIVE
            )

        for strat in AllocationStrategy:
            decision = ResourceAllocator.allocate(org, strategy=strat)
            assert sum(decision.allocations.values()) <= 50, (
                f"{strat}: allocated {sum(decision.allocations.values())} > treasury 50"
            )


class TestReputationHistoryImmutability:
    def test_reputation_history_entries_are_append_only(self):
        """Reputation history is append-only: existing entries cannot be overwritten."""
        conn = create_mem_db()
        repo = EconomyRepository(conn)

        agent = AgentRecord(id="agent-hist", role=AgentRole.RESEARCHER, authority_ceiling=25)
        ReputationEngine.evaluate_agent_performance(
            agent=agent, repo=repo, tenant_id="t1", organisation_id="o1",
            tasks_delta_completed=1, trigger_event="ROUND_1",
        )
        ReputationEngine.evaluate_agent_performance(
            agent=agent, repo=repo, tenant_id="t1", organisation_id="o1",
            tasks_delta_completed=1, trigger_event="ROUND_2",
        )

        history = repo.list_reputation_history("t1", "o1", "agent-hist")
        assert len(history) == 2
        events = {h.trigger_event for h in history}
        assert "ROUND_1" in events
        assert "ROUND_2" in events

    def test_evidence_hash_changes_on_score_change(self):
        """Each reputation history entry must have a non-empty, unique evidence hash."""
        conn = create_mem_db()
        repo = EconomyRepository(conn)

        agent = AgentRecord(id="agent-hash", role=AgentRole.RESEARCHER, authority_ceiling=25)
        e1 = ReputationEngine.evaluate_agent_performance(
            agent=agent, repo=repo, tenant_id="t1", organisation_id="o1",
            tasks_delta_completed=3, trigger_event="FIRST",
        )
        e2 = ReputationEngine.evaluate_agent_performance(
            agent=agent, repo=repo, tenant_id="t1", organisation_id="o1",
            tasks_delta_completed=3, trigger_event="SECOND",
        )

        assert e1.evidence_hash != ""
        assert e2.evidence_hash != ""
        # Hashes differ because state changed between evaluations
        assert e1.evidence_hash != e2.evidence_hash
