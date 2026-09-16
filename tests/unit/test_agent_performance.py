import sqlite3
import pytest
from datetime import datetime

from src.domain.economy import AgentPerformanceRecord
from src.domain.entities import AgentRecord
from src.domain.enums import AgentRole, AgentStatus
from src.economy.reputation import ReputationEngine
from src.persistence.economy_repo import EconomyRepository


def create_in_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE agent_performance_records (
            agent_id TEXT NOT NULL,
            organisation_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            tasks_completed INTEGER NOT NULL DEFAULT 0,
            tasks_failed INTEGER NOT NULL DEFAULT 0,
            missions_contributed INTEGER NOT NULL DEFAULT 0,
            successful_proposals INTEGER NOT NULL DEFAULT 0,
            rejected_proposals INTEGER NOT NULL DEFAULT 0,
            policy_violations INTEGER NOT NULL DEFAULT 0,
            resources_allocated INTEGER NOT NULL DEFAULT 0,
            resources_consumed INTEGER NOT NULL DEFAULT 0,
            value_produced INTEGER NOT NULL DEFAULT 0,
            unnecessary_actions INTEGER NOT NULL DEFAULT 0,
            execution_successes INTEGER NOT NULL DEFAULT 0,
            execution_failures INTEGER NOT NULL DEFAULT 0,
            recovery_successes INTEGER NOT NULL DEFAULT 0,
            recovery_failures INTEGER NOT NULL DEFAULT 0,
            performance_score REAL NOT NULL DEFAULT 100.0,
            reliability_score REAL NOT NULL DEFAULT 100.0,
            resource_efficiency_score REAL NOT NULL DEFAULT 100.0,
            policy_compliance_score REAL NOT NULL DEFAULT 100.0,
            composite_score REAL NOT NULL DEFAULT 100.0,
            reputation_score REAL NOT NULL DEFAULT 100.0,
            authority_level INTEGER NOT NULL DEFAULT 25,
            evaluation_count INTEGER NOT NULL DEFAULT 0,
            last_evaluated_at TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, organisation_id, agent_id)
        )
    """)
    conn.execute("""
        CREATE TABLE reputation_history (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            organisation_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            previous_score REAL NOT NULL,
            new_score REAL NOT NULL,
            score_delta REAL NOT NULL,
            trigger_event TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    return conn


def test_agent_performance_record_score_computation():
    rec = AgentPerformanceRecord(
        agent_id="agent-001",
        organisation_id="org-test",
        tenant_id="tenant-test",
        tasks_completed=8,
        tasks_failed=2,
        successful_proposals=4,
        rejected_proposals=1,
        resources_allocated=100,
        resources_consumed=80,
        value_produced=120,
        policy_violations=0,
    )
    rec.compute_scores()

    assert rec.performance_score == 80.0
    assert rec.reliability_score == 80.0
    assert rec.resource_efficiency_score == 100.0
    assert rec.policy_compliance_score == 100.0
    # Composite: 80*0.35 + 80*0.25 + 100*0.20 + 100*0.20 = 28 + 20 + 20 + 20 = 88.0
    assert rec.composite_score == 88.0


def test_agent_performance_policy_violation_penalty():
    rec = AgentPerformanceRecord(
        agent_id="agent-002",
        organisation_id="org-test",
        tenant_id="tenant-test",
        tasks_completed=10,
        tasks_failed=0,
        successful_proposals=10,
        rejected_proposals=0,
        resources_allocated=100,
        resources_consumed=100,
        value_produced=100,
        policy_violations=2,
    )
    rec.compute_scores()
    assert rec.policy_compliance_score == 50.0
    # Composite: 100*0.35 + 100*0.25 + 100*0.20 + 50*0.20 = 35 + 25 + 20 + 10 = 90.0
    assert rec.composite_score == 90.0


def test_agent_performance_record_hash_determinism():
    rec1 = AgentPerformanceRecord(
        agent_id="agent-001",
        organisation_id="org-test",
        tenant_id="tenant-test",
        tasks_completed=5,
        tasks_failed=1,
        resources_allocated=50,
        resources_consumed=40,
        value_produced=60,
    )
    rec2 = AgentPerformanceRecord(
        agent_id="agent-001",
        organisation_id="org-test",
        tenant_id="tenant-test",
        tasks_completed=5,
        tasks_failed=1,
        resources_allocated=50,
        resources_consumed=40,
        value_produced=60,
    )
    h1 = rec1.compute_record_hash()
    h2 = rec2.compute_record_hash()
    assert h1 == h2
    assert len(h1) == 64

    rec2.tasks_failed = 2
    assert rec1.compute_record_hash() != rec2.compute_record_hash()


def test_reputation_engine_evaluate_agent_performance():
    conn = create_in_memory_db()
    repo = EconomyRepository(conn)

    agent = AgentRecord(
        id="agent-eval-1",
        role=AgentRole.RESEARCHER,
        authority_ceiling=25,
        reputation_score=100.0,
        status=AgentStatus.ACTIVE,
    )

    entry = ReputationEngine.evaluate_agent_performance(
        agent=agent,
        repo=repo,
        tenant_id="tenant-test",
        organisation_id="org-test",
        tasks_delta_completed=5,
        tasks_delta_failed=0,
        proposals_delta_approved=3,
        proposals_delta_rejected=0,
        resources_allocated=50,
        resources_consumed=40,
        value_produced=60,
        policy_violations_delta=0,
        trigger_event="MISSION_COMPLETION",
    )

    assert entry is not None
    assert entry.agent_id == "agent-eval-1"
    assert entry.tenant_id == "tenant-test"
    assert entry.trigger_event == "MISSION_COMPLETION"

    persisted = repo.get_performance_record("tenant-test", "org-test", "agent-eval-1")
    assert persisted is not None
    assert persisted.tasks_completed == 5
    assert persisted.tasks_failed == 0
    assert persisted.evaluation_count == 1
    assert persisted.composite_score == 100.0

    history = repo.list_reputation_history("tenant-test", "org-test", "agent-eval-1")
    assert len(history) == 1
    assert history[0].evidence_hash != ""
