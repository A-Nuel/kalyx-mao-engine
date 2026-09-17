"""Unit tests for Phase 16 Milestone 2: Canonical Multi-Agent Coordination.

Tests:
1. FinancialAnalystAgent unit-economics preflight (margin >= 15%, treasury solvency, zero bounty).
2. CEOAgent selection and prioritization.
3. WorkOrderCoordinator end-to-end viable execution and persistence.
4. WorkOrderCoordinator graceful rejection of unviable work orders.
"""

from datetime import datetime, timezone
import pytest

from src.agents.mock_adapter import MockAgentAdapter
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.agents.self_sustaining_loop import SelfSustainingLoopRunner
from src.agents.work_order_coordinator import WorkOrderCoordinator
from src.domain.enums import CurrencyAsset, DeliverableStatus, WorkOrderStatus
from src.domain.work_order import WorkOrder
from src.economy.ledger import DoubleEntryLedger, REVENUE, TREASURY
from src.economy.surplus_accounting import SurplusReconciler
from src.execution.work_executor import SimulatedWorkExecutor
from src.persistence.database import Database
from src.persistence.work_order_repository import WorkOrderRepository
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider
from src.settlement.work_verifier import WorkDeliverableVerifier


@pytest.fixture
def db():
    database = Database(":memory:")
    now_iso = datetime.now(timezone.utc).isoformat()
    with database.conn:
        database.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES ('tenant-test', 'Tenant Test', 'active', ?)",
            (now_iso,),
        )
        database.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES ('org-test', 'tenant-test', 'Autonomous Work', 1000, 'ACTIVE', ?)",
            (now_iso,),
        )
    return database


@pytest.fixture
def repo(db):
    return WorkOrderRepository(db)


@pytest.fixture
def ledger():
    return DoubleEntryLedger()


@pytest.fixture
def exchange_provider():
    provider = SimulatedOrbioExchangeProvider()
    return provider


@pytest.fixture
def work_executor(exchange_provider):
    return SimulatedWorkExecutor(exchange_provider=exchange_provider)


@pytest.fixture
def work_verifier():
    return WorkDeliverableVerifier(secret_key="verifier-secret-phase16")


@pytest.fixture
def surplus_reconciler(ledger):
    return SurplusReconciler(ledger=ledger, default_reserve_ratio=0.30)



@pytest.fixture
def loop_runner(ledger, work_executor, work_verifier, surplus_reconciler, exchange_provider):
    return SelfSustainingLoopRunner(
        ledger=ledger,
        work_executor=work_executor,
        work_verifier=work_verifier,
        surplus_reconciler=surplus_reconciler,
        exchange_provider=exchange_provider,
        reserve_ratio=0.30,
    )


@pytest.fixture
def coordinator(loop_runner, repo):
    adapter = MockAgentAdapter()
    ceo = CEOAgent(agent_id="ceo-1", adapter=adapter)
    analyst = FinancialAnalystAgent(agent_id="fa-1", adapter=adapter)
    return WorkOrderCoordinator(
        ceo_agent=ceo,
        financial_analyst=analyst,
        loop_runner=loop_runner,
        work_order_repo=repo,
        min_margin_ratio=0.15,
    )


def test_financial_analyst_unit_economics_viable():
    adapter = MockAgentAdapter()
    analyst = FinancialAnalystAgent(agent_id="fa-1", adapter=adapter)

    wo = WorkOrder(
        tenant_id="tenant-test",
        organisation_id="org-test",
        work_order_id="wo-high-margin",
        client_id="client-1",
        title="High Margin Research",
        description="High return research project",
        deliverable_type="research_report",
        required_orbio_credits=100_000,  # requires 0.1 USDG
        bounty_amount=200,  # 200 USDG bounty
    )

    evaluation = analyst.evaluate_unit_economics(
        work_order=wo,
        current_treasury_usdg=500,
        current_orbio_credits=0,
    )

    assert evaluation.viable is True
    assert evaluation.projected_direct_cost_usdg == 1
    assert evaluation.expected_surplus_usdg == 199
    assert evaluation.margin_ratio >= 0.90


def test_financial_analyst_unit_economics_thin_margin_rejected():
    adapter = MockAgentAdapter()
    analyst = FinancialAnalystAgent(agent_id="fa-1", adapter=adapter)

    wo = WorkOrder(
        tenant_id="tenant-test",
        organisation_id="org-test",
        work_order_id="wo-thin-margin",
        client_id="client-1",
        title="Thin Margin Task",
        description="Costly compute with low reward",
        deliverable_type="heavy_compute",
        required_orbio_credits=95_000_000,  # requires 95 USDG
        bounty_amount=100,  # 100 USDG bounty -> 5 USDG surplus -> 5% margin < 15%
    )

    evaluation = analyst.evaluate_unit_economics(
        work_order=wo,
        current_treasury_usdg=500,
        current_orbio_credits=0,
        min_margin_ratio=0.15,
    )

    assert evaluation.viable is False
    assert evaluation.margin_ratio == 0.05
    assert "MARGIN_BELOW_THRESHOLD" in evaluation.reason


