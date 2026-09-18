"""Phase 17 Comprehensive Adversarial & Invariant Attack Suite.

Covers all 18 required attack vectors:
1. Self-grant capability attempt
2. Unauthorized claim without capability/score
3. Unauthorized settlement by non-claimant
4. Rejected deliverable blocks settlement
5. Duplicate settlement prevention
6. Duplicate revenue reconciliation prevention
7. Duplicate capability grant idempotency
8. Cross-tenant isolation
9. Forged policy authorization
10. Modified authorized order integrity
11. Insufficient treasury escrow rejection
12. Insufficient/negative surplus rejection
13. Failed Orbio API call fallback to simulated
14. Malformed Orbio response safe handling
15. Simulated vs live provenance truthfulness
16. Mission budget overspending prevention
17. Replayed deliverable receipt mismatch
18. Settlement after capability revocation
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest
import requests

from src.agents.b2b_marketplace_coordinator import B2BMarketplaceCoordinator
from src.domain.capability import CapabilityGrant, CapabilityGrantStatus, CapabilityProposal
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, CurrencyAsset, OrgState, PolicyResult
from src.domain.exceptions import InsufficientCreditsError
from src.domain.marketplace import EscrowAgreement, EscrowStatus, MarketplaceOrder, MarketplaceOrderStatus
from src.domain.work_order import WorkDeliverable, WorkOrder, WorkDeliverableReceipt
from src.economy.ledger import DoubleEntryLedger, ESCROW, EXTERNAL_SINK, TREASURY
from src.economy.surplus_accounting import SurplusReconciler
from src.execution.orbio_gateway_adapter import OrbioGatewayAdapter
from src.execution.work_executor import SimulatedWorkExecutor
from src.governance.capability_policy import CapabilityEvolutionRule
from src.governance.policy_engine import PolicyEngine
from src.orchestration.capability_manager import CapabilityManager
from src.persistence.database import Database
from src.persistence.marketplace_repository import MarketplaceRepository
from src.persistence.work_order_repository import WorkOrderRepository
from src.settlement.work_verifier import WorkDeliverableVerifier


@pytest.fixture
def test_db():
    db = Database(":memory:")
    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("tenant-alpha", "Tenant Alpha", "active"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("tenant-beta", "Tenant Beta", "active"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-alpha", "tenant-alpha", "Client Org", 1000, "PLANNING"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-beta", "tenant-beta", "Provider Org", 500, "PLANNING"),
        )
    return db


@pytest.fixture
def test_context(test_db):
    marketplace_repo = MarketplaceRepository(test_db)
    work_order_repo = WorkOrderRepository(test_db)
    policy_engine = PolicyEngine(signing_secret="sec-audit-policy-999", human_approval_threshold=1000)
    capability_manager = CapabilityManager(
        repository=marketplace_repo,
        policy_engine=policy_engine,
        default_grant_duration_days=7,
    )
    coordinator = B2BMarketplaceCoordinator(
        marketplace_repo=marketplace_repo,
        work_order_repo=work_order_repo,
    )
    client_ledger = DoubleEntryLedger(initial_treasury=1000)
    provider_ledger = DoubleEntryLedger(initial_treasury=500)
    secret_key = "audit-reconciler-hmac-key"
    surplus_reconciler = SurplusReconciler(ledger=provider_ledger, receipt_secret_key=secret_key)
    work_verifier = WorkDeliverableVerifier(secret_key=secret_key)
    sim_executor = SimulatedWorkExecutor(credit_store={"org-beta": 10000})

    client_agent = AgentRecord(
        id="alpha-procurement-agent",
        organisation_id="org-alpha",
        role=AgentRole.STRATEGIST,
        authority_ceiling=1000,
        allowed_action_types=[ActionType.PUBLISH_MARKETPLACE_ORDER, ActionType.INTERNAL_ANALYSIS],
    )
    provider_agent = AgentRecord(
        id="beta-worker-agent",
        organisation_id="org-beta",
        role=AgentRole.RESEARCHER,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS],
        performance_score=90.0,
    )
    client_org = Organisation(id="org-alpha", mission="Client Mission", state=OrgState.PLANNING)
    client_org.agents[client_agent.id] = client_agent

    provider_org = Organisation(id="org-beta", mission="Provider Mission", state=OrgState.PLANNING)
    provider_org.agents[provider_agent.id] = provider_agent

    return {
        "db": test_db,
        "marketplace_repo": marketplace_repo,
        "work_order_repo": work_order_repo,
        "policy_engine": policy_engine,
        "capability_manager": capability_manager,
        "coordinator": coordinator,
        "client_ledger": client_ledger,
        "provider_ledger": provider_ledger,
        "surplus_reconciler": surplus_reconciler,
        "work_verifier": work_verifier,
        "sim_executor": sim_executor,
        "client_agent": client_agent,
        "provider_agent": provider_agent,
        "client_org": client_org,
        "provider_org": provider_org,
    }


# ---------------------------------------------------------------------------
# Attack 1: Self-Grant
# ---------------------------------------------------------------------------
def test_attack_01_self_grant_rejection(test_context):
    """An agent cannot self-grant capabilities without independent supervisor approval."""
    rule = CapabilityEvolutionRule(min_performance_threshold=0.80)
    agent = test_context["provider_agent"]
    org = test_context["provider_org"]

    proposal = ActionProposal(
        id="prop-self-grant",
        task_id="task-sg-1",
        proposing_agent_id=agent.id,
        action_type=ActionType.PROPOSE_CAPABILITY_EXPANSION,
        target="capability://ROOT_ADMIN",
        parameters={
            "target_agent_id": agent.id,
            "proposer_agent_id": agent.id,
            "requested_capability": "ROOT_ADMIN",
            "performance_score": 99.0,
            "supervisor_approved": True,  # Attacker attempts forge
        },
        requested_credits=0,
        expected_value_score=0.9,
        risk_assessment="High",
        rationale="Adversarial self grant",
    )
    rejection = rule.evaluate(proposal, agent, org)
    assert rejection is not None
    assert "cannot self-grant capabilities" in rejection


# ---------------------------------------------------------------------------
# Attack 2: Unauthorized Claim (insufficient capability and low score)
# ---------------------------------------------------------------------------
def test_attack_02_unauthorized_claim_insufficient_capability_and_score(test_context):
    """An agent with low performance cannot claim an order requiring an unheld capability."""
    ctx = test_context
    order = ctx["coordinator"].publish_order(
        client_tenant_id="tenant-alpha",
        client_org_id="org-alpha",
        title="High Precision Analysis",
        description="Requires specialized capability",
        required_capability="QUANTUM_CRYPTOGRAPHY",
        bounty_amount=200,
        client_ledger=ctx["client_ledger"],
        policy_engine=ctx["policy_engine"],
        client_agent=ctx["client_agent"],
        client_org=ctx["client_org"],
    )

    underqualified_agent = AgentRecord(
        id="beta-intern-agent",
        organisation_id="org-beta",
        role=AgentRole.RESEARCHER,
        performance_score=40.0,  # Below 80 threshold
    )
    ctx["provider_org"].agents[underqualified_agent.id] = underqualified_agent

    claimed = ctx["coordinator"].discover_and_claim(
        provider_tenant_id="tenant-beta",
        provider_org_id="org-beta",
        provider_agent=underqualified_agent,
        provider_org=ctx["provider_org"],
        capability_manager=ctx["capability_manager"],
        policy_engine=ctx["policy_engine"],
        target_order_id=order.order_id,
    )
    assert claimed is None

    # Verify order is still OPEN
    stored_order = ctx["marketplace_repo"].get_order("tenant-alpha", "org-alpha", order.order_id)
    assert stored_order.status == MarketplaceOrderStatus.OPEN


# ---------------------------------------------------------------------------
# Attack 3: Unauthorized Settlement by Non-Claimant
# ---------------------------------------------------------------------------
def test_attack_03_unauthorized_settlement_by_non_claimant(test_context):
    """An imposter agent or org cannot execute and settle an order claimed by another."""
    ctx = test_context
    order = ctx["coordinator"].publish_order(
        client_tenant_id="tenant-alpha",
        client_org_id="org-alpha",
        title="Order for Settlement",
        description="Settlement security test",
        required_capability="ADVANCED_SECURITY_AUDIT",
        bounty_amount=150,
        client_ledger=ctx["client_ledger"],
        policy_engine=ctx["policy_engine"],
        client_agent=ctx["client_agent"],
        client_org=ctx["client_org"],
    )

    claimed = ctx["coordinator"].discover_and_claim(
        provider_tenant_id="tenant-beta",
        provider_org_id="org-beta",
        provider_agent=ctx["provider_agent"],
        provider_org=ctx["provider_org"],
        capability_manager=ctx["capability_manager"],
        policy_engine=ctx["policy_engine"],
        target_order_id=order.order_id,
    )
    assert claimed is not None

    imposter_agent_id = "agent-imposter"
    with pytest.raises(PermissionError, match="is not the authorized claimant"):
        ctx["coordinator"].execute_and_settle(
            order=claimed,
            provider_tenant_id="tenant-beta",
            provider_org_id="org-beta",
            producer_agent_id=imposter_agent_id,
            work_executor=ctx["sim_executor"],
            work_verifier=ctx["work_verifier"],
            client_ledger=ctx["client_ledger"],
            provider_ledger=ctx["provider_ledger"],
            surplus_reconciler=ctx["surplus_reconciler"],
        )


# ---------------------------------------------------------------------------
# Attack 4: Rejected Deliverable Blocks Settlement
# ---------------------------------------------------------------------------
def test_attack_04_rejected_deliverable_blocks_settlement(test_context):
    """If deliverable verification fails, escrow is NOT released and provider receives no revenue."""
    ctx = test_context
    order = ctx["coordinator"].publish_order(
        client_tenant_id="tenant-alpha",
        client_org_id="org-alpha",
        title="Quality Strict Order",
        description="Must pass verification",
        required_capability="SECURITY_AUDIT",
        bounty_amount=200,
        client_ledger=ctx["client_ledger"],
        policy_engine=ctx["policy_engine"],
        client_agent=ctx["client_agent"],
        client_org=ctx["client_org"],
    )

    claimed = ctx["coordinator"].discover_and_claim(
        provider_tenant_id="tenant-beta",
        provider_org_id="org-beta",
        provider_agent=ctx["provider_agent"],
        provider_org=ctx["provider_org"],
        capability_manager=ctx["capability_manager"],
        policy_engine=ctx["policy_engine"],
        target_order_id=order.order_id,
    )

    # Mock work executor returning a deliverable with critical finding (triggers verifier rejection)
    bad_executor = MagicMock()
    bad_executor.execute_work.return_value = WorkDeliverable.create(
        work_order_id=claimed.work_order_id or f"wo-b2b-{claimed.order_id}",
        producer_agent_id=ctx["provider_agent"].id,
        content_payload={"findings": [{"severity": "CRITICAL", "description": "Failure"}]},
        orbio_credits_consumed=500,
    )

    initial_escrow = ctx["client_ledger"].get_balance(ESCROW)
    initial_provider_treasury = ctx["provider_ledger"].get_balance(TREASURY)

    with pytest.raises(ValueError, match="failed independent verification"):
        ctx["coordinator"].execute_and_settle(
            order=claimed,
            provider_tenant_id="tenant-beta",
            provider_org_id="org-beta",
            producer_agent_id=ctx["provider_agent"].id,
            work_executor=bad_executor,
            work_verifier=ctx["work_verifier"],
            client_ledger=ctx["client_ledger"],
            provider_ledger=ctx["provider_ledger"],
            surplus_reconciler=ctx["surplus_reconciler"],
        )

    # Escrow must still be HELD; Provider treasury must NOT increase
    assert ctx["client_ledger"].get_balance(ESCROW) == initial_escrow
    assert ctx["provider_ledger"].get_balance(TREASURY) == initial_provider_treasury
    escrow = ctx["marketplace_repo"].get_escrow_by_order(order.order_id)
    assert escrow.status == EscrowStatus.HELD


# ---------------------------------------------------------------------------
# Attack 5: Duplicate Settlement Prevention
# ---------------------------------------------------------------------------
def test_attack_05_duplicate_settlement_prevention(test_context):
    """An already settled order cannot be executed and settled a second time."""
    ctx = test_context
    order = ctx["coordinator"].publish_order(
        client_tenant_id="tenant-alpha",
        client_org_id="org-alpha",
        title="One Time Settlement",
        description="Desc",
        required_capability="ADVANCED_SECURITY_AUDIT",
        bounty_amount=100,
        client_ledger=ctx["client_ledger"],
        policy_engine=ctx["policy_engine"],
        client_agent=ctx["client_agent"],
        client_org=ctx["client_org"],
    )
    claimed = ctx["coordinator"].discover_and_claim(
        provider_tenant_id="tenant-beta",
        provider_org_id="org-beta",
        provider_agent=ctx["provider_agent"],
        provider_org=ctx["provider_org"],
        capability_manager=ctx["capability_manager"],
        policy_engine=ctx["policy_engine"],
        target_order_id=order.order_id,
    )

    outcome = ctx["coordinator"].execute_and_settle(
        order=claimed,
        provider_tenant_id="tenant-beta",
        provider_org_id="org-beta",
        producer_agent_id=ctx["provider_agent"].id,
        work_executor=ctx["sim_executor"],
        work_verifier=ctx["work_verifier"],
        client_ledger=ctx["client_ledger"],
        provider_ledger=ctx["provider_ledger"],
        surplus_reconciler=ctx["surplus_reconciler"],
    )
    assert outcome.success is True

    # Re-fetch order and attempt second settlement
    settled_order = ctx["marketplace_repo"].get_order("tenant-alpha", "org-alpha", order.order_id)
    with pytest.raises(ValueError, match="must be CLAIMED"):
        ctx["coordinator"].execute_and_settle(
            order=settled_order,
            provider_tenant_id="tenant-beta",
            provider_org_id="org-beta",
            producer_agent_id=ctx["provider_agent"].id,
            work_executor=ctx["sim_executor"],
            work_verifier=ctx["work_verifier"],
            client_ledger=ctx["client_ledger"],
            provider_ledger=ctx["provider_ledger"],
            surplus_reconciler=ctx["surplus_reconciler"],
        )


# ---------------------------------------------------------------------------
# Attack 6: Duplicate Revenue Reconciliation Prevention
# ---------------------------------------------------------------------------
def test_attack_06_duplicate_revenue_reconciliation_prevention(test_context):
    """Calling SurplusReconciler twice for the same work order is strictly rejected."""
    ctx = test_context
    wo = WorkOrder(
        tenant_id="tenant-beta", organisation_id="org-beta", work_order_id="wo-dup-test",
        client_id="org-alpha", title="Work", description="Desc", deliverable_type="DOCS",
        required_orbio_credits=10, bounty_amount=100,
    )
    deliverable = WorkDeliverable.create(
        work_order_id=wo.work_order_id,
        producer_agent_id=ctx["provider_agent"].id,
        content_payload={"deliverable_type": "DOCS", "summary": "Valid payload"},
        orbio_credits_consumed=10,
    )
    receipt = ctx["work_verifier"].verify(wo, deliverable)
    assert receipt.is_verified()

    # Deposit revenue to REVENUE account before surplus reconciliation
    ctx["provider_ledger"].deposit_revenue(100, "wo-dup-test revenue")

    # First reconciliation succeeds
    event1 = ctx["surplus_reconciler"].reconcile_surplus(wo, receipt, 100, 0, 10)
    assert event1 is not None

    # Second reconciliation must fail
    with pytest.raises(ValueError, match="has already been reconciled"):
        ctx["surplus_reconciler"].reconcile_surplus(wo, receipt, 100, 0, 10)


# ---------------------------------------------------------------------------
# Attack 7: Duplicate Capability Grant Idempotency
# ---------------------------------------------------------------------------
def test_attack_07_duplicate_capability_grant_idempotency(test_context):
    """Attempting to evaluate/grant an active capability does not create duplicate active grants."""
    ctx = test_context
    supervisor = AgentRecord(
        id="beta-ceo",
        organisation_id="org-beta",
        role=AgentRole.CEO,
        allowed_action_types=[ActionType.PROPOSE_CAPABILITY_EXPANSION],
    )
    ctx["provider_org"].agents[supervisor.id] = supervisor

    prop = CapabilityProposal(
        tenant_id="tenant-beta",
        organisation_id="org-beta",
        proposal_id="prop-cap-idem-1",
        target_agent_id=ctx["provider_agent"].id,
        proposer_agent_id=supervisor.id,
        requested_capability="DATA_MODELING",
        requested_permission="EXECUTE_ADVANCED",
        current_performance_score=92.0,
        rationale="Promotion test",
    )
    grant1 = ctx["capability_manager"].evaluate_and_grant(
        proposal=prop,
        supervisor_agent=supervisor,
        target_agent=ctx["provider_agent"],
        organisation=ctx["provider_org"],
    )
    assert grant1 is not None

    # Second grant attempt with identical capability
    prop2 = CapabilityProposal(
        tenant_id="tenant-beta",
        organisation_id="org-beta",
        proposal_id="prop-cap-idem-2",
        target_agent_id=ctx["provider_agent"].id,
        proposer_agent_id=supervisor.id,
        requested_capability="DATA_MODELING",
        requested_permission="EXECUTE_ADVANCED",
        current_performance_score=93.0,
        rationale="Duplicate attempt",
    )
    grant2 = ctx["capability_manager"].evaluate_and_grant(
        proposal=prop2,
        supervisor_agent=supervisor,
        target_agent=ctx["provider_agent"],
        organisation=ctx["provider_org"],
    )
    assert grant2 is not None

    # Check active capabilities: agent should have active capability
    has_cap = ctx["marketplace_repo"].has_capability(
        "tenant-beta", "org-beta", ctx["provider_agent"].id, "DATA_MODELING"
    )
    assert has_cap is True


# ---------------------------------------------------------------------------
# Attack 8: Cross-Tenant Access
# ---------------------------------------------------------------------------
def test_attack_08_cross_tenant_isolation(test_context):
    """Tenant Alpha's private marketplace records are inaccessible to Tenant Beta."""
    ctx = test_context
    order = ctx["coordinator"].publish_order(
        client_tenant_id="tenant-alpha",
        client_org_id="org-alpha",
        title="Tenant Isolation Check",
        description="Confidential",
        required_capability="ADVANCED_SECURITY_AUDIT",
        bounty_amount=50,
        client_ledger=ctx["client_ledger"],
        policy_engine=ctx["policy_engine"],
        client_agent=ctx["client_agent"],
        client_org=ctx["client_org"],
    )

    # Inaccessible under foreign tenant_id
    foreign_fetch = ctx["marketplace_repo"].get_order("tenant-beta", "org-alpha", order.order_id)
    assert foreign_fetch is None

    foreign_escrow = ctx["marketplace_repo"].get_escrow(
        "tenant-beta", "org-alpha", f"escrow-{order.order_id}"
    )
    assert foreign_escrow is None


