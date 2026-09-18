"""B2B Marketplace Coordinator for Phase 17 Inter-DAO Commerce."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.domain.capability import CapabilityProposal
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, CurrencyAsset, DeliverableStatus, PolicyResult
from src.domain.marketplace import (
    EscrowAgreement,
    EscrowStatus,
    MarketplaceOrder,
    MarketplaceOrderStatus,
)
from src.domain.work_order import (
    RevenueEvent,
    WorkDeliverable,
    WorkDeliverableReceipt,
    WorkOrder,
)
from src.economy.ledger import DoubleEntryLedger, ESCROW, EXTERNAL_SINK, TREASURY
from src.economy.surplus_accounting import SurplusReconciler
from src.execution.work_executor import BaseWorkExecutor
from src.governance.circuit_breaker import SystemCircuitBreaker
from src.governance.policy_engine import PolicyEngine
from src.orchestration.capability_manager import CapabilityManager
from src.persistence.marketplace_repository import MarketplaceRepository
from src.persistence.work_order_repository import WorkOrderRepository
from src.settlement.work_verifier import WorkDeliverableVerifier

logger = logging.getLogger(__name__)


@dataclass
class B2BSettlementOutcome:
    order_id: str
    work_order_id: str
    deliverable: WorkDeliverable
    receipt: WorkDeliverableReceipt
    revenue_event: RevenueEvent
    net_surplus_usdg: int
    allocated_to_mission_budget: int
    allocated_to_reserve: int
    client_escrow_tx_id: str
    provider_revenue_tx_id: str
    is_simulated: bool
    success: bool
    error_message: Optional[str] = None


class B2BMarketplaceCoordinator:
    """Coordinates cross-organisation B2B commerce with dual-sided escrow and capability evolution."""

    def __init__(
        self,
        marketplace_repo: MarketplaceRepository,
        work_order_repo: Optional[WorkOrderRepository] = None,
        receipt_secret_key: str = "kalyx-phase17-secret-key-42",
        circuit_breaker: Optional[SystemCircuitBreaker] = None,
    ) -> None:
        self.marketplace_repo = marketplace_repo
        self.work_order_repo = work_order_repo
        self.receipt_secret_key = receipt_secret_key
        self.circuit_breaker = circuit_breaker

    def publish_b2b_order(
        self,
        client_tenant_id: str,
        client_org_id: str,
        title: str,
        description: str,
        required_capability: str,
        bounty_amount: int,
        client_ledger: DoubleEntryLedger,
        policy_engine: PolicyEngine,
        client_agent: AgentRecord,
        client_org: Organisation,
        sla_timeout_seconds: int = 3600,
        specification_payload: Optional[Dict[str, Any]] = None,
    ) -> MarketplaceOrder:
        """
        Client Org publishes a public B2B order:
        1. Check authoritative circuit breaker state
        2. Propose PUBLISH_MARKETPLACE_ORDER
        3. Policy validates treasury balance
        4. Ledger locks funds: TREASURY -> ESCROW
        5. Persist order and escrow agreement
        """
        # Authoritative circuit breaker check
        if self.circuit_breaker is not None and self.circuit_breaker.is_paused(client_tenant_id, client_org_id):
            raise PermissionError(f"Organisation '{client_org_id}' circuit breaker is PAUSED: new order publication blocked")

        order_id = f"mkt-order-{uuid.uuid4().hex[:8]}"

        # 1. Propose & Policy evaluate
        proposal = ActionProposal(
            id=f"prop-{uuid.uuid4().hex[:8]}",
            task_id=f"task-mkt-{uuid.uuid4().hex[:6]}",
            proposing_agent_id=client_agent.id,
            action_type=ActionType.PUBLISH_MARKETPLACE_ORDER,
            target=f"marketplace://order/{order_id}",
            parameters={
                "bounty_amount": bounty_amount,
                "required_capability": required_capability,
            },
            requested_credits=bounty_amount,
            expected_value_score=0.9,
            risk_assessment="Low risk B2B bounty publication",
            rationale=f"Publishing B2B work order: {title}",
        )

        if client_agent.id not in client_org.agents:
            client_org.agents[client_agent.id] = client_agent

        decision = policy_engine.evaluate(proposal, client_org, ledger=client_ledger)
        if decision.result != PolicyResult.APPROVED:
            reason = decision.violated_rule_description or decision.violated_rule_id or str(decision.result)
            raise PermissionError(f"Policy rejected B2B order publication: {reason}")

        # 2. Lock escrow in Client Org Ledger: TREASURY -> ESCROW
        client_tx_id = f"escrow-lock-{order_id}"
        client_ledger.transfer(
            from_account=TREASURY,
            to_account=ESCROW,
            amount=bounty_amount,
            memo=f"Escrow lock for B2B Order {order_id}",
            transaction_id=client_tx_id,
        )

        # 3. Create Marketplace Order & Escrow Agreement
        order = MarketplaceOrder.create(
            tenant_id=client_tenant_id,
            organisation_id=client_org_id,
            order_id=order_id,
            title=title,
            description=description,
            required_capability=required_capability,
            bounty_amount=bounty_amount,
            bounty_asset="USDG",
            sla_timeout_seconds=sla_timeout_seconds,
            specification_payload=specification_payload,
        )

        escrow_id = f"escrow-{uuid.uuid4().hex[:8]}"
        escrow = EscrowAgreement(
            tenant_id=client_tenant_id,
            organisation_id=client_org_id,
            escrow_id=escrow_id,
            order_id=order_id,
            client_tenant_id=client_tenant_id,
            client_org_id=client_org_id,
            bounty_amount=bounty_amount,
            bounty_asset="USDG",
            status=EscrowStatus.HELD,
            client_ledger_tx_id=client_tx_id,
        )

        self.marketplace_repo.create_order(order)
        self.marketplace_repo.save_escrow(escrow)
        return order

    publish_order = publish_b2b_order

    def discover_and_claim(
        self,
        provider_tenant_id: str,
        provider_org_id: str,
        provider_agent: AgentRecord,
        provider_org: Organisation,
        capability_manager: CapabilityManager,
        policy_engine: PolicyEngine,
        target_order_id: Optional[str] = None,
    ) -> Optional[MarketplaceOrder]:
        """
        Provider Org discovers an open order and claims it:
        1. Check authoritative circuit breaker state
        2. Identifies eligible order from public board
        3. Checks capability; if missing, triggers governed capability evolution proposal
        4. Claims order atomically
        """
        # Authoritative circuit breaker check
        if self.circuit_breaker is not None and self.circuit_breaker.is_paused(provider_tenant_id, provider_org_id):
            raise PermissionError(f"Organisation '{provider_org_id}' circuit breaker is PAUSED: order claim blocked")

        public_orders = self.marketplace_repo.list_public_orders(status="OPEN")
        eligible_order: Optional[MarketplaceOrder] = None

        for pub in public_orders:
            if target_order_id and pub.order_id != target_order_id:
                continue
            # Fetch authoritative full order to check tenant/org ownership
            full_order = self.marketplace_repo.get_order_by_id(pub.order_id)
            if not full_order:
                continue
            # Do not self-fulfill own order
            if full_order.tenant_id == provider_tenant_id and full_order.organisation_id == provider_org_id:
                continue
            eligible_order = full_order
            break

        if not eligible_order:
            return None

        # Check agent capability
        has_cap = capability_manager.check_agent_capability(
            provider_tenant_id, provider_org_id, provider_agent.id, eligible_order.required_capability
        )

        if not has_cap:
            # Check empirical performance score
            score = getattr(provider_agent, "performance_score", 0.0)
            normalized_score = score / 100.0 if score > 1.0 else score
            if normalized_score >= 0.80:
                # Propose governed capability expansion
                proposal = CapabilityProposal(
                    tenant_id=provider_tenant_id,
                    organisation_id=provider_org_id,
                    proposal_id=f"prop-cap-{uuid.uuid4().hex[:6]}",
                    target_agent_id=provider_agent.id,
                    proposer_agent_id=f"{provider_org_id}-ceo",
                    requested_capability=eligible_order.required_capability,
                    requested_permission="EXECUTE_ADVANCED",
                    current_performance_score=score,
                    rationale=f"Empirical score {score:.2f} qualifies agent for {eligible_order.required_capability}",
                )
                # Evaluated by Policy Engine
                supervisor = AgentRecord(
                    id=f"{provider_org_id}-ceo",
                    organisation_id=provider_org_id,
                    role="CEO",
                    allowed_action_types=[ActionType.PROPOSE_CAPABILITY_EXPANSION],
                )
                if supervisor.id not in provider_org.agents:
                    provider_org.agents[supervisor.id] = supervisor
                if provider_agent.id not in provider_org.agents:
                    provider_org.agents[provider_agent.id] = provider_agent

                grant = capability_manager.evaluate_and_grant(
                    proposal=proposal,
                    supervisor_agent=supervisor,
                    target_agent=provider_agent,
                    organisation=provider_org,
                )
                if not grant:
                    logger.warning(f"Capability grant rejected for agent {provider_agent.id}")
                    return None
            else:
                logger.info(f"Agent {provider_agent.id} score {score} insufficient for {eligible_order.required_capability}")
                return None

        # Claim the order
        work_order_id = f"wo-b2b-{eligible_order.order_id}"
        success = self.marketplace_repo.claim_order(
            tenant_id=eligible_order.tenant_id,
            organisation_id=eligible_order.organisation_id,
            order_id=eligible_order.order_id,
            claimed_by_tenant_id=provider_tenant_id,
            claimed_by_org_id=provider_org_id,
            claimed_by_agent_id=provider_agent.id,
            work_order_id=work_order_id,
        )

        if not success:
            return None

        # Return updated order
        return self.marketplace_repo.get_order(
            eligible_order.tenant_id, eligible_order.organisation_id, eligible_order.order_id
        )

    def execute_and_settle(
        self,
        order: MarketplaceOrder,
        provider_tenant_id: str,
        provider_org_id: str,
        producer_agent_id: str,
        work_executor: BaseWorkExecutor,
        work_verifier: WorkDeliverableVerifier,
        client_ledger: DoubleEntryLedger,
        provider_ledger: DoubleEntryLedger,
        surplus_reconciler: SurplusReconciler,
    ) -> B2BSettlementOutcome:
        """
        Executes productive work, verifies deliverable, and settles escrow:
        1. Preflight validations (status, authorized claimant, escrow state, capability)
        2. Synthesize WorkOrder
        3. Execute work via work_executor (captures live vs sim telemetry)
        4. Verify deliverable independently via work_verifier
        5. Settle escrow:
           - Client Org: ESCROW -> EXTERNAL_SINK
           - Provider Org: REVENUE (deposit_revenue)
        6. Reconcile surplus via SurplusReconciler
        """
        # 0. Preflight checks
        if order.status != MarketplaceOrderStatus.CLAIMED:
            raise ValueError(
                f"Marketplace order '{order.order_id}' cannot be executed: status is '{order.status}', must be CLAIMED."
            )

        if order.claimed_by_org_id != provider_org_id or order.claimed_by_agent_id != producer_agent_id:
            raise PermissionError(
                f"Caller ({provider_org_id}, {producer_agent_id}) is not the authorized claimant for order '{order.order_id}' "
                f"(claimed by: {order.claimed_by_org_id}, {order.claimed_by_agent_id})"
            )

        escrow = self.marketplace_repo.get_escrow_by_order(order.order_id)
        if not escrow or escrow.status != EscrowStatus.HELD:
            raise ValueError(
                f"Active escrow not found or not HELD for order '{order.order_id}' "
                f"(current status: {escrow.status if escrow else 'None'})"
            )

        # Validate producer agent holds active, unrevoked capability
        has_cap = self.marketplace_repo.has_capability(
            provider_tenant_id, provider_org_id, producer_agent_id, order.required_capability
        )
        if not has_cap:
            raise PermissionError(
                f"Producer agent '{producer_agent_id}' does not hold active, unrevoked capability '{order.required_capability}'"
            )

        work_order_id = order.work_order_id or f"wo-b2b-{order.order_id}"
        work_order = WorkOrder(
            tenant_id=provider_tenant_id,
            organisation_id=provider_org_id,
            work_order_id=work_order_id,
            client_id=order.organisation_id,
            title=order.title,
            description=order.description,
            deliverable_type="B2B_DELIVERABLE",
            required_orbio_credits=1000,
            bounty_amount=order.bounty_amount,
            bounty_asset=CurrencyAsset.USDG,
        )

        if self.work_order_repo:
            self.work_order_repo.save_work_order(work_order)

        # 1. Productive Execution
        deliverable = work_executor.execute_work(
            work_order=work_order,
            producer_agent_id=producer_agent_id,
            organisation_id=provider_org_id,
        )

        is_simulated = bool(
            deliverable.execution_telemetry.get("is_simulated", True)
            if isinstance(deliverable.execution_telemetry, dict)
            else True
        )

        if self.work_order_repo:
            self.work_order_repo.save_deliverable(provider_tenant_id, provider_org_id, deliverable)

        # 2. Independent Verification
        receipt = work_verifier.verify(
            work_order=work_order,
            deliverable=deliverable,
        )

        if self.work_order_repo:
            self.work_order_repo.save_receipt(provider_tenant_id, provider_org_id, receipt)

        if not receipt.is_verified():
            raise ValueError(f"B2B Deliverable failed independent verification: {receipt.verification_notes}")

        # 3. Dual-Sided Escrow Settlement
        client_release_tx_id = f"escrow-release-{order.order_id}"
        client_ledger.transfer(
            from_account=ESCROW,
            to_account=EXTERNAL_SINK,
            amount=order.bounty_amount,
            memo=f"Escrow released to provider {provider_org_id} for order {order.order_id}",
            transaction_id=client_release_tx_id,
        )

        provider_rev_tx_id = f"revenue-b2b-{order.order_id}"
        provider_ledger.deposit_revenue(
            amount=order.bounty_amount,
            memo=f"B2B revenue from client {order.organisation_id} for order {order.order_id}",
            transaction_id=provider_rev_tx_id,
        )

        # 4. Surplus Reconciliation (Deterministic calculation)
        revenue_event = surplus_reconciler.reconcile_surplus(
            work_order=work_order,
            receipt=receipt,
            gross_revenue_usdg=order.bounty_amount,
            direct_expense_usdg=0,  # B2B revenue has 0 direct purchase deduction in this flow
            orbio_credits_consumed=deliverable.orbio_credits_consumed,
        )

        if self.work_order_repo:
            self.work_order_repo.save_revenue_event(provider_tenant_id, provider_org_id, revenue_event)

        # 5. Update Marketplace Persistence
        self.marketplace_repo.update_escrow_status(
            tenant_id=escrow.tenant_id,
            organisation_id=escrow.organisation_id,
            escrow_id=escrow.escrow_id,
            status=EscrowStatus.RELEASED,
            provider_tenant_id=provider_tenant_id,
            provider_org_id=provider_org_id,
            provider_ledger_tx_id=provider_rev_tx_id,
        )

        self.marketplace_repo.update_order_status(
            tenant_id=order.tenant_id,
            organisation_id=order.organisation_id,
            order_id=order.order_id,
            status=MarketplaceOrderStatus.COMPLETED,
            deliverable_id=deliverable.deliverable_id,
        )

        return B2BSettlementOutcome(
            order_id=order.order_id,
            work_order_id=work_order_id,
            deliverable=deliverable,
            receipt=receipt,
            revenue_event=revenue_event,
            net_surplus_usdg=revenue_event.net_surplus_usdg,
            allocated_to_mission_budget=revenue_event.allocated_to_mission_budget,
            allocated_to_reserve=revenue_event.allocated_to_reserve,
            client_escrow_tx_id=client_release_tx_id,
            provider_revenue_tx_id=provider_rev_tx_id,
            is_simulated=is_simulated,
            success=True,
        )

    def refund_escrow(
        self,
        tenant_id: str,
        organisation_id: str,
        order_id: str,
        client_ledger: DoubleEntryLedger,
        reason: str = "Order cancelled or expired",
    ) -> EscrowAgreement:
        """
        Refunds held escrow back to client org treasury:
        1. Validates order is OPEN or CANCELLED
        2. Validates escrow is HELD
        3. Transfers ESCROW -> TREASURY in client_ledger
        4. Updates order to CANCELLED and escrow to REFUNDED
        """
        order = self.marketplace_repo.get_order(tenant_id, organisation_id, order_id)
        if not order:
            raise ValueError(f"Marketplace order '{order_id}' not found.")

        if order.status == MarketplaceOrderStatus.COMPLETED:
            raise ValueError(f"Cannot refund escrow for completed order '{order_id}'.")

        escrow = self.marketplace_repo.get_escrow_by_order(order_id)
        if not escrow or escrow.status != EscrowStatus.HELD:
            raise ValueError(f"No escrow in HELD status found for order '{order_id}'.")

        refund_tx_id = f"escrow-refund-{order_id}"
        client_ledger.transfer(
            from_account=ESCROW,
            to_account=TREASURY,
            amount=escrow.bounty_amount,
            memo=f"Escrow refund for order {order_id}: {reason}",
            transaction_id=refund_tx_id,
        )

        self.marketplace_repo.update_escrow_status(
            tenant_id=escrow.tenant_id,
            organisation_id=escrow.organisation_id,
            escrow_id=escrow.escrow_id,
            status=EscrowStatus.REFUNDED,
        )

        self.marketplace_repo.update_order_status(
            tenant_id=order.tenant_id,
            organisation_id=order.organisation_id,
            order_id=order.order_id,
            status=MarketplaceOrderStatus.CANCELLED,
        )

        res = self.marketplace_repo.get_escrow_by_order(order_id)
        if not res:
            raise RuntimeError(f"Escrow '{order_id}' missing after refund update.")
        return res
