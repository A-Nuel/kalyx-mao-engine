import pytest
from src.agents.orbio_purchase_loop import AgentLoopState, OrbioPurchaseAgentLoop
from src.agents.self_sustaining_loop import SelfSustainingLoopRunner
from src.domain.entities import Organisation
from src.domain.enums import CurrencyAsset, DeliverableStatus, WorkOrderStatus
from src.domain.work_order import WorkOrder
from src.economy.ledger import DoubleEntryLedger, EXTERNAL_SINK, REVENUE, SURPLUS_RESERVE, TREASURY
from src.economy.surplus_accounting import SurplusReconciler
from src.execution.orbio_purchase import OrbioPurchaseBridge
from src.execution.work_executor import SimulatedWorkExecutor
from src.governance.orbio_purchase_rules import OrbioPurchasePolicy
from src.settlement.orbio_purchase_reconciliation import OrbioPurchaseReconciliation
from src.settlement.orbio_purchase_verifier import OrbioPurchaseVerifier
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider
from src.settlement.work_verifier import WorkDeliverableVerifier

BENEFICIARY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
ONE_CREDIT = 1_000_000  # Phase 14B native 6-decimal micro-units


def create_loop_infrastructure(initial_treasury=50):
    org = Organisation(id="org-a", mission="self-sustaining", tenant_id="tenant-a", treasury_balance=initial_treasury)
    ledger = DoubleEntryLedger(initial_treasury=initial_treasury)
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 100_000_000)

    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=100_000_000,
        absolute_usdg_ceiling=100_000_000,
    )
    bridge = OrbioPurchaseBridge(purchase_policy=policy)
    orbio_verifier = OrbioPurchaseVerifier()
    orbio_reconciler = OrbioPurchaseReconciliation(
        provider=provider,
        ledger=ledger,
        verifier=orbio_verifier,
    )

    work_executor = SimulatedWorkExecutor(exchange_provider=provider)
    work_verifier = WorkDeliverableVerifier(secret_key="verifier-secret-phase15")
    surplus_reconciler = SurplusReconciler(ledger=ledger, default_reserve_ratio=0.20)

    loop_runner = SelfSustainingLoopRunner(
        ledger=ledger,
        work_executor=work_executor,
        work_verifier=work_verifier,
        surplus_reconciler=surplus_reconciler,
        exchange_provider=provider,
        reserve_ratio=0.20,
    )

    return {
        "org": org,
        "ledger": ledger,
        "provider": provider,
        "policy": policy,
        "bridge": bridge,
        "orbio_verifier": orbio_verifier,
        "orbio_reconciler": orbio_reconciler,
        "work_executor": work_executor,
        "work_verifier": work_verifier,
        "surplus_reconciler": surplus_reconciler,
        "loop_runner": loop_runner,
    }


def create_purchase_loop(infra, mission_id, target_credit):
    return OrbioPurchaseAgentLoop(
        state=AgentLoopState(
            agent_id="agent-eng",
            tenant_id="tenant-a",
            organisation_id="org-a",
            mission_id=mission_id,
            objective="Acquire compute resources for mission",
            target_credit=target_credit,
        ),
        org=infra["org"],
        policy=infra["policy"],
        bridge=infra["bridge"],
        provider=infra["provider"],
        verifier=infra["orbio_verifier"],
        reconciler=infra["orbio_reconciler"],
        ledger=infra["ledger"],
        max_single_usdg=50_000_000,
        max_cumulative_usdg=50_000_000,
        default_beneficiary=BENEFICIARY,
    )


