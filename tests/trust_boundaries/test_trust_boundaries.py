"""Adversarial Trust Boundary Tests for Phase 17."""

import pytest
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, OrgState, PolicyResult
from src.domain.work_order import WorkOrder, WorkDeliverable
from src.economy.ledger import DoubleEntryLedger, ESCROW, TREASURY
from src.economy.surplus_accounting import SurplusReconciler
from src.governance.capability_policy import CapabilityEvolutionRule
from src.persistence.database import Database
from src.persistence.marketplace_repository import MarketplaceRepository
from src.settlement.work_verifier import WorkDeliverableVerifier


def test_agent_cannot_self_grant_capability():
    """Trust Boundary 1: Agents cannot self-grant privileged capabilities."""
    rule = CapabilityEvolutionRule(min_performance_threshold=0.80)
    agent = AgentRecord(id="agent-rogue", organisation_id="org-rogue", role=AgentRole.RESEARCHER)
    org = Organisation(id="org-rogue", mission="Rogue Mission", state=OrgState.PLANNING)

    proposal = ActionProposal(
        id="prop-rogue",
        task_id="task-rogue",
        proposing_agent_id="agent-rogue",
        action_type=ActionType.PROPOSE_CAPABILITY_EXPANSION,
        target="capability://TREASURY_TRANSFER",
        parameters={
            "target_agent_id": "agent-rogue",
            "proposer_agent_id": "agent-rogue",
            "requested_capability": "TREASURY_TRANSFER",
            "performance_score": 99.9,
            "supervisor_approved": False,
        },
        requested_credits=0,
        expected_value_score=0.9,
        risk_assessment="Extreme",
        rationale="Self grant attempt",
    )

    rejection = rule.evaluate(proposal, agent, org)
    assert rejection is not None
    assert "cannot self-grant capabilities" in rejection


def test_unverified_deliverable_cannot_release_escrow():
    """Trust Boundary 2: Corrupted or unverified deliverables cannot release escrow."""
    ledger = DoubleEntryLedger(initial_treasury=100)
    secret = "real-auditor-secret-key"
    reconciler = SurplusReconciler(ledger=ledger, receipt_secret_key=secret)
    verifier = WorkDeliverableVerifier(secret_key=secret)

    wo = WorkOrder(
        tenant_id="t1", organisation_id="o1", work_order_id="wo-1", client_id="c1",
        title="Critical Task", description="Desc", deliverable_type="SECURITY_AUDIT",
        required_orbio_credits=10, bounty_amount=50,
    )

    failing_deliverable = WorkDeliverable.create(
        work_order_id="wo-1",
        producer_agent_id="agent-worker",
        content_payload={"findings": [{"severity": "CRITICAL", "description": "Critical vulnerability detected"}]},
        orbio_credits_consumed=10,
    )

    receipt = verifier.verify(wo, failing_deliverable)
    assert not receipt.is_verified()

    with pytest.raises(ValueError, match="Cannot reconcile unverified deliverable receipt"):
        reconciler.reconcile_surplus(wo, receipt, gross_revenue_usdg=50, direct_expense_usdg=0, orbio_credits_consumed=10)


def test_cross_tenant_isolation_in_marketplace():
    """Trust Boundary 3: Cross-tenant data isolation."""
    db = Database(":memory:")
    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("tenant-isolated-1", "Tenant Isolated 1", "active"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("tenant-isolated-2", "Tenant Isolated 2", "active"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-isolated-alpha", "tenant-isolated-1", "Defense Analysis", 100, "PLANNING"),
        )
    repo = MarketplaceRepository(db)

    from src.domain.marketplace import MarketplaceOrder
    order1 = MarketplaceOrder.create(
        tenant_id="tenant-isolated-1",
        organisation_id="org-isolated-alpha",
        order_id="order-alpha-1",
        title="Confidential Defense Analysis",
        description="Private specification details",
        required_capability="ADVANCED_ANALYTICS",
        bounty_amount=500,
    )
    repo.create_order(order1)

    cross_tenant_fetch = repo.get_order("tenant-isolated-2", "org-isolated-alpha", "order-alpha-1")
    assert cross_tenant_fetch is None


def test_escrow_double_spending_prevented():
    """Trust Boundary 4: Escrow reservation prevents double-spending."""
    ledger = DoubleEntryLedger(initial_treasury=100)
    ledger.transfer(from_account=TREASURY, to_account=ESCROW, amount=80, memo="Lock 1")
    assert ledger.get_balance(TREASURY) == 20

    from src.domain.exceptions import InsufficientCreditsError
    with pytest.raises(InsufficientCreditsError):
        ledger.transfer(from_account=TREASURY, to_account=ESCROW, amount=50, memo="Lock 2")


def test_provenance_truthfulness_flag():
    """Trust Boundary 5: Simulated executions are never represented as live."""
    from src.execution.work_executor import SimulatedWorkExecutor
    from src.execution.orbio_gateway_adapter import OrbioGatewayAdapter

    sim_executor = SimulatedWorkExecutor(credit_store={"org-test": 5000})
    adapter = OrbioGatewayAdapter(fallback_executor=sim_executor, credit_store={"org-test": 5000})

    wo = WorkOrder(
        tenant_id="t1", organisation_id="org-test", work_order_id="wo-telemetry",
        client_id="c1", title="Telemetry Verification", description="Desc",
        deliverable_type="SECURITY_AUDIT", required_orbio_credits=100, bounty_amount=50,
    )

    deliv = adapter.execute_work(wo, "agent-test", "org-test")
    assert "is_simulated" in deliv.execution_telemetry
    assert deliv.execution_telemetry["is_simulated"] is True
    assert deliv.execution_telemetry["executor"] in ("SimulatedWorkExecutor", "OrbioGatewayAdapter")