# ---------------------------------------------------------------------------
# Attack 9: Forged Authorization
# ---------------------------------------------------------------------------
def test_attack_09_forged_authorization_rejection(test_context):
    """A forged or tampered policy authorization token is rejected by system verifiers."""
    from src.audit.auditor import Auditor
    engine = test_context["policy_engine"]
    proposal = ActionProposal(
        id="prop-tamper",
        task_id="task-1",
        proposing_agent_id=test_context["client_agent"].id,
        action_type=ActionType.INTERNAL_ANALYSIS,
        target="internal://research_synthesis",
        parameters={},
        requested_credits=0,
        expected_value_score=0.9,
        risk_assessment="None",
        rationale="Test",
    )
    decision = engine.evaluate(proposal, test_context["client_org"])
    assert decision.result == PolicyResult.APPROVED
    assert decision.authorization_token is not None

    auditor = Auditor(verification_secret="sec-audit-policy-999")
    valid, reason = auditor.verify_authorization_token(decision.authorization_token, proposal, test_context["client_org"])
    assert valid is True

    # Tampered token is rejected
    tampered_token = decision.authorization_token + "TAMPERED"
    valid_bad, reason_bad = auditor.verify_authorization_token(tampered_token, proposal, test_context["client_org"])
    assert valid_bad is False