def test_full_recursive_self_sustaining_loop():
    """
    Core Phase 15 E2E Proof:
    1. Mission 1 requires 10 Orbio credits (10_000_000 micro-units), has 0 credits.
    2. Acquires credits via Phase 14B loop (costs 10 USDG from Treasury).
    3. Executes productive security audit, passing independent audit.
    4. Collects 50 USDG revenue.
    5. Reconciles: Gross 50 - Cost 10 = 40 Net Surplus.
       - 8 USDG to SURPLUS_RESERVE
       - 32 USDG to Next Mission Budget
    6. Mission 2 is funded STRICTLY from the 32 USDG surplus (0 external capital).
    7. Mission 2 acquires 15 credits (costing 15 USDG <= 32 USDG budget limit).
    8. Mission 2 delivers work, passes verification, earns 70 USDG.
    9. Complete ledger conservation verified across both cycles.
    """
    infra = create_loop_infrastructure(initial_treasury=50)
    runner = infra["loop_runner"]
    ledger = infra["ledger"]

    # -------------------------------------------------------------
    # MISSION 1
    # -------------------------------------------------------------
    wo1 = WorkOrder(
        work_order_id="wo-msn1-audit",
        tenant_id="tenant-a",
        organisation_id="org-a",
        client_id="client-dao",
        title="Protocol Vault Audit",
        description="Verify security of smart contracts",
        deliverable_type="SECURITY_AUDIT",
        required_orbio_credits=10 * ONE_CREDIT,
        bounty_amount=50,
        bounty_asset=CurrencyAsset.USDG,
    )

    purchase_loop1 = create_purchase_loop(infra, "msn-1", target_credit=10 * ONE_CREDIT)
    res1 = runner.execute_mission(
        mission_id="msn-1",
        work_order=wo1,
        producer_agent_id="agent-eng",
        purchase_loop=purchase_loop1,
    )

    assert res1.success is True
    assert res1.direct_expense_usdg == 10
    assert res1.net_surplus_usdg == 40
    assert res1.allocated_to_reserve == 8
    assert res1.allocated_to_next_mission == 32
    assert res1.receipt.is_verified() is True
    assert res1.work_order.status == WorkOrderStatus.SETTLED

    # Check ledger conservation after Mission 1
    assert ledger.get_balance(REVENUE) == 0
    assert ledger.get_balance(SURPLUS_RESERVE) == 8
    # Treasury: 50 - 10 (spent) + 10 (cost reimbursement) + 32 (surplus) = 82
    assert ledger.get_balance(TREASURY) == 82
    assert ledger.get_balance(EXTERNAL_SINK) == 10
    assert ledger.verify_conservation() is True

    # -------------------------------------------------------------
    # MISSION 2 (Funded strictly from Mission 1 Surplus)
    # -------------------------------------------------------------
    self_funded_budget = res1.allocated_to_next_mission
    assert self_funded_budget == 32

    wo2 = WorkOrder(
        work_order_id="wo-msn2-market",
        tenant_id="tenant-a",
        organisation_id="org-a",
        client_id="client-dao-2",
        title="DEX Liquidity Analysis",
        description="Market depth and slippage projections",
        deliverable_type="MARKET_ANALYSIS",
        required_orbio_credits=15 * ONE_CREDIT,
        bounty_amount=70,
        bounty_asset=CurrencyAsset.USDG,
    )

    purchase_loop2 = create_purchase_loop(infra, "msn-2", target_credit=15 * ONE_CREDIT)
    res2 = runner.execute_mission(
        mission_id="msn-2",
        work_order=wo2,
        producer_agent_id="agent-eng",
        purchase_loop=purchase_loop2,
        available_budget_limit=self_funded_budget,
    )

    assert res2.success is True
    assert res2.direct_expense_usdg == 15  # 15 USDG <= 32 USDG self-funded budget
    assert res2.net_surplus_usdg == 55     # 70 - 15
    assert res2.allocated_to_reserve == 11  # 20% of 55
    assert res2.allocated_to_next_mission == 44 # 80% of 55
    assert res2.receipt.is_verified() is True
    assert res2.work_order.status == WorkOrderStatus.SETTLED

    # Check ledger conservation after Mission 2
    assert ledger.get_balance(REVENUE) == 0
    assert ledger.get_balance(SURPLUS_RESERVE) == 8 + 11 # 19
    # Treasury: 82 - 15 + 15 + 44 = 126
    assert ledger.get_balance(TREASURY) == 126
    assert ledger.get_balance(EXTERNAL_SINK) == 10 + 15 # 25
    assert ledger.verify_conservation() is True


def test_budget_overrun_protection_on_self_funded_mission():
    """
    If Mission 2 requires more resources than the surplus allocated by Mission 1,
    the preflight check must reject the mission before any capital is spent.
    """
    infra = create_loop_infrastructure(initial_treasury=50)
    runner = infra["loop_runner"]
    ledger = infra["ledger"]

    # Budget limit from previous mission is only 10 USDG
    available_budget = 10

    wo = WorkOrder(
        work_order_id="wo-expensive",
        tenant_id="tenant-a",
        organisation_id="org-a",
        client_id="client-dao",
        title="Massive Compute Audit",
        description="Requires 40 Orbio credits",
        deliverable_type="SECURITY_AUDIT",
        required_orbio_credits=40 * ONE_CREDIT,  # Costs ~40 USDG, exceeds 10 USDG limit!
        bounty_amount=100,
        bounty_asset=CurrencyAsset.USDG,
    )

    purchase_loop = create_purchase_loop(infra, "msn-overrun", target_credit=40 * ONE_CREDIT)
    treasury_before = ledger.get_balance(TREASURY)

    result = runner.execute_mission(
        mission_id="msn-overrun",
        work_order=wo,
        producer_agent_id="agent-eng",
        purchase_loop=purchase_loop,
        available_budget_limit=available_budget,
    )

    assert result.success is False
    assert "exceeds self-funded mission budget" in result.error_message
    assert wo.status == WorkOrderStatus.REJECTED
    # Invariant: Not a single cent spent from Treasury
    assert ledger.get_balance(TREASURY) == treasury_before
    assert ledger.verify_conservation() is True


