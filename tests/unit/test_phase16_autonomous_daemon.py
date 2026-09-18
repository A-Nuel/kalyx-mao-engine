"""Unit tests for Phase 16 Milestone 5: Continuous Autonomous Daemon & Solvency Regimes.

Verifies:
1. Deterministic solvency regime derivation (EXPANSION, AUSTERE, STANDBY).
2. Daemon halts work acquisitions when entering STANDBY to preserve capital.
3. Multi-cycle autonomous execution chaining parent-child mission lineage.
4. Cumulative metric tracking across cycles.
"""

from datetime import datetime, timezone
import pytest

from src.agents.mock_adapter import MockAgentAdapter
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.agents.self_sustaining_loop import SelfSustainingLoopRunner
from src.agents.work_order_coordinator import WorkOrderCoordinator
from src.domain.enums import CurrencyAsset, DeliverableStatus, SolvencyRegime, WorkOrderStatus
from src.domain.work_order import WorkOrder
from src.economy.ledger import DoubleEntryLedger, REVENUE, TREASURY
from src.economy.surplus_accounting import SurplusReconciler
from src.execution.work_executor import SimulatedWorkExecutor
from src.orchestration.autonomous_daemon import AutonomousDaemon, derive_solvency_regime
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
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES ('tenant-daemon', 'Tenant Daemon', 'active', ?)",
            (now_iso,),
        )
        database.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES ('org-daemon', 'tenant-daemon', 'Autonomous Growth', 1000, 'ACTIVE', ?)",
            (now_iso,),
        )
    return database


@pytest.fixture
def repo(db):
    return WorkOrderRepository(db)


def test_derive_solvency_regime():
    assert derive_solvency_regime(200, expansion_threshold=150, standby_threshold=40) == SolvencyRegime.EXPANSION
    assert derive_solvency_regime(150, expansion_threshold=150, standby_threshold=40) == SolvencyRegime.EXPANSION
    assert derive_solvency_regime(100, expansion_threshold=150, standby_threshold=40) == SolvencyRegime.AUSTERE
    assert derive_solvency_regime(40, expansion_threshold=150, standby_threshold=40) == SolvencyRegime.AUSTERE
    assert derive_solvency_regime(39, expansion_threshold=150, standby_threshold=40) == SolvencyRegime.STANDBY
    assert derive_solvency_regime(0, expansion_threshold=150, standby_threshold=40) == SolvencyRegime.STANDBY


RECEIPT_SECRET = "receipt-secret-daemon-test"


def test_daemon_standby_preserves_capital(repo):
    ledger = DoubleEntryLedger(initial_treasury=20)  # Below 40 -> STANDBY
    provider = SimulatedOrbioExchangeProvider()
    executor = SimulatedWorkExecutor(exchange_provider=provider)
    verifier = WorkDeliverableVerifier(secret_key=RECEIPT_SECRET)
    reconciler = SurplusReconciler(
        ledger=ledger,
        default_reserve_ratio=0.20,
        receipt_secret_key=RECEIPT_SECRET,
    )
    runner = SelfSustainingLoopRunner(
        ledger=ledger,
        work_executor=executor,
        work_verifier=verifier,
        surplus_reconciler=reconciler,
        exchange_provider=provider,
    )
    adapter = MockAgentAdapter()
    ceo = CEOAgent(agent_id="ceo-1", adapter=adapter)
    analyst = FinancialAnalystAgent(agent_id="fa-1", adapter=adapter)
    coordinator = WorkOrderCoordinator(
        ceo_agent=ceo,
        financial_analyst=analyst,
        loop_runner=runner,
        work_order_repo=repo,
    )

    daemon = AutonomousDaemon(
        tenant_id="tenant-daemon",
        organisation_id="org-daemon",
        coordinator=coordinator,
        ledger=ledger,
        work_order_repo=repo,
        expansion_threshold=150,
        standby_threshold=40,
    )

    wo = WorkOrder(
        tenant_id="tenant-daemon",
        organisation_id="org-daemon",
        work_order_id="wo-standby-test",
        client_id="c1",
        title="Should not execute",
        description="Preserve remaining funds",
        deliverable_type="analysis",
        required_orbio_credits=100_000,
        bounty_amount=100,
    )

    res = daemon.step_cycle(candidate_work_orders=[wo])
    assert res.regime == SolvencyRegime.STANDBY
    assert res.outcome is None
    assert res.treasury_balance_before == 20
    assert res.treasury_balance_after == 20
    assert "STANDBY regime active" in res.state_summary
    assert ledger.get_balance(TREASURY) == 20