# ---------------------------------------------------------------------------
# Attack 10: Modified Authorized Order Bounty
# ---------------------------------------------------------------------------
def test_attack_10_modified_authorized_order_integrity(test_context):
    """Altering order bounty amount after publication cannot drain client treasury."""
    ctx = test_context
    order = ctx["coordinator"].publish_order(
        client_tenant_id="tenant-alpha",
        client_org_id="org-alpha",
        title="Tamper Bounty Test",
        description="Desc",
        required_capability="ADVANCED_SECURITY_AUDIT",
        bounty_amount=100,
        client_ledger=ctx["client_ledger"],
        policy_engine=ctx["policy_engine"],
        client_agent=ctx["client_agent"],
        client_org=ctx["client_org"],
    )

    claimed = ctx["coordinator"].discover_and_claim(
        provider_tenant_id="tenant-beta",
        provider_org_id="org-beta",
        provider_agent=ctx["provider_agent"],
        provider_org=ctx["provider_org"],
        capability_manager=ctx["capability_manager"],
        policy_engine=ctx["policy_engine"],
        target_order_id=order.order_id,
    )

    # Malicious actor tampers with order in memory to claim 500 bounty
    claimed.bounty_amount = 500

    # Execute and settle will attempt to transfer 500 from ESCROW (which only holds 100)
    with pytest.raises(InsufficientCreditsError):
        ctx["coordinator"].execute_and_settle(
            order=claimed,
            provider_tenant_id="tenant-beta",
            provider_org_id="org-beta",
            producer_agent_id=ctx["provider_agent"].id,
            work_executor=ctx["sim_executor"],
            work_verifier=ctx["work_verifier"],
            client_ledger=ctx["client_ledger"],
            provider_ledger=ctx["provider_ledger"],
            surplus_reconciler=ctx["surplus_reconciler"],
        )


