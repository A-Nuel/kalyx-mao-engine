import pytest
from src.domain.economy import (
    AgentPerformanceRecord,
    AllocationStrategy,
    ResourceAllocationDecision,
)
from src.domain.entities import AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, AgentStatus
from src.economy.allocator import ResourceAllocator


def build_test_org(treasury=100):
    org = Organisation(
        id="org-alloc-test",
        mission="Test Resource Allocation Strategies",
        treasury_balance=treasury,
    )
    agents = [
        AgentRecord(
            id="agent-ceo",
            role=AgentRole.CEO,
            authority_ceiling=50,
            status=AgentStatus.ACTIVE,
            reputation_score=100.0,
            performance_score=100.0,
            reliability_score=100.0,
            resource_efficiency=1.0,
        ),
        AgentRecord(
            id="agent-res",
            role=AgentRole.RESEARCHER,
            authority_ceiling=25,
            status=AgentStatus.ACTIVE,
            reputation_score=90.0,
            performance_score=90.0,
            reliability_score=90.0,
            resource_efficiency=1.2,
        ),
        AgentRecord(
            id="agent-strat",
            role=AgentRole.STRATEGIST,
            authority_ceiling=25,
            status=AgentStatus.ACTIVE,
            reputation_score=80.0,
            performance_score=80.0,
            reliability_score=80.0,
            resource_efficiency=0.9,
        ),
        AgentRecord(
            id="agent-fin",
            role=AgentRole.FINANCIAL_ANALYST,
            authority_ceiling=25,
            status=AgentStatus.ACTIVE,
            reputation_score=70.0,
            performance_score=70.0,
            reliability_score=70.0,
            resource_efficiency=0.8,
        ),
    ]
    for a in agents:
        org.agents[a.id] = a
    return org


def test_allocate_static_equal_division():
    org = build_test_org(treasury=100)
    decision = ResourceAllocator.allocate(org, strategy=AllocationStrategy.STATIC)

    assert isinstance(decision, ResourceAllocationDecision)
    assert decision.strategy == AllocationStrategy.STATIC
    assert decision.treasury_available == 100
    for agent_id, alloc in decision.allocations.items():
        assert alloc <= 25
        assert alloc <= org.agents[agent_id].authority_ceiling
    assert sum(decision.allocations.values()) <= 100


def test_allocate_performance_proportional():
    org = build_test_org(treasury=100)
    perf_records = {
        "agent-ceo": AgentPerformanceRecord(
            agent_id="agent-ceo", organisation_id=org.id, composite_score=100.0
        ),
        "agent-res": AgentPerformanceRecord(
            agent_id="agent-res", organisation_id=org.id, composite_score=80.0
        ),
        "agent-strat": AgentPerformanceRecord(
            agent_id="agent-strat", organisation_id=org.id, composite_score=60.0
        ),
        "agent-fin": AgentPerformanceRecord(
            agent_id="agent-fin", organisation_id=org.id, composite_score=40.0
        ),
    }

    decision = ResourceAllocator.allocate(
        org, strategy=AllocationStrategy.PERFORMANCE, performance_records=perf_records
    )

    assert decision.strategy == AllocationStrategy.PERFORMANCE
    assert decision.allocations["agent-ceo"] >= decision.allocations["agent-res"]
    assert decision.allocations["agent-res"] >= decision.allocations["agent-strat"]
    assert decision.allocations["agent-strat"] >= decision.allocations["agent-fin"]
    assert sum(decision.allocations.values()) <= 100


def test_allocate_adaptive_with_scarcity_and_mission_type():
    org = build_test_org(treasury=15)
    decision = ResourceAllocator.allocate(
        org,
        strategy=AllocationStrategy.ADAPTIVE,
        mission_type="Autonomous Liquidity Rebalancing & Financial Optimization",
    )

    assert decision.strategy == AllocationStrategy.ADAPTIVE
    assert decision.total_allocated <= 15
    assert sum(decision.allocations.values()) <= 15
    assert decision.allocations["agent-fin"] >= 0


def test_conservation_invariant_zero_minting():
    for strat in [AllocationStrategy.STATIC, AllocationStrategy.PERFORMANCE, AllocationStrategy.ADAPTIVE]:
        org = build_test_org(treasury=57)
        decision = ResourceAllocator.allocate(org, strategy=strat)
        assert sum(decision.allocations.values()) <= 57
        assert decision.total_allocated == sum(decision.allocations.values())


def test_zero_treasury_handling():
    org = build_test_org(treasury=0)
    for strat in AllocationStrategy:
        decision = ResourceAllocator.allocate(org, strategy=strat)
        assert decision.total_allocated == 0
        for alloc in decision.allocations.values():
            assert alloc == 0


def test_suspended_and_retired_agents_excluded():
    org = build_test_org(treasury=100)
    org.agents["agent-strat"].status = AgentStatus.SUSPENDED
    org.agents["agent-fin"].status = AgentStatus.RETIRED

    decision = ResourceAllocator.allocate(org, strategy=AllocationStrategy.STATIC)
    assert decision.allocations.get("agent-strat", 0) == 0
    assert decision.allocations.get("agent-fin", 0) == 0
    assert decision.allocations["agent-ceo"] > 0
    assert decision.allocations["agent-res"] > 0


def test_authority_ceiling_strictly_bounds_allocation():
    org = build_test_org(treasury=1000)
    org.agents["agent-res"].authority_ceiling = 7

    for strat in AllocationStrategy:
        decision = ResourceAllocator.allocate(org, strategy=strat)
        assert decision.allocations["agent-res"] <= 7
