"""WorkOrderCoordinator — Canonical Multi-Agent Coordination for Phase 16.

Orchestrates the complete economic and operational cycle:
1. CEOAgent selects and prioritizes work orders.
2. FinancialAnalystAgent performs rigorous unit-economics preflight (margin >= 15%, treasury solvency).
3. PolicyEngine validates and authorizes action proposal.
4. SelfSustainingLoopRunner manages Orbio acquisition (if needed), execution, deliverable verification, and surplus reconciliation.
5. WorkOrderRepository persists all state transitions, deliverables, receipts, revenue events, and mission lineage.

Invariant:
AGENTS PROPOSE -> POLICIES AUTHORIZE -> EXECUTORS EXECUTE -> AUDITORS VERIFY
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.agents.orbio_purchase_loop import OrbioPurchaseAgentLoop
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.agents.schemas import UnitEconomicsEvaluation
from src.agents.self_sustaining_loop import MissionExecutionResult, SelfSustainingLoopRunner
from src.domain.enums import DeliverableStatus, WorkOrderStatus
from src.domain.work_order import (
    RevenueEvent,
    WorkDeliverable,
    WorkDeliverableReceipt,
    WorkOrder,
)
from src.persistence.work_order_repository import WorkOrderRepository

logger = logging.getLogger(__name__)


@dataclass
class WorkOrderExecutionOutcome:
    mission_id: str
    work_order_id: str
    status: WorkOrderStatus
    evaluation: UnitEconomicsEvaluation
    deliverable: Optional[WorkDeliverable]
    receipt: Optional[WorkDeliverableReceipt]
    revenue_event: Optional[RevenueEvent]
    direct_expense_usdg: int
    net_surplus_usdg: int
    allocated_to_mission_budget: int
    allocated_to_reserve: int
    success: bool
    error_message: Optional[str] = None


class WorkOrderCoordinator:
    """Multi-agent orchestrator integrating CEO, Financial Analyst, Executor, Auditor, and Repository."""

    def __init__(
        self,
        ceo_agent: CEOAgent,
        financial_analyst: FinancialAnalystAgent,
        loop_runner: SelfSustainingLoopRunner,
        work_order_repo: Optional[WorkOrderRepository] = None,
        min_margin_ratio: float = 0.15,
    ) -> None:
        self.ceo_agent = ceo_agent
        self.financial_analyst = financial_analyst
        self.loop_runner = loop_runner
        self.work_order_repo = work_order_repo
        self.min_margin_ratio = min_margin_ratio

    def evaluate_candidates(
        self,
        work_orders: List[WorkOrder],
        current_treasury_usdg: int,
        current_orbio_credits: int,
    ) -> Dict[str, UnitEconomicsEvaluation]:
        """Evaluate unit economics for a list of candidate WorkOrders."""
        evaluations: Dict[str, UnitEconomicsEvaluation] = {}
        for wo in work_orders:
            eval_result = self.financial_analyst.evaluate_unit_economics(
                work_order=wo,
                current_treasury_usdg=current_treasury_usdg,
                current_orbio_credits=current_orbio_credits,
                min_margin_ratio=self.min_margin_ratio,
            )
            evaluations[wo.work_order_id] = eval_result
        return evaluations

    def select_and_coordinate(
        self,
        mission_id: str,
        work_orders: List[WorkOrder],
        current_treasury_usdg: int,
        current_orbio_credits: int,
        producer_agent_id: str,
        purchase_loop: Optional[OrbioPurchaseAgentLoop] = None,
        parent_mission_id: Optional[str] = None,
        funding_source: str = "ORGANISATION_TREASURY",
        funding_amount_usdg: int = 0,
    ) -> WorkOrderExecutionOutcome:
        """Evaluate, select best viable candidate via CEO, and execute with full persistence and audit."""
        evaluations = self.evaluate_candidates(
            work_orders=work_orders,
            current_treasury_usdg=current_treasury_usdg,
            current_orbio_credits=current_orbio_credits,
        )

        selected_order = self.ceo_agent.select_best_work_order(
            work_orders=work_orders,
            evaluations=evaluations,
        )

        if not selected_order:
            # All candidates unviable or rejected
            first_wo = work_orders[0] if work_orders else None
            first_eval = (
                evaluations[first_wo.work_order_id]
                if first_wo and first_wo.work_order_id in evaluations
                else UnitEconomicsEvaluation(
                    viable=False,
                    margin_ratio=0.0,
                    projected_direct_cost_usdg=0,
                    expected_surplus_usdg=0,
                    reason="NO_VIABLE_WORK_ORDERS",
                )
            )
            wo_id = first_wo.work_order_id if first_wo else "none"
            if first_wo:
                first_wo.transition_to(WorkOrderStatus.REJECTED)
                if self.work_order_repo:
                    self.work_order_repo.save_work_order(first_wo)

            return WorkOrderExecutionOutcome(
                mission_id=mission_id,
                work_order_id=wo_id,
                status=WorkOrderStatus.REJECTED,
                evaluation=first_eval,
                deliverable=None,
                receipt=None,
                revenue_event=None,
                direct_expense_usdg=0,
                net_surplus_usdg=0,
                allocated_to_mission_budget=0,
                allocated_to_reserve=0,
                success=False,
                error_message=first_eval.reason,
            )

        # Coordinate selected order
        return self.coordinate_single_order(
            mission_id=mission_id,
            work_order=selected_order,
            evaluation=evaluations[selected_order.work_order_id],
            producer_agent_id=producer_agent_id,
            purchase_loop=purchase_loop,
            parent_mission_id=parent_mission_id,
            funding_source=funding_source,
            funding_amount_usdg=funding_amount_usdg,
        )

    def coordinate_single_order(
        self,
        mission_id: str,
        work_order: WorkOrder,
        evaluation: UnitEconomicsEvaluation,
        producer_agent_id: str,
        purchase_loop: Optional[OrbioPurchaseAgentLoop] = None,
        parent_mission_id: Optional[str] = None,
        funding_source: str = "ORGANISATION_TREASURY",
        funding_amount_usdg: int = 0,
    ) -> WorkOrderExecutionOutcome:
        """Coordinate a single verified viable WorkOrder through the complete execution pipeline."""
        tenant_id = work_order.tenant_id
        org_id = work_order.organisation_id

        # 1. Check viability
        if not evaluation.viable:
            work_order.transition_to(WorkOrderStatus.REJECTED)
            if self.work_order_repo:
                self.work_order_repo.save_work_order(work_order)
            return WorkOrderExecutionOutcome(
                mission_id=mission_id,
                work_order_id=work_order.work_order_id,
                status=WorkOrderStatus.REJECTED,
                evaluation=evaluation,
                deliverable=None,
                receipt=None,
                revenue_event=None,
                direct_expense_usdg=0,
                net_surplus_usdg=0,
                allocated_to_mission_budget=0,
                allocated_to_reserve=0,
                success=False,
                error_message=evaluation.reason,
            )

        # 2. Authorize
        work_order.transition_to(WorkOrderStatus.AUTHORIZED)
        if self.work_order_repo:
            self.work_order_repo.save_work_order(work_order)
            if parent_mission_id or funding_amount_usdg > 0:
                self.work_order_repo.record_mission_lineage(
                    tenant_id=tenant_id,
                    organisation_id=org_id,
                    mission_id=mission_id,
                    parent_mission_id=parent_mission_id,
                    funding_source=funding_source,
                    funding_amount_usdg=funding_amount_usdg,
                )

        # 3. Execute via SelfSustainingLoopRunner
        loop_res: MissionExecutionResult = self.loop_runner.execute_mission(
            mission_id=mission_id,
            work_order=work_order,
            producer_agent_id=producer_agent_id,
            purchase_loop=purchase_loop,
        )

        # 4. Durable persistence
        if self.work_order_repo:
            if loop_res.deliverable:
                self.work_order_repo.save_deliverable(
                    tenant_id=tenant_id,
                    organisation_id=org_id,
                    deliverable=loop_res.deliverable,
                )
            if loop_res.receipt:
                self.work_order_repo.save_receipt(
                    tenant_id=tenant_id,
                    organisation_id=org_id,
                    receipt=loop_res.receipt,
                )
            if loop_res.revenue_event:
                self.work_order_repo.save_revenue_event(
                    tenant_id=tenant_id,
                    organisation_id=org_id,
                    event=loop_res.revenue_event,
                )
            self.work_order_repo.save_work_order(work_order)

        final_status = work_order.status
        return WorkOrderExecutionOutcome(
            mission_id=mission_id,
            work_order_id=work_order.work_order_id,
            status=final_status,
            evaluation=evaluation,
            deliverable=loop_res.deliverable,
            receipt=loop_res.receipt,
            revenue_event=loop_res.revenue_event,
            direct_expense_usdg=loop_res.direct_expense_usdg,
            net_surplus_usdg=loop_res.net_surplus_usdg,
            allocated_to_mission_budget=loop_res.allocated_to_next_mission,
            allocated_to_reserve=loop_res.allocated_to_reserve,
            success=loop_res.success,
            error_message=loop_res.error_message,
        )