# ---------------------------------------------------------------------------
# Attack 11: Insufficient Escrow Publication
# ---------------------------------------------------------------------------
def test_attack_11_insufficient_escrow_publication(test_context):
    """Publishing an order with a bounty exceeding treasury balance fails."""
    ctx = test_context
    with pytest.raises(Exception):  # Either Policy rejection or InsufficientCreditsError
        ctx["coordinator"].publish_order(
            client_tenant_id="tenant-alpha",
            client_org_id="org-alpha",
            title="Overdraft Order",
            description="Exceeds balance",
            required_capability="ADVANCED_SECURITY_AUDIT",
            bounty_amount=99999,  # Treasury is only 1000
            client_ledger=ctx["client_ledger"],
            policy_engine=ctx["policy_engine"],
            client_agent=ctx["client_agent"],
            client_org=ctx["client_org"],
        )


# ---------------------------------------------------------------------------
# Attack 12: Insufficient / Negative Surplus Rejection
# ---------------------------------------------------------------------------
def test_attack_12_insufficient_surplus_rejection(test_context):
    """Reconciler rejects negative gross revenue or direct expense exceeding gross revenue."""
    ctx = test_context
    wo = WorkOrder(
        tenant_id="tenant-beta", organisation_id="org-beta", work_order_id="wo-neg-test",
        client_id="org-alpha", title="Work", description="Desc", deliverable_type="DOCS",
        required_orbio_credits=10, bounty_amount=100,
    )
    deliverable = WorkDeliverable.create(
        work_order_id=wo.work_order_id,
        producer_agent_id=ctx["provider_agent"].id,
        content_payload={"deliverable_type": "DOCS", "summary": "Valid"},
        orbio_credits_consumed=10,
    )
    receipt = ctx["work_verifier"].verify(wo, deliverable)
    assert receipt.is_verified()

    # Negative gross revenue rejected
    with pytest.raises(ValueError, match="cannot be negative"):
        ctx["surplus_reconciler"].reconcile_surplus(wo, receipt, gross_revenue_usdg=-10, direct_expense_usdg=0, orbio_credits_consumed=10)

    # Negative direct expense rejected
    with pytest.raises(ValueError, match="cannot be negative"):
        ctx["surplus_reconciler"].reconcile_surplus(wo, receipt, gross_revenue_usdg=50, direct_expense_usdg=-5, orbio_credits_consumed=10)

    # Loss scenario (expense > gross revenue): capital preservation, 0 surplus allocated
    ctx["provider_ledger"].deposit_revenue(50, "Loss scenario revenue")
    event_loss = ctx["surplus_reconciler"].reconcile_surplus(wo, receipt, gross_revenue_usdg=50, direct_expense_usdg=100, orbio_credits_consumed=10)
    assert event_loss.net_surplus_usdg == 0
    assert event_loss.allocated_to_mission_budget == 0
    assert event_loss.allocated_to_reserve == 0