def test_daemon_multi_cycle_chaining_and_metrics(repo):
    ledger = DoubleEntryLedger(initial_treasury=200)  # Starts in EXPANSION
    provider = SimulatedOrbioExchangeProvider()
    provider._credit["org-daemon"] = 2_000_000  # Seed credits so no USDG purchases needed
    executor = SimulatedWorkExecutor(exchange_provider=provider)
    verifier = WorkDeliverableVerifier(secret_key=RECEIPT_SECRET)
    reconciler = SurplusReconciler(
        ledger=ledger,
        default_reserve_ratio=0.20,
        receipt_secret_key=RECEIPT_SECRET,
    )
    runner = SelfSustainingLoopRunner(
        ledger=ledger,
        work_executor=executor,
        work_verifier=verifier,
        surplus_reconciler=reconciler,
        exchange_provider=provider,
    )
    adapter = MockAgentAdapter()
    ceo = CEOAgent(agent_id="ceo-1", adapter=adapter)
    analyst = FinancialAnalystAgent(agent_id="fa-1", adapter=adapter)
    coordinator = WorkOrderCoordinator(
        ceo_agent=ceo,
        financial_analyst=analyst,
        loop_runner=runner,
        work_order_repo=repo,
    )

    daemon = AutonomousDaemon(
        tenant_id="tenant-daemon",
        organisation_id="org-daemon",
        coordinator=coordinator,
        ledger=ledger,
        work_order_repo=repo,
        expansion_threshold=150,
        standby_threshold=40,
    )

    wo_cycle1 = WorkOrder(
        tenant_id="tenant-daemon",
        organisation_id="org-daemon",
        work_order_id="wo-c1",
        client_id="c1",
        title="Cycle 1 Task",
        description="First autonomous loop",
        deliverable_type="audit",
        required_orbio_credits=500_000,
        bounty_amount=100,
        status=WorkOrderStatus.PROPOSED,
    )
    repo.save_work_order(wo_cycle1)

    wo_cycle2 = WorkOrder(
        tenant_id="tenant-daemon",
        organisation_id="org-daemon",
        work_order_id="wo-c2",
        client_id="c2",
        title="Cycle 2 Task",
        description="Second autonomous loop",
        deliverable_type="audit",
        required_orbio_credits=500_000,
        bounty_amount=150,
        status=WorkOrderStatus.PROPOSED,
    )
    repo.save_work_order(wo_cycle2)

    # Run 2 cycles
    results = daemon.run_cycles(
        max_cycles=2,
        candidate_batches=[[wo_cycle1], [wo_cycle2]],
    )

    assert len(results) == 2
    c1, c2 = results[0], results[1]

    assert c1.outcome is not None
    assert c1.outcome.success is True
    assert c1.work_order_id == "wo-c1"
    assert c1.regime == SolvencyRegime.EXPANSION

    assert c2.outcome is not None
    assert c2.outcome.success is True
    assert c2.work_order_id == "wo-c2"
    assert c2.regime == SolvencyRegime.EXPANSION

    # Verify parent-child lineage in repository
    lineage_list = repo.list_mission_lineage("tenant-daemon", "org-daemon")
    assert len(lineage_list) == 2
    mission1_id = c1.mission_id
    mission2_id = c2.mission_id

    m2_lineage = repo.get_mission_lineage("tenant-daemon", "org-daemon", mission2_id)
    assert m2_lineage is not None
    assert m2_lineage["parent_mission_id"] == mission1_id

    # Verify cumulative metrics
    assert daemon.cycle_count == 2
    assert daemon.total_revenue_earned_usdg == 250  # 100 + 150
    assert daemon.total_compute_consumed_credits == 1_000_000  # 500k + 500k


def test_daemon_cycle_requires_matching_receipt_secret(repo):
    """Proves that a daemon cycle fails settlement if the deliverable verifier and reconciler keys do not match."""
    ledger = DoubleEntryLedger(initial_treasury=200)
    provider = SimulatedOrbioExchangeProvider()
    provider._credit["org-daemon"] = 1_000_000
    executor = SimulatedWorkExecutor(exchange_provider=provider)
    verifier = WorkDeliverableVerifier(secret_key="daemon-secret-A")
    reconciler = SurplusReconciler(
        ledger=ledger,
        default_reserve_ratio=0.20,
        receipt_secret_key="daemon-secret-B",
    )
    runner = SelfSustainingLoopRunner(
        ledger=ledger,
        work_executor=executor,
        work_verifier=verifier,
        surplus_reconciler=reconciler,
        exchange_provider=provider,
    )
    adapter = MockAgentAdapter()
    ceo = CEOAgent(agent_id="ceo-1", adapter=adapter)
    analyst = FinancialAnalystAgent(agent_id="fa-1", adapter=adapter)
    coordinator = WorkOrderCoordinator(
        ceo_agent=ceo,
        financial_analyst=analyst,
        loop_runner=runner,
        work_order_repo=repo,
    )

    daemon = AutonomousDaemon(
        tenant_id="tenant-daemon",
        organisation_id="org-daemon",
        coordinator=coordinator,
        ledger=ledger,
        work_order_repo=repo,
    )

    wo = WorkOrder(
        tenant_id="tenant-daemon",
        organisation_id="org-daemon",
        work_order_id="wo-mismatch-cycle",
        client_id="c-mismatch",
        title="Mismatch Cycle Order",
        description="Deliverable signed with secret-A but reconciler expects secret-B",
        deliverable_type="analysis",
        required_orbio_credits=500_000,
        bounty_amount=100,
    )

    with pytest.raises(ValueError, match="invalid HMAC signature"):
        daemon.step_cycle(candidate_work_orders=[wo])
