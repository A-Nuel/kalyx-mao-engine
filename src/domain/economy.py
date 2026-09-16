"""Domain entities for Phase 13: Adaptive Organisational Economics & Agent Performance."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.domain.events import canonical_json


class AllocationStrategy(str, Enum):
    """Deterministic resource allocation strategies."""
    STATIC = "STATIC"
    PERFORMANCE = "PERFORMANCE"
    ADAPTIVE = "ADAPTIVE"


class AgentPerformanceRecord(BaseModel):
    """Durable multi-dimensional performance and economic record for an agent.
    
    Distinguishes credits/resources, task performance, execution reliability,
    resource efficiency, policy compliance, reputation, authority, and lifecycle.
    """
    agent_id: str
    organisation_id: str
    tenant_id: str = "tenant-demo"

    # Task & Mission counters
    tasks_completed: int = 0
    tasks_failed: int = 0
    missions_contributed: int = 0

    # Proposal & Governance counters
    successful_proposals: int = 0
    rejected_proposals: int = 0
    policy_violations: int = 0

    # Economic & Resource metrics
    resources_allocated: int = 0
    resources_consumed: int = 0
    value_produced: float = 0.0
    unnecessary_actions: int = 0

    # Consequential Execution & Recovery metrics
    execution_successes: int = 0
    execution_failures: int = 0
    recovery_successes: int = 0
    recovery_failures: int = 0

    # Explicit, inspectable sub-scores (0.0 to 100.0 or ratio)
    performance_score: float = 100.0
    reliability_score: float = 100.0
    resource_efficiency_score: float = 1.0  # value delivered per credit consumed (normalized baseline 1.0)
    policy_compliance_score: float = 100.0
    composite_score: float = 100.0
    reputation_score: float = 100.0

    # Execution Authority
    authority_level: int = 1  # Level 1..5 corresponding to action permissions and budget limits

    # Evaluation metadata
    evaluation_count: int = 0
    last_evaluated_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def compute_scores(self) -> None:
        """Compute inspectable sub-scores and composite score deterministically."""
        total_tasks = self.tasks_completed + self.tasks_failed
        if total_tasks > 0:
            self.performance_score = round((self.tasks_completed / total_tasks) * 100.0, 2)
        else:
            self.performance_score = 100.0

        total_proposals = self.successful_proposals + self.rejected_proposals
        if total_proposals > 0:
            self.reliability_score = round((self.successful_proposals / total_proposals) * 100.0, 2)
        else:
            self.reliability_score = 100.0

        if self.resources_consumed > 0:
            eff_ratio = self.value_produced / self.resources_consumed
            self.resource_efficiency_score = round(min(100.0, max(0.0, eff_ratio * 100.0)), 2)
        else:
            self.resource_efficiency_score = 100.0

        self.policy_compliance_score = round(max(0.0, 100.0 - (self.policy_violations * 25.0)), 2)

        self.composite_score = round(
            (self.performance_score * 0.35)
            + (self.reliability_score * 0.25)
            + (self.resource_efficiency_score * 0.20)
            + (self.policy_compliance_score * 0.20),
            2,
        )

    def compute_record_hash(self) -> str:
        """Deterministic fingerprint of the agent performance state."""
        data = {
            "agent_id": self.agent_id,
            "organisation_id": self.organisation_id,
            "tenant_id": self.tenant_id,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "policy_violations": self.policy_violations,
            "resources_consumed": self.resources_consumed,
            "value_produced": round(self.value_produced, 4),
            "performance_score": round(self.performance_score, 2),
            "reliability_score": round(self.reliability_score, 2),
            "resource_efficiency_score": round(self.resource_efficiency_score, 2),
            "policy_compliance_score": round(self.policy_compliance_score, 2),
            "composite_score": round(self.composite_score, 2),
            "reputation_score": round(self.reputation_score, 2),
            "authority_level": self.authority_level,
            "evaluation_count": self.evaluation_count,
        }
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


class ResourceAllocationDecision(BaseModel):
    """Authoritative record of a deterministic resource allocation pass."""
    id: str
    tenant_id: str = "tenant-demo"
    organisation_id: str
    mission_id: Optional[str] = None
    strategy: AllocationStrategy
    treasury_available: int
    total_allocated: int
    allocations: Dict[str, int] = Field(default_factory=dict)  # agent_id -> allocated credits
    authority_limits: Dict[str, int] = Field(default_factory=dict)  # agent_id -> authority ceiling
    rationale: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def compute_decision_hash(self) -> str:
        data = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "strategy": self.strategy.value,
            "treasury_available": self.treasury_available,
            "total_allocated": self.total_allocated,
            "allocations": self.allocations,
            "authority_limits": self.authority_limits,
        }
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


class ReputationHistoryEntry(BaseModel):
    """Immutable audit trail of a reputation evaluation event."""
    id: str
    agent_id: str
    organisation_id: str
    tenant_id: str = "tenant-demo"
    previous_score: float
    new_score: float
    score_delta: float
    trigger_event: str  # e.g., "TASK_SUCCESS", "TASK_FAILURE", "POLICY_VIOLATION", "RECOVERY_SUCCESS"
    evidence_hash: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UtilityModel:
    """Transparent normalized economic utility computation for missions/tasks.
    
    Formula:
    UTILITY = outcome_value - resource_cost - risk_penalty - policy_violation_penalty - failure_cost
    """
    @staticmethod
    def calculate_utility(
        outcome_value: float,
        resource_cost: int,
        risk_penalty: float = 0.0,
        policy_violations: int = 0,
        failure: bool = False,
    ) -> float:
        policy_penalty = float(policy_violations * 5.0)
        failure_penalty = 10.0 if failure else 0.0
        utility = outcome_value - float(resource_cost) - risk_penalty - policy_penalty - failure_penalty
        return round(utility, 2)