def test_audit_rejection_halts_revenue_release():
    """
    If the deliverable is rejected by the independent verifier (e.g. content hash mismatch),
    the loop halts, no revenue is collected, and no surplus is minted.
    """
    infra = create_loop_infrastructure(initial_treasury=50)
    runner = infra["loop_runner"]
    ledger = infra["ledger"]

    wo = WorkOrder(
        work_order_id="wo-fail-audit",
        tenant_id="tenant-a",
        organisation_id="org-a",
        client_id="client-dao",
        title="Tampered Audit",
        description="Deliverable will be corrupted",
        deliverable_type="SECURITY_AUDIT",
        required_orbio_credits=10 * ONE_CREDIT,
        bounty_amount=50,
        bounty_asset=CurrencyAsset.USDG,
    )

    purchase_loop = create_purchase_loop(infra, "msn-fail", target_credit=10 * ONE_CREDIT)

    # Monkeypatch work_executor to simulate corruption
    orig_execute = runner.work_executor.execute_work
    def corrupt_execute(*args, **kwargs):
        deliv = orig_execute(*args, **kwargs)
        deliv.content_payload["audit_verdict"] = "MALICIOUS_TAMPER"
        return deliv

    runner.work_executor.execute_work = corrupt_execute

    result = runner.execute_mission(
        mission_id="msn-fail",
        work_order=wo,
        producer_agent_id="agent-eng",
        purchase_loop=purchase_loop,
    )

    assert result.success is False
    assert "Deliverable audit rejected" in result.error_message
    assert result.net_surplus_usdg == 0
    assert result.allocated_to_next_mission == 0
    assert wo.status == WorkOrderStatus.REJECTED

    # Revenue was NOT released
    assert ledger.get_balance(REVENUE) == 0
    assert ledger.get_balance(SURPLUS_RESERVE) == 0
    assert ledger.verify_conservation() is True


def test_zero_surplus_loss_scenario_blocks_mission_2():
    """
    If Mission 1 produces 0 net surplus (loss scenario where bounty < cost),
    the allocated budget for Mission 2 is 0. Attempting to run Mission 2 with
    budget_limit=0 must immediately fail at preflight without spending any funds.
    """
    infra = create_loop_infrastructure(initial_treasury=50)
    runner = infra["loop_runner"]
    ledger = infra["ledger"]

    # WorkOrder 1 has a tiny bounty of 5 USDG, but costs 10 USDG to acquire credits
    wo1 = WorkOrder(
        work_order_id="wo-loss-01",
        tenant_id="tenant-a",
        organisation_id="org-a",
        client_id="client-dao",
        title="Underpriced Audit",
        description="Bounty does not cover compute cost",
        deliverable_type="SECURITY_AUDIT",
        required_orbio_credits=10 * ONE_CREDIT,
        bounty_amount=5,
        bounty_asset=CurrencyAsset.USDG,
    )

    purchase_loop1 = create_purchase_loop(infra, "msn-loss", target_credit=10 * ONE_CREDIT)
    res1 = runner.execute_mission(
        mission_id="msn-loss",
        work_order=wo1,
        producer_agent_id="agent-eng",
        purchase_loop=purchase_loop1,
    )

    # Mission 1 completes work, but generates 0 surplus
    assert res1.success is True
    assert res1.direct_expense_usdg == 10
    assert res1.net_surplus_usdg == 0
    assert res1.allocated_to_reserve == 0
    assert res1.allocated_to_next_mission == 0
    assert ledger.verify_conservation() is True

    # Now attempt Mission 2 using Mission 1's surplus budget allocation (0 USDG)
    wo2 = WorkOrder(
        work_order_id="wo-msn2-fail",
        tenant_id="tenant-a",
        organisation_id="org-a",
        client_id="client-dao",
        title="Subsequent Mission",
        description="Requires compute resources",
        deliverable_type="SECURITY_AUDIT",
        required_orbio_credits=10 * ONE_CREDIT,
        bounty_amount=50,
        bounty_asset=CurrencyAsset.USDG,
    )

    purchase_loop2 = create_purchase_loop(infra, "msn-loss-2", target_credit=10 * ONE_CREDIT)
    treasury_before = ledger.get_balance(TREASURY)

    res2 = runner.execute_mission(
        mission_id="msn-loss-2",
        work_order=wo2,
        producer_agent_id="agent-eng",
        purchase_loop=purchase_loop2,
        available_budget_limit=res1.allocated_to_next_mission,  # 0 USDG!
    )

    # Mission 2 is rejected at preflight: 10 USDG acquisition cost exceeds 0 USDG budget
    assert res2.success is False
    assert "exceeds self-funded mission budget 0 USDG" in res2.error_message
    assert wo2.status == WorkOrderStatus.REJECTED
    assert ledger.get_balance(TREASURY) == treasury_before
    assert ledger.verify_conservation() is True
