"""Deterministic resource allocation engine supporting STATIC, PERFORMANCE, and ADAPTIVE strategies."""

from __future__ import annotations

import math
import uuid
from typing import Any, Dict, List, Optional

from src.domain.economy import (
    AgentPerformanceRecord,
    AllocationStrategy,
    ResourceAllocationDecision,
)
from src.domain.entities import AgentRecord, Organisation
from src.domain.enums import AgentRole, AgentStatus


class ResourceAllocator:
    """Deterministic, mathematically conserved resource allocation engine.
    
    Guarantees:
    1. Zero resource creation: sum(allocated) <= available_treasury.
    2. Authority separation: budget <= authority_ceiling.
    3. Reproducibility: same inputs produce identical allocations.
    """

    @classmethod
    def allocate(
        cls,
        org: Organisation,
        strategy: AllocationStrategy = AllocationStrategy.PERFORMANCE,
        mission_id: Optional[str] = None,
        performance_records: Optional[Dict[str, AgentPerformanceRecord]] = None,
        mission_type: Optional[str] = None,
    ) -> ResourceAllocationDecision:
        """Executes a deterministic resource allocation pass according to the specified strategy."""
        treasury = max(0, org.treasury_balance)
        eligible_agents = [
            agent for agent in org.agents.values()
            if agent.status not in (AgentStatus.SUSPENDED, AgentStatus.RETIRED)
        ]

        if not eligible_agents or treasury == 0:
            allocations = {a.id: 0 for a in org.agents.values()}
            return ResourceAllocationDecision(
                id=f"alloc-{uuid.uuid4().hex[:12]}",
                tenant_id=getattr(org, "tenant_id", "tenant-demo"),
                organisation_id=org.id,
                mission_id=mission_id,
                strategy=strategy,
                treasury_available=treasury,
                total_allocated=0,
                allocations=allocations,
                authority_limits={a.id: a.authority_ceiling for a in org.agents.values()},
                rationale="Zero eligible agents or exhausted treasury",
            )

        if strategy == AllocationStrategy.STATIC:
            allocations, rationale = cls._allocate_static(eligible_agents, treasury)
        elif strategy == AllocationStrategy.PERFORMANCE:
            allocations, rationale = cls._allocate_performance(eligible_agents, treasury, performance_records)
        elif strategy == AllocationStrategy.ADAPTIVE:
            allocations, rationale = cls._allocate_adaptive(
                eligible_agents, treasury, performance_records, mission_type
            )
        else:
            raise ValueError(f"Unknown allocation strategy: {strategy}")

        # Strict conservation invariant assertion
        total_allocated = sum(allocations.values())
        assert total_allocated <= treasury, (
            f"Conservation violated: total allocated ({total_allocated}) > treasury ({treasury})"
        )

        # Update live agent credit balances
        for agent_id, credit in allocations.items():
            if agent_id in org.agents:
                org.agents[agent_id].credit_balance = credit
                org.agents[agent_id]._has_allocated_balance = True

        decision = ResourceAllocationDecision(
            id=f"alloc-{uuid.uuid4().hex[:12]}",
            tenant_id=getattr(org, "tenant_id", "tenant-demo"),
            organisation_id=org.id,
            mission_id=mission_id,
            strategy=strategy,
            treasury_available=treasury,
            total_allocated=total_allocated,
            allocations=allocations,
            authority_limits={a.id: a.authority_ceiling for a in eligible_agents},
            rationale=rationale,
        )
        return decision

    @classmethod
    def _allocate_static(
        cls, eligible_agents: List[AgentRecord], treasury: int
    ) -> tuple[Dict[str, int], str]:
        """STATIC: Equal division of available treasury among eligible agents, capped by authority ceiling."""
        n = len(eligible_agents)
        share = treasury // n
        allocations: Dict[str, int] = {}
        for agent in eligible_agents:
            if agent.status == AgentStatus.RESTRICTED:
                allocations[agent.id] = min(5, share)
            elif agent.status == AgentStatus.PROBATION:
                allocations[agent.id] = min(agent.authority_ceiling, min(12, share))
            else:
                allocations[agent.id] = min(agent.authority_ceiling, share)

        rationale = f"Static equal division: {share} credits per eligible agent (capped by authority)"
        return allocations, rationale

    @classmethod
    def _allocate_performance(
        cls,
        eligible_agents: List[AgentRecord],
        treasury: int,
        performance_records: Optional[Dict[str, AgentPerformanceRecord]] = None,
    ) -> tuple[Dict[str, int], str]:
        """PERFORMANCE: Proportional allocation based strictly on demonstrated composite performance."""
        scores: Dict[str, float] = {}
        for agent in eligible_agents:
            if agent.status == AgentStatus.RESTRICTED:
                # Bare minimum restricted floor
                scores[agent.id] = 5.0
            else:
                perf = performance_records.get(agent.id) if performance_records else None
                score = perf.composite_score if perf else agent.performance_score
                scores[agent.id] = max(1.0, score)

        total_score = sum(scores.values())
        allocations: Dict[str, int] = {}
        allocated_so_far = 0

        # Sort deterministic by (score desc, agent_id asc)
        sorted_agents = sorted(eligible_agents, key=lambda a: (-scores[a.id], a.id))
        for agent in sorted_agents:
            weight = scores[agent.id] / total_score
            raw_share = int(weight * treasury)
            # Cap by agent's authoritative ceiling
            share = min(agent.authority_ceiling, raw_share)
            # Ensure conservation
            available = treasury - allocated_so_far
            final_share = max(0, min(share, available))
            allocations[agent.id] = final_share
            allocated_so_far += final_share

        rationale = "Performance-weighted proportional allocation based on demonstrated composite scores"
        return allocations, rationale

    @classmethod
    def _allocate_adaptive(
        cls,
        eligible_agents: List[AgentRecord],
        treasury: int,
        performance_records: Optional[Dict[str, AgentPerformanceRecord]] = None,
        mission_type: Optional[str] = None,
    ) -> tuple[Dict[str, int], str]:
        """ADAPTIVE: Dynamic multi-factor allocation considering performance, reliability,
        efficiency, compliance, treasury scarcity pressure, and mission alignment.
        """
        # Treasury scarcity factor: if treasury is low (< 30 credits), dampen spend aggressively
        scarcity_multiplier = 1.0 if treasury >= 50 else (0.5 if treasury < 20 else 0.75)

        weights: Dict[str, float] = {}
        for agent in eligible_agents:
            if agent.status == AgentStatus.RESTRICTED:
                weights[agent.id] = 2.0
                continue
            if agent.status == AgentStatus.PROBATION:
                weights[agent.id] = 8.0
                continue

            perf = performance_records.get(agent.id) if performance_records else None
            perf_score = (perf.performance_score if perf else agent.performance_score) / 100.0
            rel_score = (perf.reliability_score if perf else agent.reliability_score) / 100.0
            eff_score = min(1.5, perf.resource_efficiency_score if perf else agent.resource_efficiency)
            comp_score = (perf.policy_compliance_score if perf else 100.0) / 100.0
            rep_score = agent.reputation_score / 100.0

            # Role relevance boost based on mission
            role_boost = 1.0
            if mission_type:
                m_lower = mission_type.lower()
                if "financial" in m_lower or "market" in m_lower or "liquidity" in m_lower:
                    if agent.role == AgentRole.FINANCIAL_ANALYST:
                        role_boost = 1.3
                elif "research" in m_lower or "analysis" in m_lower:
                    if agent.role == AgentRole.RESEARCHER:
                        role_boost = 1.3
                elif "strategy" in m_lower or "governance" in m_lower:
                    if agent.role == AgentRole.STRATEGIST:
                        role_boost = 1.3

            # Adaptive composite factor
            combined = (
                (0.30 * perf_score)
                + (0.25 * rel_score)
                + (0.20 * eff_score)
                + (0.15 * comp_score)
                + (0.10 * rep_score)
            ) * role_boost

            weights[agent.id] = max(0.1, combined)

        total_weight = sum(weights.values())
        allocations: Dict[str, int] = {}
        allocated_so_far = 0

        # Deterministic sorting
        sorted_agents = sorted(eligible_agents, key=lambda a: (-weights[a.id], a.id))
        for agent in sorted_agents:
            norm_w = weights[agent.id] / total_weight
            target_budget = int(norm_w * treasury * scarcity_multiplier)
            # Cap by authority ceiling and remaining budget
            share = min(agent.authority_ceiling, target_budget)
            remaining = treasury - allocated_so_far
            final_share = max(0, min(share, remaining))
            allocations[agent.id] = final_share
            allocated_so_far += final_share

        rationale = (
            f"Adaptive allocation: multi-factor weighted with scarcity damping "
            f"(factor {scarcity_multiplier}) and mission alignment"
        )
        return allocations, rationale

    # Backward compatibility method
    @classmethod
    def reallocate_budgets(cls, org: Organisation) -> Dict[str, int]:
        decision = cls.allocate(org, strategy=AllocationStrategy.PERFORMANCE)
        return decision.allocations
