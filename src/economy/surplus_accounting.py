import math
import uuid
from typing import Dict, List, Optional
from datetime import datetime

from src.domain.enums import CurrencyAsset
from src.domain.work_order import RevenueEvent, WorkOrder, WorkDeliverableReceipt
from src.economy.ledger import (
    DoubleEntryLedger,
    REVENUE,
    TREASURY,
    SURPLUS_RESERVE,
    EXTERNAL_SINK,
)


class SurplusReconciler:
    """
    Reconciles external revenue and verified acquisition costs to calculate
    net surplus deterministically without conflating distinct economic assets.
    
    Invariants:
    1. Revenue and external costs are denominated and settled in USDG.
    2. Compute resource usage (ORBIO_CREDIT) is recorded in telemetry and events,
       never subtracted directly from currency units.
    3. Net surplus is non-negative: max(0, gross_revenue_usdg - direct_expense_usdg).
    4. Surplus is deterministically split between retained reserve and mission budget.
    5. All movements are recorded in DoubleEntryLedger preserving conservation.
    """

    def __init__(self, ledger: DoubleEntryLedger, default_reserve_ratio: float = 0.20):
        if not (0.0 <= default_reserve_ratio <= 1.0):
            raise ValueError("default_reserve_ratio must be between 0.0 and 1.0")
        self._ledger = ledger
        self._reserve_ratio = default_reserve_ratio
        self._events: Dict[str, RevenueEvent] = {}

    def reconcile_surplus(
        self,
        work_order: WorkOrder,
        receipt: WorkDeliverableReceipt,
        gross_revenue_usdg: int,
        direct_expense_usdg: int,
        orbio_credits_consumed: int,
        reserve_ratio: Optional[float] = None,
    ) -> RevenueEvent:
        if not receipt.is_verified():
            raise ValueError(f"Cannot reconcile unverified deliverable receipt {receipt.receipt_id}")
        if receipt.work_order_id != work_order.work_order_id:
            raise ValueError(f"Receipt work_order_id mismatch: {receipt.work_order_id} vs {work_order.work_order_id}")
        if gross_revenue_usdg < 0:
            raise ValueError("gross_revenue_usdg cannot be negative")
        if direct_expense_usdg < 0:
            raise ValueError("direct_expense_usdg cannot be negative")

        ratio = self._reserve_ratio if reserve_ratio is None else reserve_ratio
        if not (0.0 <= ratio <= 1.0):
            raise ValueError("reserve_ratio must be between 0.0 and 1.0")

        # 1. Calculate net surplus in USDG
        if gross_revenue_usdg >= direct_expense_usdg:
            net_surplus_usdg = gross_revenue_usdg - direct_expense_usdg
            allocated_to_reserve = math.floor(net_surplus_usdg * ratio)
            allocated_to_mission = net_surplus_usdg - allocated_to_reserve
            cost_reimbursement = direct_expense_usdg
        else:
            # Loss scenario: gross revenue does not cover direct expenses
            net_surplus_usdg = 0
            allocated_to_reserve = 0
            allocated_to_mission = 0
            cost_reimbursement = gross_revenue_usdg

        # 2. Perform DoubleEntryLedger settlement from REVENUE
        # Assumes gross_revenue_usdg was previously deposited into REVENUE
        rev_balance = self._ledger.get_balance(REVENUE)
        if rev_balance < gross_revenue_usdg:
            raise ValueError(
                f"REVENUE account has insufficient balance ({rev_balance}) for settlement of {gross_revenue_usdg}"
            )

        tx_group = str(uuid.uuid4())[:8]

        # Reimburse Treasury for the direct expense incurred earlier
        if cost_reimbursement > 0:
            self._ledger.transfer(
                from_account=REVENUE,
                to_account=TREASURY,
                amount=cost_reimbursement,
                memo=f"[USDG] Cost reimbursement for work order {work_order.work_order_id}",
                transaction_id=f"reconcile-cost-{work_order.work_order_id}-{tx_group}",
            )

        # Allocate reserve portion
        if allocated_to_reserve > 0:
            self._ledger.transfer(
                from_account=REVENUE,
                to_account=SURPLUS_RESERVE,
                amount=allocated_to_reserve,
                memo=f"[USDG] Retained surplus reserve for work order {work_order.work_order_id}",
                transaction_id=f"reconcile-res-{work_order.work_order_id}-{tx_group}",
            )

        # Allocate mission budget portion to Treasury
        if allocated_to_mission > 0:
            self._ledger.transfer(
                from_account=REVENUE,
                to_account=TREASURY,
                amount=allocated_to_mission,
                memo=f"[USDG] Mission budget surplus allocation for work order {work_order.work_order_id}",
                transaction_id=f"reconcile-msn-{work_order.work_order_id}-{tx_group}",
            )

        # 3. Create and record immutable RevenueEvent
        event_id = f"rev-{uuid.uuid4()}"
        event = RevenueEvent(
            revenue_event_id=event_id,
            work_order_id=work_order.work_order_id,
            gross_revenue_usdg=gross_revenue_usdg,
            direct_expense_usdg=direct_expense_usdg,
            net_surplus_usdg=net_surplus_usdg,
            orbio_credits_consumed=orbio_credits_consumed,
            allocated_to_mission_budget=allocated_to_mission,
            allocated_to_reserve=allocated_to_reserve,
            settled_at=datetime.utcnow(),
            ledger_tx_id=tx_group,
        )
        self._events[event_id] = event
        return event

    def get_event(self, revenue_event_id: str) -> Optional[RevenueEvent]:
        return self._events.get(revenue_event_id)

    def get_events_for_work_order(self, work_order_id: str) -> List[RevenueEvent]:
        return [e for e in self._events.values() if e.work_order_id == work_order_id]
