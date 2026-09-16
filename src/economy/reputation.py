"""Deterministic organisational economics, agent performance scoring, reputation tracking, and lifecycle transitions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.domain.economy import AgentPerformanceRecord, ReputationHistoryEntry
from src.domain.entities import AgentRecord
from src.domain.enums import ActionType, AgentStatus


class ReputationEngine:
    """Deterministic score updates, multi-factor metrics tracking, and
    lifecycle state transitions based on agent performance, compliance,
    and resource efficiency.
    """

    SUCCESS_BOOST: float = 3.0
    FAILURE_PENALTY: float = 5.0
    POLICY_VIOLATION_PENALTY: float = 10.0

    # Multi-factor weights for composite scoring (strictly sum to 1.0)
    WEIGHT_PERFORMANCE: float = 0.35
    WEIGHT_RELIABILITY: float = 0.25
    WEIGHT_EFFICIENCY: float = 0.20
    WEIGHT_COMPLIANCE: float = 0.20

    # For backward compatibility
    WEIGHT_SUCCESS: float = 0.35
    WEIGHT_COMPLIANCE_OLD: float = 0.25

    @classmethod
    def record_task_success(
        cls,
        agent: AgentRecord,
        credits_allocated: int = 0,
        credits_used: int = 0,
        value_score: float = 1.0,
        task_id: Optional[str] = None,
        perf_record: Optional[AgentPerformanceRecord] = None,
        event_store: Optional[Any] = None,
    ) -> None:
        """Record a successful task execution. Updates completion stats,
        reputation score, resource efficiency, reliability, and lifecycle.
        """
        agent.successful_tasks += 1
        if task_id:
            agent.task_history.append(f"SUCCESS:{task_id}")
        else:
            agent.task_history.append("SUCCESS")

        prev_rep = agent.reputation_score

        # Update resource efficiency
        if credits_allocated > 0 and credits_used >= 0:
            ratio = (credits_allocated / max(1, credits_used)) * max(0.1, value_score)
            bounded_ratio = min(2.0, max(0.0, ratio))
            agent.resource_efficiency = round(0.7 * agent.resource_efficiency + 0.3 * bounded_ratio, 2)

        # Reputation score increment
        agent.reputation_score = min(100.0, round(agent.reputation_score + cls.SUCCESS_BOOST, 2))

        # Update performance record if present
        if perf_record is not None:
            perf_record.tasks_completed += 1
            perf_record.resources_allocated += credits_allocated
            perf_record.resources_consumed += credits_used
            perf_record.value_produced += value_score
            perf_record.evaluation_count += 1
            perf_record.last_evaluated_at = datetime.utcnow()

        # Recalculate reliability and composite performance
        cls._update_metrics(agent, perf_record=perf_record)
        cls._evaluate_lifecycle(agent, perf_record=perf_record, event_store=event_store)

        if event_store is not None and hasattr(event_store, "append_event") and prev_rep != agent.reputation_score:
            event_store.append_event(
                actor_id="REPUTATION_ENGINE",
                event_type="REPUTATION_UPDATED",
                entity_id=agent.id,
                payload={
                    "agent_id": agent.id,
                    "previous_score": prev_rep,
                    "new_score": agent.reputation_score,
                    "delta": round(agent.reputation_score - prev_rep, 2),
                    "reason": f"TASK_SUCCESS:{task_id or 'anonymous'}",
                },
            )

    @classmethod
    def record_task_failure(
        cls,
        agent: AgentRecord,
        credits_allocated: int = 0,
        credits_used: int = 0,
        reason: Optional[str] = None,
        task_id: Optional[str] = None,
        perf_record: Optional[AgentPerformanceRecord] = None,
        event_store: Optional[Any] = None,
    ) -> None:
        """Record a failed task execution. Penalizes reputation, adjusts efficiency,
        recalculates reliability, and triggers lifecycle checks.
        """
        agent.failed_tasks += 1
        entry = f"FAILURE:{task_id or 'unknown'}"
        if reason:
            entry += f":{reason}"
        agent.task_history.append(entry)

        prev_rep = agent.reputation_score

        # Inefficient usage penalty on failure if credits were consumed
        if credits_used > 0:
            agent.resource_efficiency = round(max(0.1, agent.resource_efficiency * 0.85), 2)

        agent.reputation_score = max(0.0, round(agent.reputation_score - cls.FAILURE_PENALTY, 2))

        if perf_record is not None:
            perf_record.tasks_failed += 1
            perf_record.resources_allocated += credits_allocated
            perf_record.resources_consumed += credits_used
            perf_record.evaluation_count += 1
            perf_record.last_evaluated_at = datetime.utcnow()

        cls._update_metrics(agent, perf_record=perf_record)
        cls._evaluate_lifecycle(agent, perf_record=perf_record, event_store=event_store)

        if event_store is not None and hasattr(event_store, "append_event") and prev_rep != agent.reputation_score:
            event_store.append_event(
                actor_id="REPUTATION_ENGINE",
                event_type="REPUTATION_UPDATED",
                entity_id=agent.id,
                payload={
                    "agent_id": agent.id,
                    "previous_score": prev_rep,
                    "new_score": agent.reputation_score,
                    "delta": round(agent.reputation_score - prev_rep, 2),
                    "reason": f"TASK_FAILURE:{task_id or 'unknown'}:{reason or ''}",
                },
            )

    @classmethod
    def record_policy_violation(
        cls,
        agent: AgentRecord,
        details: Optional[str] = None,
        task_id: Optional[str] = None,
        perf_record: Optional[AgentPerformanceRecord] = None,
        event_store: Optional[Any] = None,
    ) -> None:
        """Record an unauthorized action or policy rule breach. Severely penalizes
        compliance, reputation score, and triggers potential probation/restriction.
        """
        agent.policy_violations += 1
        entry = f"VIOLATION:{task_id or 'unknown'}"
        if details:
            entry += f":{details}"
        agent.task_history.append(entry)

        prev_rep = agent.reputation_score
        agent.reputation_score = max(0.0, round(agent.reputation_score - cls.POLICY_VIOLATION_PENALTY, 2))
        agent.risk_score = min(100.0, round(agent.risk_score + 15.0, 2))

        if perf_record is not None:
            perf_record.policy_violations += 1
            perf_record.evaluation_count += 1
            perf_record.last_evaluated_at = datetime.utcnow()

        cls._update_metrics(agent, perf_record=perf_record)
        cls._evaluate_lifecycle(agent, perf_record=perf_record, event_store=event_store)

        if event_store is not None and hasattr(event_store, "append_event"):
            event_store.append_event(
                actor_id="REPUTATION_ENGINE",
                event_type="REPUTATION_UPDATED",
                entity_id=agent.id,
                payload={
                    "agent_id": agent.id,
                    "previous_score": prev_rep,
                    "new_score": agent.reputation_score,
                    "delta": round(agent.reputation_score - prev_rep, 2),
                    "reason": f"POLICY_VIOLATION:{details or ''}",
                },
            )

    @classmethod
    def record_proposal_outcome(
        cls,
        agent: AgentRecord,
        approved: bool,
        perf_record: Optional[AgentPerformanceRecord] = None,
    ) -> None:
        """Record whether an agent's proposal was approved or rejected by policy."""
        if perf_record is not None:
            if approved:
                perf_record.successful_proposals += 1
            else:
                perf_record.rejected_proposals += 1
            perf_record.last_evaluated_at = datetime.utcnow()
            cls._update_metrics(agent, perf_record=perf_record)

    @classmethod
    def record_execution_outcome(
        cls,
        agent: AgentRecord,
        succeeded: bool,
        recovered: bool = False,
        perf_record: Optional[AgentPerformanceRecord] = None,
    ) -> None:
        """Record the physical or simulated outcome of a consequential execution."""
        if perf_record is not None:
            if succeeded:
                perf_record.execution_successes += 1
                if recovered:
                    perf_record.recovery_successes += 1
            else:
                perf_record.execution_failures += 1
                if recovered:
                    perf_record.recovery_failures += 1
            perf_record.last_evaluated_at = datetime.utcnow()
            cls._update_metrics(agent, perf_record=perf_record)

    @classmethod
    def _update_metrics(
        cls, agent: AgentRecord, perf_record: Optional[AgentPerformanceRecord] = None
    ) -> None:
        """Recalculates individual inspectable sub-scores and deterministic composite_score.
        
        Formula:
        composite_score = (norm_perf * 0.35) + (norm_rel * 0.25) + (norm_eff * 0.20) + (norm_comp * 0.20)
        """
        total_tasks = agent.successful_tasks + agent.failed_tasks
        if total_tasks == 0:
            agent.reliability_score = 100.0
            agent.performance_score = 100.0
            if perf_record is not None:
                perf_record.performance_score = 100.0
                perf_record.reliability_score = 100.0
                perf_record.resource_efficiency_score = 1.0
                perf_record.policy_compliance_score = 100.0
                perf_record.composite_score = 100.0
                perf_record.reputation_score = round(max(0.0, min(100.0, agent.reputation_score)), 2)
            return

        # 1. Performance: task success rate with proposal acceptance factor
        task_success_rate = agent.successful_tasks / total_tasks
        perf_score = task_success_rate * 100.0

        # 2. Compliance: penalized 10 points per violation
        compliance_score = max(0.0, 100.0 - (10.0 * agent.policy_violations))

        # 3. Reliability: task success rate scaled by compliance and execution history
        rel_base = task_success_rate * (compliance_score / 100.0) * 100.0
        if perf_record is not None:
            tot_exec = perf_record.execution_successes + perf_record.execution_failures
            if tot_exec > 0:
                exec_rate = perf_record.execution_successes / tot_exec
                rel_base = (rel_base * 0.6) + (exec_rate * 40.0)
        reliability_score = round(max(0.0, min(100.0, rel_base)), 2)

        # 4. Resource efficiency
        eff_ratio = agent.resource_efficiency  # baseline 1.0
        if perf_record is not None and perf_record.resources_consumed > 0:
            eff_ratio = perf_record.value_produced / perf_record.resources_consumed
        norm_eff = min(100.0, max(0.0, eff_ratio * 100.0))

        # Composite score calculation
        composite = (
            (cls.WEIGHT_PERFORMANCE * perf_score)
            + (cls.WEIGHT_RELIABILITY * reliability_score)
            + (cls.WEIGHT_EFFICIENCY * norm_eff)
            + (cls.WEIGHT_COMPLIANCE * compliance_score)
        )
        composite = round(max(0.0, min(100.0, composite)), 2)

        agent.reliability_score = reliability_score
        agent.performance_score = composite

        if perf_record is not None:
            perf_record.performance_score = round(perf_score, 2)
            perf_record.reliability_score = reliability_score
            perf_record.resource_efficiency_score = round(eff_ratio, 2)
            perf_record.policy_compliance_score = round(compliance_score, 2)
            perf_record.composite_score = composite
            perf_record.reputation_score = round(max(0.0, min(100.0, agent.reputation_score)), 2)

    @classmethod
    def _consecutive_failures(cls, agent: AgentRecord) -> int:
        """Count how many consecutive tasks at the end of history resulted in failure."""
        count = 0
        for entry in reversed(agent.task_history):
            if entry.startswith("FAILURE") or entry.startswith("VIOLATION"):
                count += 1
            elif entry.startswith("SUCCESS"):
                break
        return count

    @classmethod
    def _consecutive_successes(cls, agent: AgentRecord) -> int:
        """Count how many consecutive tasks at the end of history resulted in success."""
        count = 0
        for entry in reversed(agent.task_history):
            if entry.startswith("SUCCESS"):
                count += 1
            elif entry.startswith("FAILURE") or entry.startswith("VIOLATION"):
                break
        return count

    @classmethod
    def _evaluate_lifecycle(
        cls,
        agent: AgentRecord,
        perf_record: Optional[AgentPerformanceRecord] = None,
        event_store: Optional[Any] = None,
    ) -> None:
        """Deterministic state transitions:
        ACTIVE <-> PROBATION <-> RESTRICTED <-> SUSPENDED -> RETIRED
        And promotion / demotion of authority ceilings.
        """
        # Terminal state check
        if agent.status == AgentStatus.RETIRED:
            agent.authority_ceiling = 0
            agent.allowed_action_types = []
            if perf_record is not None:
                perf_record.authority_level = 0
            return

        prev_status = agent.status
        prev_ceiling = agent.authority_ceiling

        score = agent.reputation_score
        composite = agent.performance_score
        consecutive_failures = cls._consecutive_failures(agent)
        consecutive_successes = cls._consecutive_successes(agent)

        # 1. Downward triggers in order of severity
        if score < 15.0 or composite < 15.0 or consecutive_failures >= 15:
            agent.status = AgentStatus.RETIRED
            agent.authority_ceiling = 0
            agent.allowed_action_types = []
        elif score < 30.0 or composite < 30.0 or consecutive_failures >= 10:
            agent.status = AgentStatus.SUSPENDED
            agent.authority_ceiling = 0
            agent.allowed_action_types = []
        elif score < 50.0 or composite < 50.0 or consecutive_failures >= 6:
            agent.status = AgentStatus.RESTRICTED
            agent.authority_ceiling = 0
            # Remove external and high-risk action types
            agent.allowed_action_types = [
                act for act in agent.allowed_action_types
                if act not in (ActionType.EXTERNAL_API_CALL, ActionType.SIMULATED_ALLOCATION, ActionType.BLOCKCHAIN_TRANSACTION)
            ]
        elif score < 75.0 or composite < 70.0 or consecutive_failures >= 3:
            agent.status = AgentStatus.PROBATION
            agent.authority_ceiling = min(agent.authority_ceiling, 12)
        else:
            agent.status = AgentStatus.ACTIVE
            agent.authority_ceiling = max(agent.authority_ceiling, 25)

        # 3. Promotion & Demotion mechanics based on sustained composite score
        if perf_record is not None:
            if agent.status == AgentStatus.ACTIVE and composite >= 90.0 and perf_record.evaluation_count >= 5:
                # Promotion: increase authority ceiling (up to 50 credits) and authority level (up to Level 5)
                perf_record.authority_level = min(5, perf_record.authority_level + 1)
                agent.authority_ceiling = min(50, 25 + (perf_record.authority_level * 5))
            elif composite < 60.0 and agent.status != AgentStatus.RETIRED:
                # Demotion: reduce authority level and ceiling
                perf_record.authority_level = max(1, perf_record.authority_level - 1)
                agent.authority_ceiling = max(5, agent.authority_ceiling - 5)

        # Audit lifecycle mutations
        if event_store is not None and hasattr(event_store, "append_event"):
            if agent.status != prev_status:
                event_type = f"AGENT_{agent.status.value.upper()}"
                event_store.append_event(
                    actor_id="REPUTATION_ENGINE",
                    event_type=event_type,
                    entity_id=agent.id,
                    payload={
                        "agent_id": agent.id,
                        "previous_status": prev_status.value,
                        "new_status": agent.status.value,
                        "reputation_score": agent.reputation_score,
                        "composite_score": composite,
                        "consecutive_failures": consecutive_failures,
                    },
                )
            if agent.authority_ceiling != prev_ceiling:
                is_promo = agent.authority_ceiling > prev_ceiling
                event_store.append_event(
                    actor_id="REPUTATION_ENGINE",
                    event_type="AGENT_PROMOTED" if is_promo else "AGENT_DEMOTED",
                    entity_id=agent.id,
                    payload={
                        "agent_id": agent.id,
                        "previous_ceiling": prev_ceiling,
                        "new_ceiling": agent.authority_ceiling,
                        "composite_score": composite,
                    },
                )

    @classmethod
    def can_assign_task(cls, agent: AgentRecord) -> bool:
        """Determines if an agent is eligible to be assigned any new tasks."""
        return agent.status in (AgentStatus.ACTIVE, AgentStatus.PROBATION, AgentStatus.RESTRICTED)

    @classmethod
    def can_propose_action(
        cls,
        agent: AgentRecord,
        action_type: ActionType,
        requested_credits: int,
    ) -> Tuple[bool, str]:
        """Validates whether an agent's current lifecycle state and authority
        ceiling permit proposing a specific action and budget.
        """
        if agent.status == AgentStatus.RETIRED:
            return False, "Agent is RETIRED and permanently disabled from proposing actions."
        if agent.status == AgentStatus.SUSPENDED:
            return False, "Agent is SUSPENDED and cannot propose actions."
        if agent.status == AgentStatus.RESTRICTED:
            if action_type in (
                ActionType.EXTERNAL_API_CALL,
                ActionType.SIMULATED_ALLOCATION,
                ActionType.BLOCKCHAIN_TRANSACTION,
            ):
                return False, f"Agent is RESTRICTED: disallowed from proposing external action {action_type}."
            if requested_credits > 5:
                return False, f"Agent is RESTRICTED: requested credits {requested_credits} exceed restricted limit of 5."

        if requested_credits > agent.authority_ceiling:
            return False, f"Requested credits ({requested_credits}) exceed agent authority ceiling ({agent.authority_ceiling})."

        return True, "Action proposal permitted by agent lifecycle and authority policy."

    @classmethod
    def evaluate_agent_performance(
        cls,
        agent: AgentRecord,
        repo: Any,
        tenant_id: str,
        organisation_id: str,
        tasks_delta_completed: int = 0,
        tasks_delta_failed: int = 0,
        proposals_delta_approved: int = 0,
        proposals_delta_rejected: int = 0,
        resources_allocated: int = 0,
        resources_consumed: int = 0,
        value_produced: float = 0.0,
        policy_violations_delta: int = 0,
        trigger_event: str = "MANUAL_EVALUATION",
        evidence_hash: Optional[str] = None,
    ) -> ReputationHistoryEntry:
        """Evaluate agent performance against EconomyRepository, persisting records and audit delta."""
        perf_rec = repo.get_performance_record(tenant_id, organisation_id, agent.id)
        if not perf_rec:
            perf_rec = AgentPerformanceRecord(
                agent_id=agent.id,
                organisation_id=organisation_id,
                tenant_id=tenant_id,
            )

        perf_rec.tasks_completed += tasks_delta_completed
        perf_rec.tasks_failed += tasks_delta_failed
        perf_rec.successful_proposals += proposals_delta_approved
        perf_rec.rejected_proposals += proposals_delta_rejected
        perf_rec.resources_allocated += resources_allocated
        perf_rec.resources_consumed += resources_consumed
        perf_rec.value_produced += value_produced
        perf_rec.policy_violations += policy_violations_delta
        perf_rec.evaluation_count += 1
        perf_rec.last_evaluated_at = datetime.utcnow()
        perf_rec.compute_scores()

        prev_score = agent.reputation_score
        agent.successful_tasks = perf_rec.tasks_completed
        agent.failed_tasks = perf_rec.tasks_failed
        agent.policy_violations = perf_rec.policy_violations
        cls._update_metrics(agent, perf_record=perf_rec)
        cls._evaluate_lifecycle(agent, perf_record=perf_rec)

        repo.save_performance_record(perf_rec)

        delta = round(agent.reputation_score - prev_score, 2)
        ev_hash = evidence_hash or perf_rec.compute_record_hash()

        entry = ReputationHistoryEntry(
            id=f"rep-{uuid.uuid4().hex[:12]}",
            agent_id=agent.id,
            organisation_id=organisation_id,
            tenant_id=tenant_id,
            previous_score=prev_score,
            new_score=agent.reputation_score,
            score_delta=delta,
            trigger_event=trigger_event,
            evidence_hash=ev_hash,
            created_at=datetime.utcnow(),
        )
        repo.record_reputation_event(entry)
        return entry
