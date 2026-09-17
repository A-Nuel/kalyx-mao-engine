import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.agents.orbio_purchase_loop import LoopStatus, OrbioPurchaseAgentLoop
from src.domain.enums import CurrencyAsset, DeliverableStatus, WorkOrderStatus
from src.domain.work_order import (
    RevenueEvent,
    WorkDeliverable,
    WorkDeliverableReceipt,
    WorkOrder,
)
from src.economy.ledger import DoubleEntryLedger, REVENUE, TREASURY
from src.economy.surplus_accounting import SurplusReconciler
from src.execution.work_executor import BaseWorkExecutor
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider
from src.settlement.work_verifier import WorkDeliverableVerifier

logger = logging.getLogger(__name__)


@dataclass
class MissionExecutionResult:
    mission_id: str
    work_order: WorkOrder
    deliverable: Optional[WorkDeliverable]
    receipt: Optional[WorkDeliverableReceipt]
    revenue_event: Optional[RevenueEvent]
    direct_expense_usdg: int
    net_surplus_usdg: int
    allocated_to_next_mission: int
    allocated_to_reserve: int
    success: bool
    error_message: Optional[str] = None


class SelfSustainingLoopRunner:
    """
    Coordinates the complete self-sustaining economic loop:

    Mission 1:
      1. Receive/Propose WorkOrder
      2. Resource check: Evaluate Orbio CREDITs
      3. Acquire Orbio CREDITs via Phase 14B Purchase Loop if needed (settled ESCROW -> EXTERNAL_SINK)
      4. Execute productive work (SimulatedWorkExecutor), producing WorkDeliverable
      5. Independent deliverable verification (WorkDeliverableVerifier)
      6. Client settles bounty into REVENUE
      7. Reconcile costs and net surplus (SurplusReconciler)
      8. Record surplus allocation to next mission budget and reserve

    Mission 2:
      - Funded STRICTLY from Mission 1 surplus budget allocation (zero external capital).
    """

    def __init__(
        self,
        ledger: DoubleEntryLedger,
        work_executor: BaseWorkExecutor,
        work_verifier: WorkDeliverableVerifier,
        surplus_reconciler: SurplusReconciler,
        exchange_provider: Optional[SimulatedOrbioExchangeProvider] = None,
        reserve_ratio: float = 0.20,
    ):
        self.ledger = ledger
        self.work_executor = work_executor
        self.work_verifier = work_verifier
        self.surplus_reconciler = surplus_reconciler
        self.exchange_provider = exchange_provider
        self.reserve_ratio = reserve_ratio

    def execute_mission(
        self,
        mission_id: str,
        work_order: WorkOrder,
        producer_agent_id: str,
        purchase_loop: Optional[OrbioPurchaseAgentLoop] = None,
        available_budget_limit: Optional[int] = None,
    ) -> MissionExecutionResult:
        work_order.transition_to(WorkOrderStatus.IN_PROGRESS)
        org_id = work_order.organisation_id

        # 1. Resource Preflight: check available Orbio credits
        current_credits = 0
        if self.exchange_provider is not None:
            current_credits = self.exchange_provider.get_credit_balance(org_id)
        elif hasattr(self.work_executor, "get_credit_balance"):
            current_credits = self.work_executor.get_credit_balance(org_id)

        direct_expense_usdg = 0
        credits_needed = work_order.required_orbio_credits

        # 2. Resource Acquisition via Phase 14B loop if credits are insufficient
        if current_credits < credits_needed:
            shortfall = credits_needed - current_credits
            if purchase_loop is None:
                work_order.transition_to(WorkOrderStatus.REJECTED)
                return MissionExecutionResult(
                    mission_id=mission_id,
                    work_order=work_order,
                    deliverable=None,
                    receipt=None,
                    revenue_event=None,
                    direct_expense_usdg=0,
                    net_surplus_usdg=0,
                    allocated_to_next_mission=0,
                    allocated_to_reserve=0,
                    success=False,
                    error_message=f"Need {shortfall} Orbio credits but no purchase loop provided.",
                )

            # Sizing USDG needed (converting native micro-units to whole USDG for ledger/budget comparison)
            usdg_needed_native = purchase_loop._usdg_for_credit_need(shortfall)
            # Ceil native 6-decimal USDG into whole USDG budget units. Floor division
            # would understate a fractional-unit acquisition and could admit an
            # unaffordable mission at the budget boundary.
            usdg_needed_whole = max(1, (usdg_needed_native + 1_000_000 - 1) // 1_000_000)

            # Enforce self-funded budget limit if specified (e.g. Mission 2)
            if available_budget_limit is not None and usdg_needed_whole > available_budget_limit:
                work_order.transition_to(WorkOrderStatus.REJECTED)
                return MissionExecutionResult(
                    mission_id=mission_id,
                    work_order=work_order,
                    deliverable=None,
                    receipt=None,
                    revenue_event=None,
                    direct_expense_usdg=0,
                    net_surplus_usdg=0,
                    allocated_to_next_mission=0,
                    allocated_to_reserve=0,
                    success=False,
                    error_message=f"Acquisition cost {usdg_needed_whole} USDG exceeds self-funded mission budget {available_budget_limit} USDG.",
                )

            # Run Phase 14B acquisition step
            treasury_before = self.ledger.get_balance(TREASURY)
            step_result = purchase_loop.step()
            treasury_after = self.ledger.get_balance(TREASURY)
            direct_expense_usdg = max(0, treasury_before - treasury_after)

            is_successful = (
                step_result.verification_result == "VERIFIED"
                or step_result.status == LoopStatus.OBJECTIVE_COMPLETE
                or purchase_loop.state.acquired_credit >= credits_needed
            )
            if not is_successful:
                work_order.transition_to(WorkOrderStatus.REJECTED)
                return MissionExecutionResult(
                    mission_id=mission_id,
                    work_order=work_order,
                    deliverable=None,
                    receipt=None,
                    revenue_event=None,
                    direct_expense_usdg=direct_expense_usdg,
                    net_surplus_usdg=0,
                    allocated_to_next_mission=0,
                    allocated_to_reserve=0,
                    success=False,
                    error_message=f"Orbio credit acquisition failed: {step_result.message}",
                )

        # 3. Productive Work Execution
        try:
            deliverable = self.work_executor.execute_work(
                work_order=work_order,
                producer_agent_id=producer_agent_id,
                organisation_id=org_id,
            )
            work_order.transition_to(WorkOrderStatus.DELIVERED)
        except Exception as e:
            work_order.transition_to(WorkOrderStatus.REJECTED)
            return MissionExecutionResult(
                mission_id=mission_id,
                work_order=work_order,
                deliverable=None,
                receipt=None,
                revenue_event=None,
                direct_expense_usdg=direct_expense_usdg,
                net_surplus_usdg=0,
                allocated_to_next_mission=0,
                allocated_to_reserve=0,
                success=False,
                error_message=f"Work execution failed: {str(e)}",
            )

        # 4. Independent Deliverable Verification
        receipt = self.work_verifier.verify(work_order=work_order, deliverable=deliverable)
        if not receipt.is_verified() or not receipt.verify_hmac(self.work_verifier.secret_key):
            work_order.transition_to(WorkOrderStatus.REJECTED)
            return MissionExecutionResult(
                mission_id=mission_id,
                work_order=work_order,
                deliverable=deliverable,
                receipt=receipt,
                revenue_event=None,
                direct_expense_usdg=direct_expense_usdg,
                net_surplus_usdg=0,
                allocated_to_next_mission=0,
                allocated_to_reserve=0,
                success=False,
                error_message=f"Deliverable audit rejected: {receipt.verification_notes}",
            )

        work_order.transition_to(WorkOrderStatus.VERIFIED)

        # 5. Client Revenue Settlement & Surplus Reconciliation
        # Simulate client depositing bounty USDG into the REVENUE account
        gross_bounty = work_order.bounty_amount
        self.ledger._mint(
            to_account=REVENUE,
            amount=gross_bounty,
            memo=f"Client deposit for work order {work_order.work_order_id}",
        )

        revenue_event = self.surplus_reconciler.reconcile_surplus(
            work_order=work_order,
            receipt=receipt,
            gross_revenue_usdg=gross_bounty,
            direct_expense_usdg=direct_expense_usdg,
            orbio_credits_consumed=deliverable.orbio_credits_consumed,
            reserve_ratio=self.reserve_ratio,
        )

        work_order.transition_to(WorkOrderStatus.SETTLED)

        return MissionExecutionResult(
            mission_id=mission_id,
            work_order=work_order,
            deliverable=deliverable,
            receipt=receipt,
            revenue_event=revenue_event,
            direct_expense_usdg=direct_expense_usdg,
            net_surplus_usdg=revenue_event.net_surplus_usdg,
            allocated_to_next_mission=revenue_event.allocated_to_mission_budget,
            allocated_to_reserve=revenue_event.allocated_to_reserve,
            success=True,
        )