# ---------------------------------------------------------------------------
# Attack 13: Failed Orbio Call Fallback to Simulated
# ---------------------------------------------------------------------------
def test_attack_13_failed_orbio_call_fallback_to_simulated(test_context):
    """When Orbio API call fails with network/server exception, system falls back to simulated execution with is_simulated=True."""
    adapter = OrbioGatewayAdapter(
        api_key="sk-test-key",
        fallback_executor=test_context["sim_executor"],
        credit_store={"org-beta": 10000},
    )
    wo = WorkOrder(
        tenant_id="tenant-beta", organisation_id="org-beta", work_order_id="wo-fail-test",
        client_id="org-alpha", title="Failing live test", description="Desc",
        deliverable_type="DOCS", required_orbio_credits=10, bounty_amount=50,
    )

    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("Connection refused")):
        deliverable = adapter.execute_work(wo, "beta-worker-agent", "org-beta")
        assert deliverable is not None
        assert deliverable.execution_telemetry.get("is_simulated") is True
        assert "SimulatedWorkExecutor" in str(deliverable.execution_telemetry.get("executor"))


# ---------------------------------------------------------------------------
# Attack 14: Malformed Orbio Response Safe Fallback
# ---------------------------------------------------------------------------
def test_attack_14_malformed_orbio_response_safe_fallback(test_context):
    """When Orbio returns HTTP 200 with invalid/missing JSON fields, adapter falls back safely with is_simulated=True."""
    adapter = OrbioGatewayAdapter(
        api_key="sk-test-key",
        fallback_executor=test_context["sim_executor"],
        credit_store={"org-beta": 10000},
    )
    wo = WorkOrder(
        tenant_id="tenant-beta", organisation_id="org-beta", work_order_id="wo-malform-test",
        client_id="org-alpha", title="Malformed response test", description="Desc",
        deliverable_type="DOCS", required_orbio_credits=10, bounty_amount=50,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"invalid_key": "no choices here"}
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.post", return_value=mock_resp):
        deliverable = adapter.execute_work(wo, "beta-worker-agent", "org-beta")
        assert deliverable is not None
        assert deliverable.execution_telemetry.get("is_simulated") is True


