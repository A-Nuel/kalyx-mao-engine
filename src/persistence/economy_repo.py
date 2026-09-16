"""Durable persistence repository for agent performance, reputation history, and economic allocations."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.domain.economy import (
    AgentPerformanceRecord,
    AllocationStrategy,
    ReputationHistoryEntry,
    ResourceAllocationDecision,
)


class EconomyRepository:
    """Multi-tenant, organisation-scoped persistence for economic state."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def get_performance_record(
        self, tenant_id: str, org_id: str, agent_id: str
    ) -> Optional[AgentPerformanceRecord]:
        """Fetch an agent's durable performance record scoped strictly by tenant and organisation."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """SELECT * FROM agent_performance_records
               WHERE tenant_id = ? AND organisation_id = ? AND agent_id = ?""",
            (tenant_id, org_id, agent_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_perf_record(row)

    def list_performance_records(
        self, tenant_id: str, org_id: str
    ) -> List[AgentPerformanceRecord]:
        """List all agent performance records for an organisation."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        rows = cursor.execute(
            """SELECT * FROM agent_performance_records
               WHERE tenant_id = ? AND organisation_id = ?
               ORDER BY composite_score DESC""",
            (tenant_id, org_id),
        ).fetchall()
        return [self._row_to_perf_record(r) for r in rows]

    def save_performance_record(self, record: AgentPerformanceRecord) -> None:
        """Upsert an agent's performance record atomically."""
        last_eval = record.last_evaluated_at.isoformat() if record.last_evaluated_at else None
        created = record.created_at.isoformat() if record.created_at else datetime.utcnow().isoformat()
        with self.conn:
            self.conn.execute(
                """INSERT INTO agent_performance_records (
                    agent_id, organisation_id, tenant_id,
                    tasks_completed, tasks_failed, missions_contributed,
                    successful_proposals, rejected_proposals, policy_violations,
                    resources_allocated, resources_consumed, value_produced, unnecessary_actions,
                    execution_successes, execution_failures, recovery_successes, recovery_failures,
                    performance_score, reliability_score, resource_efficiency_score,
                    policy_compliance_score, composite_score, reputation_score,
                    authority_level, evaluation_count, last_evaluated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, organisation_id, agent_id) DO UPDATE SET
                    tasks_completed = excluded.tasks_completed,
                    tasks_failed = excluded.tasks_failed,
                    missions_contributed = excluded.missions_contributed,
                    successful_proposals = excluded.successful_proposals,
                    rejected_proposals = excluded.rejected_proposals,
                    policy_violations = excluded.policy_violations,
                    resources_allocated = excluded.resources_allocated,
                    resources_consumed = excluded.resources_consumed,
                    value_produced = excluded.value_produced,
                    unnecessary_actions = excluded.unnecessary_actions,
                    execution_successes = excluded.execution_successes,
                    execution_failures = excluded.execution_failures,
                    recovery_successes = excluded.recovery_successes,
                    recovery_failures = excluded.recovery_failures,
                    performance_score = excluded.performance_score,
                    reliability_score = excluded.reliability_score,
                    resource_efficiency_score = excluded.resource_efficiency_score,
                    policy_compliance_score = excluded.policy_compliance_score,
                    composite_score = excluded.composite_score,
                    reputation_score = excluded.reputation_score,
                    authority_level = excluded.authority_level,
                    evaluation_count = excluded.evaluation_count,
                    last_evaluated_at = excluded.last_evaluated_at""",
                (
                    record.agent_id,
                    record.organisation_id,
                    record.tenant_id,
                    record.tasks_completed,
                    record.tasks_failed,
                    record.missions_contributed,
                    record.successful_proposals,
                    record.rejected_proposals,
                    record.policy_violations,
                    record.resources_allocated,
                    record.resources_consumed,
                    record.value_produced,
                    record.unnecessary_actions,
                    record.execution_successes,
                    record.execution_failures,
                    record.recovery_successes,
                    record.recovery_failures,
                    record.performance_score,
                    record.reliability_score,
                    record.resource_efficiency_score,
                    record.policy_compliance_score,
                    record.composite_score,
                    record.reputation_score,
                    record.authority_level,
                    record.evaluation_count,
                    last_eval,
                    created,
                ),
            )

    def record_reputation_event(self, entry: ReputationHistoryEntry) -> None:
        """Append an immutable reputation evaluation event."""
        created = entry.created_at.isoformat() if entry.created_at else datetime.utcnow().isoformat()
        with self.conn:
            self.conn.execute(
                """INSERT INTO reputation_history (
                    id, agent_id, organisation_id, tenant_id,
                    previous_score, new_score, score_delta,
                    trigger_event, evidence_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id,
                    entry.agent_id,
                    entry.organisation_id,
                    entry.tenant_id,
                    entry.previous_score,
                    entry.new_score,
                    entry.score_delta,
                    entry.trigger_event,
                    entry.evidence_hash,
                    created,
                ),
            )

    def list_reputation_history(
        self, tenant_id: str, org_id: str, agent_id: str, limit: int = 50
    ) -> List[ReputationHistoryEntry]:
        """Fetch the reputation change history for an agent."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        rows = cursor.execute(
            """SELECT * FROM reputation_history
               WHERE tenant_id = ? AND organisation_id = ? AND agent_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (tenant_id, org_id, agent_id, limit),
        ).fetchall()
        return [
            ReputationHistoryEntry(
                id=r["id"],
                agent_id=r["agent_id"],
                organisation_id=r["organisation_id"],
                tenant_id=r["tenant_id"],
                previous_score=r["previous_score"],
                new_score=r["new_score"],
                score_delta=r["score_delta"],
                trigger_event=r["trigger_event"],
                evidence_hash=r["evidence_hash"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def save_allocation(self, decision: ResourceAllocationDecision) -> None:
        """Persist a resource allocation decision record."""
        created = decision.created_at.isoformat() if decision.created_at else datetime.utcnow().isoformat()
        with self.conn:
            self.conn.execute(
                """INSERT INTO resource_allocations (
                    id, tenant_id, organisation_id, mission_id,
                    strategy, treasury_available, total_allocated,
                    allocations, authority_limits, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.id,
                    decision.tenant_id,
                    decision.organisation_id,
                    decision.mission_id,
                    decision.strategy.value,
                    decision.treasury_available,
                    decision.total_allocated,
                    json.dumps(decision.allocations),
                    json.dumps(decision.authority_limits),
                    decision.rationale,
                    created,
                ),
            )

    def list_allocations(
        self, tenant_id: str, org_id: str, limit: int = 50
    ) -> List[ResourceAllocationDecision]:
        """Retrieve recent resource allocation decisions."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        rows = cursor.execute(
            """SELECT * FROM resource_allocations
               WHERE tenant_id = ? AND organisation_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (tenant_id, org_id, limit),
        ).fetchall()
        return [
            ResourceAllocationDecision(
                id=r["id"],
                tenant_id=r["tenant_id"],
                organisation_id=r["organisation_id"],
                mission_id=r["mission_id"],
                strategy=AllocationStrategy(r["strategy"]),
                treasury_available=r["treasury_available"],
                total_allocated=r["total_allocated"],
                allocations=json.loads(r["allocations"]) if isinstance(r["allocations"], str) else r["allocations"],
                authority_limits=json.loads(r["authority_limits"]) if isinstance(r["authority_limits"], str) else r["authority_limits"],
                rationale=r["rationale"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def save_experiment_run(
        self,
        experiment_id: str,
        tenant_id: str,
        org_id: str,
        scenario: str,
        strategy: str,
        random_seed: int,
        results: Dict[str, Any],
        summary: str,
    ) -> None:
        """Persist an empirical experiment run."""
        with self.conn:
            self.conn.execute(
                """INSERT INTO experiment_runs (
                    id, tenant_id, organisation_id, scenario,
                    strategy, random_seed, results_json, summary_analysis, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    experiment_id,
                    tenant_id,
                    org_id,
                    scenario,
                    strategy,
                    random_seed,
                    json.dumps(results),
                    summary,
                ),
            )

    def list_experiment_runs(
        self, tenant_id: str, org_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Retrieve recent experiment runs for an organisation."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        rows = cursor.execute(
            """SELECT * FROM experiment_runs
               WHERE tenant_id = ? AND organisation_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (tenant_id, org_id, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "tenant_id": r["tenant_id"],
                "organisation_id": r["organisation_id"],
                "scenario": r["scenario"],
                "strategy": r["strategy"],
                "random_seed": r["random_seed"],
                "results": json.loads(r["results_json"]) if isinstance(r["results_json"], str) else r["results_json"],
                "summary": r["summary_analysis"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def _row_to_perf_record(self, row: Any) -> AgentPerformanceRecord:
        last_eval = datetime.fromisoformat(row["last_evaluated_at"]) if row["last_evaluated_at"] else None
        created = datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.utcnow()
        return AgentPerformanceRecord(
            agent_id=row["agent_id"],
            organisation_id=row["organisation_id"],
            tenant_id=row["tenant_id"],
            tasks_completed=row["tasks_completed"],
            tasks_failed=row["tasks_failed"],
            missions_contributed=row["missions_contributed"],
            successful_proposals=row["successful_proposals"],
            rejected_proposals=row["rejected_proposals"],
            policy_violations=row["policy_violations"],
            resources_allocated=row["resources_allocated"],
            resources_consumed=row["resources_consumed"],
            value_produced=row["value_produced"],
            unnecessary_actions=row["unnecessary_actions"],
            execution_successes=row["execution_successes"],
            execution_failures=row["execution_failures"],
            recovery_successes=row["recovery_successes"],
            recovery_failures=row["recovery_failures"],
            performance_score=row["performance_score"],
            reliability_score=row["reliability_score"],
            resource_efficiency_score=row["resource_efficiency_score"],
            policy_compliance_score=row["policy_compliance_score"],
            composite_score=row["composite_score"],
            reputation_score=row["reputation_score"],
            authority_level=row["authority_level"],
            evaluation_count=row["evaluation_count"],
            last_evaluated_at=last_eval,
            created_at=created,
        )
