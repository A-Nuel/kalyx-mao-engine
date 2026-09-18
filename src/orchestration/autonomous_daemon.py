"""Continuous Autonomous Daemon & Solvency Regimes — Phase 16 Milestone 5.

Governs multi-cycle operations with dynamic solvency regimes:
- EXPANSION (Treasury >= 150 USDG): Aggressive reinvestment (80% mission budget / 20% reserve), 15% min margin.
- AUSTERE (40 <= Treasury < 150 USDG): Defensive posture (50% mission budget / 50% reserve), 25% min margin.
- STANDBY (Treasury < 40 USDG): Capital preservation, halts autonomous purchases until refueled.

Maintains strict ledger invariants and recursive lineage across execution cycles.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.agents.orbio_purchase_loop import OrbioPurchaseAgentLoop
from src.agents.work_order_coordinator import WorkOrderCoordinator, WorkOrderExecutionOutcome
from src.domain.enums import SolvencyRegime, WorkOrderStatus
from src.domain.work_order import WorkOrder
from src.economy.ledger import DoubleEntryLedger, TREASURY
from src.persistence.work_order_repository import WorkOrderRepository

logger = logging.getLogger(__name__)


@dataclass
class RegimePolicy:
    regime: SolvencyRegime
    min_margin_ratio: float
    reserve_ratio: float
    allow_credit_acquisition: bool


REGIME_POLICIES: Dict[SolvencyRegime, RegimePolicy] = {
    SolvencyRegime.EXPANSION: RegimePolicy(
        regime=SolvencyRegime.EXPANSION,
        min_margin_ratio=0.15,
        reserve_ratio=0.20,
        allow_credit_acquisition=True,
    ),
    SolvencyRegime.AUSTERE: RegimePolicy(
        regime=SolvencyRegime.AUSTERE,
        min_margin_ratio=0.25,
        reserve_ratio=0.50,
        allow_credit_acquisition=True,
    ),
    SolvencyRegime.STANDBY: RegimePolicy(
        regime=SolvencyRegime.STANDBY,
        min_margin_ratio=0.50,
        reserve_ratio=1.00,
        allow_credit_acquisition=False,
    ),
}


def derive_solvency_regime(
    treasury_balance: int,
    expansion_threshold: int = 150,
    standby_threshold: int = 40,
) -> SolvencyRegime:
    """Deterministically derive solvency regime from treasury balance."""
    if treasury_balance >= expansion_threshold:
        return SolvencyRegime.EXPANSION
    elif treasury_balance >= standby_threshold:
        return SolvencyRegime.AUSTERE
    return SolvencyRegime.STANDBY


@dataclass
class DaemonCycleResult:
    cycle_number: int
    regime: SolvencyRegime
    mission_id: Optional[str]
    work_order_id: Optional[str]
    outcome: Optional[WorkOrderExecutionOutcome]
    treasury_balance_before: int
    treasury_balance_after: int
    state_summary: str


class AutonomousDaemon:
    """
    Continuous Autonomous Operations Daemon.
    
    Executes sequential autonomous cycles, adapting execution parameters
    based on the organisation's real-time financial solvency regime.
    """

    def __init__(
        self,
        tenant_id: str,
        organisation_id: str,
        coordinator: WorkOrderCoordinator,
        ledger: DoubleEntryLedger,
        work_order_repo: Optional[WorkOrderRepository] = None,
        expansion_threshold: int = 150,
        standby_threshold: int = 40,
        purchase_loop_factory: Optional[Any] = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.organisation_id = organisation_id
        self.coordinator = coordinator
        self.ledger = ledger
        self.work_order_repo = work_order_repo
        self.expansion_threshold = expansion_threshold
        self.standby_threshold = standby_threshold
        self.purchase_loop_factory = purchase_loop_factory

        self.total_revenue_earned_usdg = 0
        self.total_compute_consumed_credits = 0
        self.history: List[DaemonCycleResult] = []
        if self.work_order_repo:
            saved = self.work_order_repo.get_daemon_state(self.tenant_id, self.organisation_id)
            self.cycle_count = saved.get("cycle_count", 0)
            self._last_mission_id = saved.get("last_mission_id")
        else:
            self.cycle_count = 0
            self._last_mission_id = None

    def _persist_state(self) -> None:
        if self.work_order_repo:
            self.work_order_repo.save_daemon_state(
                tenant_id=self.tenant_id,
                organisation_id=self.organisation_id,
                cycle_count=self.cycle_count,
                last_mission_id=self._last_mission_id,
            )

    def get_current_regime(self) -> SolvencyRegime:
        treasury_balance = self.ledger.get_balance(TREASURY)
        return derive_solvency_regime(
            treasury_balance,
            expansion_threshold=self.expansion_threshold,
            standby_threshold=self.standby_threshold,
        )

    def step_cycle(
        self,
        candidate_work_orders: Optional[List[WorkOrder]] = None,
        producer_agent_id: str = "agent-operator",
    ) -> DaemonCycleResult:
        self.cycle_count += 1
        treasury_before = self.ledger.get_balance(TREASURY)
        regime = derive_solvency_regime(
            treasury_before,
            expansion_threshold=self.expansion_threshold,
            standby_threshold=self.standby_threshold,
        )
        regime_policy = REGIME_POLICIES[regime]

        # In STANDBY: halt consequential spending and skip execution
        if regime == SolvencyRegime.STANDBY:
            summary = (
                f"Cycle {self.cycle_count} halted: STANDBY regime active "
                f"(Treasury: {treasury_before} USDG < {self.standby_threshold} USDG threshold). "
                "Preserving remaining capital."
            )
            logger.warning(summary)
            self._persist_state()
            result = DaemonCycleResult(
                cycle_number=self.cycle_count,
                regime=regime,
                mission_id=None,
                work_order_id=None,
                outcome=None,
                treasury_balance_before=treasury_before,
                treasury_balance_after=treasury_before,
                state_summary=summary,
            )
            self.history.append(result)
            return result

        # Retrieve candidates if not passed
        candidates = candidate_work_orders or []
        if not candidates and self.work_order_repo:
            candidates = self.work_order_repo.list_work_orders(
                tenant_id=self.tenant_id,
                organisation_id=self.organisation_id,
                status=WorkOrderStatus.PROPOSED,
            )

        if not candidates:
            summary = f"Cycle {self.cycle_count}: No candidate work orders available."
            self._persist_state()
            result = DaemonCycleResult(
                cycle_number=self.cycle_count,
                regime=regime,
                mission_id=None,
                work_order_id=None,
                outcome=None,
                treasury_balance_before=treasury_before,
                treasury_balance_after=treasury_before,
                state_summary=summary,
            )
            self.history.append(result)
            return result

        # Dynamically configure coordinator thresholds based on solvency regime
        self.coordinator.min_margin_ratio = regime_policy.min_margin_ratio
        self.coordinator.loop_runner.reserve_ratio = regime_policy.reserve_ratio
        self.coordinator.loop_runner.surplus_reconciler.default_reserve_ratio = regime_policy.reserve_ratio

        mission_id = f"mission-cycle-{self.cycle_count}-{uuid.uuid4().hex[:6]}"
        parent_id = self._last_mission_id

        # Query available compute credits
        current_credits = 0
        if hasattr(self.coordinator.loop_runner.work_executor, "get_credit_balance"):
            current_credits = self.coordinator.loop_runner.work_executor.get_credit_balance(self.organisation_id)
        elif self.coordinator.loop_runner.exchange_provider:
            current_credits = self.coordinator.loop_runner.exchange_provider.get_credit_balance(self.organisation_id)

        # Purchase loop creation if factory supplied
        purchase_loop = None
        if self.purchase_loop_factory and regime_policy.allow_credit_acquisition:
            target_needed = max([c.required_orbio_credits for c in candidates], default=1_000_000)
            try:
                purchase_loop = self.purchase_loop_factory(mission_id, target_needed)
            except TypeError:
                purchase_loop = self.purchase_loop_factory(mission_id)

        outcome = self.coordinator.select_and_coordinate(
            mission_id=mission_id,
            work_orders=candidates,
            current_treasury_usdg=treasury_before,
            current_orbio_credits=current_credits,
            producer_agent_id=producer_agent_id,
            purchase_loop=purchase_loop,
            parent_mission_id=parent_id,
            funding_source="PRIOR_SURPLUS" if parent_id else "TREASURY_RESERVE",
            funding_amount_usdg=treasury_before,
        )

        treasury_after = self.ledger.get_balance(TREASURY)

        if outcome.success:
            self._last_mission_id = mission_id
            if outcome.revenue_event:
                self.total_revenue_earned_usdg += outcome.revenue_event.gross_revenue_usdg
                self.total_compute_consumed_credits += outcome.revenue_event.orbio_credits_consumed
            summary = (
                f"Cycle {self.cycle_count} [{regime.value}] SUCCESS: Executed {outcome.work_order_id}, "
                f"earned {outcome.net_surplus_usdg} USDG net surplus. "
                f"Treasury updated: {treasury_before} -> {treasury_after} USDG."
            )
        else:
            summary = (
                f"Cycle {self.cycle_count} [{regime.value}] REJECTED/FAILED: {outcome.error_message}. "
                f"Treasury preserved at {treasury_after} USDG."
            )

        self._persist_state()

        result = DaemonCycleResult(
            cycle_number=self.cycle_count,
            regime=regime,
            mission_id=mission_id,
            work_order_id=outcome.work_order_id,
            outcome=outcome,
            treasury_balance_before=treasury_before,
            treasury_balance_after=treasury_after,
            state_summary=summary,
        )
        self.history.append(result)
        return result

    def run_cycles(
        self,
        max_cycles: int,
        candidate_batches: Optional[List[List[WorkOrder]]] = None,
        producer_agent_id: str = "agent-operator",
    ) -> List[DaemonCycleResult]:
        """Run multiple autonomous cycles sequentially."""
        results: List[DaemonCycleResult] = []
        for i in range(max_cycles):
            batch = candidate_batches[i] if candidate_batches and i < len(candidate_batches) else None
            res = self.step_cycle(candidate_work_orders=batch, producer_agent_id=producer_agent_id)
            results.append(res)
            # If entered STANDBY, stop autonomous expansion
            if res.regime == SolvencyRegime.STANDBY:
                break
        return results