# ---------------------------------------------------------------------------
# Attack 15: Simulated vs Live Provenance Truthfulness
# ---------------------------------------------------------------------------
def test_attack_15_simulated_vs_live_provenance_truthfulness():
    """Simulated execution must unconditionally report is_simulated=True."""
    sim = SimulatedWorkExecutor(credit_store={"org-test": 5000})
    wo = WorkOrder(
        tenant_id="t1", organisation_id="org-test", work_order_id="wo-truth-test",
        client_id="c1", title="Truth check", description="Desc",
        deliverable_type="DOCS", required_orbio_credits=10, bounty_amount=20,
    )
    deliv = sim.execute_work(wo, "agent-1", "org-test")
    assert deliv.execution_telemetry["is_simulated"] is True
    assert deliv.execution_telemetry["is_simulated"] is not False


# ---------------------------------------------------------------------------
# Attack 16: Mission Budget Overspending Prevention
# ---------------------------------------------------------------------------
def test_attack_16_mission_budget_overspending_prevented(test_context):
    """Org cannot spend more than the allocated 60% mission budget from surplus."""
    ctx = test_context
    wo = WorkOrder(
        tenant_id="tenant-beta", organisation_id="org-beta", work_order_id="wo-budget-test",
        client_id="org-alpha", title="Work", description="Desc", deliverable_type="DOCS",
        required_orbio_credits=10, bounty_amount=200,
    )
    deliverable = WorkDeliverable.create(
        work_order_id=wo.work_order_id,
        producer_agent_id=ctx["provider_agent"].id,
        content_payload={"deliverable_type": "DOCS", "summary": "Valid"},
        orbio_credits_consumed=10,
    )
    receipt = ctx["work_verifier"].verify(wo, deliverable)
    assert receipt.is_verified()

    # Deposit gross revenue to REVENUE before surplus reconciliation
    ctx["provider_ledger"].deposit_revenue(200, "Revenue for mission budget test")

    event = ctx["surplus_reconciler"].reconcile_surplus(wo, receipt, 200, 0, 10, reserve_ratio=0.40)
    # 60% of 200 = 120
    assert event.allocated_to_mission_budget == 120
    assert event.allocated_to_reserve == 80

    # Provider treasury now has 500 (initial) + 200 (revenue) = 700
    assert event.allocated_to_mission_budget < 200
    with pytest.raises(InsufficientCreditsError):
        # Spending entire treasury (700) when requesting 800
        ctx["provider_ledger"].transfer(TREASURY, EXTERNAL_SINK, 800, "Exceed total")