def test_financial_analyst_unit_economics_insolvent_rejected():
    adapter = MockAgentAdapter()
    analyst = FinancialAnalystAgent(agent_id="fa-1", adapter=adapter)

    wo = WorkOrder(
        tenant_id="tenant-test",
        organisation_id="org-test",
        work_order_id="wo-unaffordable",
        client_id="client-1",
        title="Expensive Compute Task",
        description="Requires more compute upfront than treasury holds",
        deliverable_type="llm_finetune",
        required_orbio_credits=50_000_000,  # 50 USDG
        bounty_amount=200,
    )

    evaluation = analyst.evaluate_unit_economics(
        work_order=wo,
        current_treasury_usdg=10,  # Only 10 USDG in treasury
        current_orbio_credits=0,
    )

    assert evaluation.viable is False
    assert "INSUFFICIENT_TREASURY" in evaluation.reason


def test_ceo_prioritization_selects_highest_surplus():
    adapter = MockAgentAdapter()
    ceo = CEOAgent(agent_id="ceo-1", adapter=adapter)
    analyst = FinancialAnalystAgent(agent_id="fa-1", adapter=adapter)

    wo1 = WorkOrder(
        tenant_id="tenant-test",
        organisation_id="org-test",
        work_order_id="wo-small",
        client_id="c1",
        title="Small task",
        description="Quick",
        deliverable_type="code",
        required_orbio_credits=500_000,
        bounty_amount=50,
    )

    wo2 = WorkOrder(
        tenant_id="tenant-test",
        organisation_id="org-test",
        work_order_id="wo-large",
        client_id="c2",
        title="Large task",
        description="High yield",
        deliverable_type="analysis",
        required_orbio_credits=2_000_000,
        bounty_amount=250,
    )

    evals = {
        "wo-small": analyst.evaluate_unit_economics(wo1, 1000, 0),
        "wo-large": analyst.evaluate_unit_economics(wo2, 1000, 0),
    }

    selected = ceo.select_best_work_order([wo1, wo2], evals)
    assert selected is not None
    assert selected.work_order_id == "wo-large"


def test_work_order_coordinator_viable_execution(coordinator, exchange_provider, repo):
    # Grant initial treasury compute to exchange provider
    exchange_provider._credit["org-test"] = 1_000_000

    wo = WorkOrder(
        tenant_id="tenant-test",
        organisation_id="org-test",
        work_order_id="wo-viable-e2e",
        client_id="client-enterprise",
        title="Enterprise Security Audit",
        description="Audit multi-tenant contracts",
        deliverable_type="security_audit",
        required_orbio_credits=500_000,
        bounty_amount=300,
        bounty_asset=CurrencyAsset.USDG,
        deadline_seconds=3600,
        status=WorkOrderStatus.PROPOSED,
    )
    repo.save_work_order(wo)

    outcome = coordinator.select_and_coordinate(
        mission_id="m-coord-001",
        work_orders=[wo],
        current_treasury_usdg=500,
        current_orbio_credits=1_000_000,
        producer_agent_id="agent-sec-researcher",
        parent_mission_id="m-parent-genesis",
        funding_source="GENESIS_SEED",
        funding_amount_usdg=100,
    )

    assert outcome.success is True
    assert outcome.status == WorkOrderStatus.SETTLED
    assert outcome.evaluation.viable is True
    assert outcome.deliverable is not None
    assert outcome.receipt is not None
    assert outcome.receipt.status == DeliverableStatus.ACCEPTED
    assert outcome.revenue_event is not None
    assert outcome.revenue_event.gross_revenue_usdg == 300
    assert outcome.revenue_event.net_surplus_usdg == 300
    assert outcome.allocated_to_mission_budget == 210  # 70% of 300
    assert outcome.allocated_to_reserve == 90         # 30% of 300

    # Verify persistence in repository
    persisted_wo = repo.get_work_order("tenant-test", "org-test", "wo-viable-e2e")
    assert persisted_wo is not None
    assert persisted_wo.status == WorkOrderStatus.SETTLED

    persisted_deliv = repo.get_deliverable("tenant-test", "org-test", outcome.deliverable.deliverable_id)
    assert persisted_deliv is not None

    persisted_receipt = repo.get_receipt("tenant-test", "org-test", outcome.receipt.receipt_id)
    assert persisted_receipt is not None
    assert persisted_receipt.status == DeliverableStatus.ACCEPTED

    lineage = repo.get_mission_lineage("tenant-test", "org-test", "m-coord-001")
    assert lineage is not None
    assert lineage["parent_mission_id"] == "m-parent-genesis"
    assert lineage["funding_source"] == "GENESIS_SEED"


def test_work_order_coordinator_unviable_rejection(coordinator, repo):
    wo = WorkOrder(
        tenant_id="tenant-test",
        organisation_id="org-test",
        work_order_id="wo-loss-making",
        client_id="client-cheap",
        title="Unprofitable Task",
        description="Massive compute for tiny reward",
        deliverable_type="deep_training",
        required_orbio_credits=50_000_000,  # 50 USDG
        bounty_amount=52,  # 2 USDG surplus -> ~3.8% margin < 15%
        status=WorkOrderStatus.PROPOSED,
    )
    repo.save_work_order(wo)

    outcome = coordinator.select_and_coordinate(
        mission_id="m-coord-unviable",
        work_orders=[wo],
        current_treasury_usdg=500,
        current_orbio_credits=0,
        producer_agent_id="agent-researcher",
    )

    assert outcome.success is False
    assert outcome.status == WorkOrderStatus.REJECTED
    assert outcome.deliverable is None
    assert outcome.revenue_event is None

    # Verify persisted as REJECTED in repository
    persisted_wo = repo.get_work_order("tenant-test", "org-test", "wo-loss-making")
    assert persisted_wo is not None
    assert persisted_wo.status == WorkOrderStatus.REJECTED
