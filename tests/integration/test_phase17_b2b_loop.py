"""Integration test for the complete Phase 17 B2B Inter-DAO Commerce & Surplus Chaining Loop."""

import os
import pytest
from src.agents.b2b_marketplace_coordinator import B2BMarketplaceCoordinator
from src.domain.entities import AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, CurrencyAsset, OrgState, SolvencyRegime
from src.domain.work_order import WorkOrder
from src.economy.ledger import DoubleEntryLedger, ESCROW, EXTERNAL_SINK, REVENUE, SURPLUS_RESERVE, TREASURY
from src.economy.surplus_accounting import SurplusReconciler
from src.execution.work_executor import SimulatedWorkExecutor
from src.execution.orbio_gateway_adapter import OrbioGatewayAdapter
from src.governance.policy_engine import PolicyEngine
from src.orchestration.capability_manager import CapabilityManager
from src.persistence.database import Database
from src.persistence.marketplace_repository import MarketplaceRepository
from src.persistence.work_order_repository import WorkOrderRepository
from src.settlement.work_verifier import WorkDeliverableVerifier


def test_complete_phase17_b2b_loop_and_surplus_chaining():
    db = Database(":memory:")
    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-alpha", "tenant-demo", "FinTech Mission", 500, "PLANNING"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-beta", "tenant-demo", "Analytics Mission", 100, "PLANNING"),
        )

    mkt_repo = MarketplaceRepository(db)
    wo_repo = WorkOrderRepository(db)
    secret_key = "kalyx-phase17-test-secret-key"

    coordinator = B2BMarketplaceCoordinator(
        marketplace_repo=mkt_repo,
        work_order_repo=wo_repo,
        receipt_secret_key=secret_key,
    )

    # 1. SETUP ORGANISATIONS & LEDGERS
    client_ledger = DoubleEntryLedger(initial_treasury=500)
    client_policy = PolicyEngine(signing_secret=secret_key, human_approval_threshold=1000)
    client_ceo = AgentRecord(
        id="org-alpha-ceo",
        organisation_id="org-alpha",
        role=AgentRole.CEO,
        authority_ceiling=500,
        allowed_action_types=[ActionType.PUBLISH_MARKETPLACE_ORDER],
    )
    client_org = Organisation(
        id="org-alpha",
        mission="FinTech Portfolio Management",
        state=OrgState.PLANNING,
        treasury_balance=500,
        agents={"org-alpha-ceo": client_ceo},
    )

    provider_ledger = DoubleEntryLedger(initial_treasury=100)
    provider_policy = PolicyEngine(signing_secret=secret_key, human_approval_threshold=1000)
    provider_cap_mgr = CapabilityManager(repository=mkt_repo, policy_engine=provider_policy)
    provider_agent = AgentRecord(
        id="org-beta-worker",
        organisation_id="org-beta",
        role=AgentRole.RESEARCHER,
        allowed_action_types=[ActionType.CLAIM_MARKETPLACE_ORDER],
    )
    provider_agent.performance_score = 92.0
    provider_ceo = AgentRecord(
        id="org-beta-ceo",
        organisation_id="org-beta",
        role=AgentRole.CEO,
        allowed_action_types=[ActionType.PROPOSE_CAPABILITY_EXPANSION],
    )
    provider_org = Organisation(
        id="org-beta",
        mission="Decentralized Analytics",
        state=OrgState.PLANNING,
        treasury_balance=100,
        agents={"org-beta-worker": provider_agent, "org-beta-ceo": provider_ceo},
    )

    # 2. STAGE 1: ORG ALPHA PROPOSES & PUBLISHES B2B ORDER
    order = coordinator.publish_b2b_order(
        client_tenant_id="tenant-demo",
        client_org_id="org-alpha",
        title="Predictive Market Risk Assessment",
        description="Comprehensive scenario risk modeling",
        required_capability="ADVANCED_ANALYTICS",
        bounty_amount=300,
        client_ledger=client_ledger,
        policy_engine=client_policy,
        client_agent=client_ceo,
        client_org=client_org,
    )

    assert order is not None
    assert order.status == "OPEN"
    assert client_ledger.get_balance(TREASURY) == 200
    assert client_ledger.get_balance(ESCROW) == 300

    # 3. STAGE 2: ORG BETA DISCOVERS & CLAIMS ORDER (TRIGGERING GOVERNED CAPABILITY EVOLUTION)
    assert not provider_cap_mgr.check_agent_capability("tenant-demo", "org-beta", "org-beta-worker", "ADVANCED_ANALYTICS")

    claimed_order = coordinator.discover_and_claim(
        provider_tenant_id="tenant-demo",
        provider_org_id="org-beta",
        provider_agent=provider_agent,
        provider_org=provider_org,
        capability_manager=provider_cap_mgr,
        policy_engine=provider_policy,
        target_order_id=order.order_id,
    )

    assert claimed_order is not None
    assert claimed_order.status == "CLAIMED"
    assert claimed_order.claimed_by_org_id == "org-beta"
    assert provider_cap_mgr.check_agent_capability("tenant-demo", "org-beta", "org-beta-worker", "ADVANCED_ANALYTICS")

    # 4. STAGE 3: PRODUCTIVE WORK & EXECUTION VIA ORBIO (PLUGGABLE SIMULATOR WITH PROVENANCE)
    credit_store = {"org-beta": 100_000}
    simulated_executor = SimulatedWorkExecutor(credit_store=credit_store)
    orbio_adapter = OrbioGatewayAdapter(
        fallback_executor=simulated_executor,
        credit_store=credit_store,
    )
    work_verifier = WorkDeliverableVerifier(secret_key=secret_key)
    surplus_reconciler = SurplusReconciler(
        ledger=provider_ledger,
        default_reserve_ratio=0.20,
        receipt_secret_key=secret_key,
    )

    # 5. STAGE 4 & 5: EXECUTION, INDEPENDENT VERIFICATION, AND ESCROW SETTLEMENT
    outcome = coordinator.execute_and_settle(
        order=claimed_order,
        provider_tenant_id="tenant-demo",
        provider_org_id="org-beta",
        producer_agent_id="org-beta-worker",
        work_executor=orbio_adapter,
        work_verifier=work_verifier,
        client_ledger=client_ledger,
        provider_ledger=provider_ledger,
        surplus_reconciler=surplus_reconciler,
    )

    assert outcome.success is True
    assert outcome.is_simulated is True
    assert outcome.net_surplus_usdg == 300
    assert outcome.allocated_to_reserve == 60
    assert outcome.allocated_to_mission_budget == 240

    # Verify Client Org A ledger state: Escrow drained to EXTERNAL_SINK
    assert client_ledger.get_balance(ESCROW) == 0
    assert client_ledger.get_balance(EXTERNAL_SINK) == 300
    assert client_ledger.get_balance(TREASURY) == 200

    # Verify Provider Org B ledger state: Treasury received budget, reserve received reserve
    assert provider_ledger.get_balance(TREASURY) == 100 + 240  # 340 USDG
    assert provider_ledger.get_balance(SURPLUS_RESERVE) == 60   # 60 USDG

    # 6. STAGE 6: ORG BETA CHAINS SUBSEQUENT MISSION FUNDED STRICTLY FROM VERIFIED SURPLUS
    surplus_budget = outcome.allocated_to_mission_budget  # 240 USDG
    mission_2_work_order = WorkOrder(
        tenant_id="tenant-demo",
        organisation_id="org-beta",
        work_order_id="wo-mission-2",
        client_id="internal-growth",
        title="Expansion Product Build",
        description="Autonomous mission funded strictly from Mission 1 B2B surplus",
        deliverable_type="SECURITY_AUDIT",
        required_orbio_credits=1000,
        bounty_amount=200,  # <= 240 USDG surplus budget
        bounty_asset=CurrencyAsset.USDG,
    )

    assert mission_2_work_order.bounty_amount <= surplus_budget

    wo_repo.record_mission_lineage(
        tenant_id="tenant-demo",
        organisation_id="org-beta",
        mission_id="mission-2",
        parent_mission_id=outcome.order_id,
        funding_source="PRIOR_SURPLUS",
        funding_amount_usdg=surplus_budget,
    )

    lineage = wo_repo.get_mission_lineage("tenant-demo", "org-beta", "mission-2")
    assert lineage is not None
    assert lineage["funding_source"] == "PRIOR_SURPLUS"
    assert lineage["funding_amount_usdg"] == 240
    assert lineage["parent_mission_id"] == outcome.order_id