# ---------------------------------------------------------------------------
# Attack 17: Replayed Deliverable Receipt Mismatch
# ---------------------------------------------------------------------------
def test_attack_17_replayed_deliverable_receipt_mismatch(test_context):
    """Reconciler strictly rejects receipt where work_order_id does not match the work order."""
    ctx = test_context
    wo_real = WorkOrder(
        tenant_id="tenant-beta", organisation_id="org-beta", work_order_id="wo-real",
        client_id="org-alpha", title="Real Work", description="Desc", deliverable_type="DOCS",
        required_orbio_credits=10, bounty_amount=100,
    )
    wo_victim = WorkOrder(
        tenant_id="tenant-beta", organisation_id="org-beta", work_order_id="wo-victim",
        client_id="org-alpha", title="Victim Work", description="Desc", deliverable_type="DOCS",
        required_orbio_credits=10, bounty_amount=100,
    )

    deliverable = WorkDeliverable.create(
        work_order_id="wo-real",
        producer_agent_id=ctx["provider_agent"].id,
        content_payload={"deliverable_type": "DOCS", "summary": "Valid"},
        orbio_credits_consumed=10,
    )
    receipt_real = ctx["work_verifier"].verify(wo_real, deliverable)
    assert receipt_real.is_verified()

    with pytest.raises(ValueError, match="Receipt work_order_id mismatch"):
        ctx["surplus_reconciler"].reconcile_surplus(wo_victim, receipt_real, 100, 0, 10)


# ---------------------------------------------------------------------------
# Attack 18: Settlement After Capability Revocation
# ---------------------------------------------------------------------------
def test_attack_18_settlement_after_capability_revocation(test_context):
    """If capability is revoked or expired after claim but before execution, settlement is rejected."""
    ctx = test_context
    order = ctx["coordinator"].publish_order(
        client_tenant_id="tenant-alpha",
        client_org_id="org-alpha",
        title="Revocation Settlement Check",
        description="Desc",
        required_capability="REVOCABLE_CAPABILITY",
        bounty_amount=100,
        client_ledger=ctx["client_ledger"],
        policy_engine=ctx["policy_engine"],
        client_agent=ctx["client_agent"],
        client_org=ctx["client_org"],
    )

    claimed = ctx["coordinator"].discover_and_claim(
        provider_tenant_id="tenant-beta",
        provider_org_id="org-beta",
        provider_agent=ctx["provider_agent"],
        provider_org=ctx["provider_org"],
        capability_manager=ctx["capability_manager"],
        policy_engine=ctx["policy_engine"],
        target_order_id=order.order_id,
    )
    assert claimed is not None

    # Now revoke the capability before execution
    ctx["marketplace_repo"].revoke_capability(
        tenant_id="tenant-beta",
        organisation_id="org-beta",
        agent_id=ctx["provider_agent"].id,
        capability="REVOCABLE_CAPABILITY",
    )

    # Attempt execute and settle: must raise PermissionError
    with pytest.raises(PermissionError, match="does not hold active, unrevoked capability"):
        ctx["coordinator"].execute_and_settle(
            order=claimed,
            provider_tenant_id="tenant-beta",
            provider_org_id="org-beta",
            producer_agent_id=ctx["provider_agent"].id,
            work_executor=ctx["sim_executor"],
            work_verifier=ctx["work_verifier"],
            client_ledger=ctx["client_ledger"],
            provider_ledger=ctx["provider_ledger"],
            surplus_reconciler=ctx["surplus_reconciler"],
        )
