"""Milestone 3 & 4: Emergency Circuit Breaker and Governed 2-of-2 Multi-Sig tests."""

import pytest
from src.domain.entities import AgentRecord, Organisation
from src.domain.enums import AgentRole, ActionType, OrgState
from src.domain.exceptions import PolicyViolationError
from src.economy.ledger import DoubleEntryLedger
from src.governance.admin_governance import AdminApproval, AdminGovernanceManager
from src.governance.circuit_breaker import CircuitBreakerState, SystemCircuitBreaker
from src.governance.policy_engine import PolicyEngine
from src.agents.b2b_marketplace_coordinator import B2BMarketplaceCoordinator
from src.persistence.database import Database
from src.persistence.marketplace_repository import MarketplaceRepository
from src.persistence.work_order_repository import WorkOrderRepository


@pytest.fixture
def env():
    db = Database(":memory:")
    with db.conn:
        db.conn.execute("INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES ('tenant-test', 'Test Tenant', 'active', datetime('now'))")
        db.conn.execute("INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES ('org-test', 'tenant-test', 'Test Mission', 1000, 'PLANNING', datetime('now'))")
    
    secret = "test-secret-key-32-characters-minimum"
    gov = AdminGovernanceManager(db, secret_key=secret)
    cb = SystemCircuitBreaker(db, governance_manager=gov)
    mkt_repo = MarketplaceRepository(db)
    wo_repo = WorkOrderRepository(db)
    coordinator = B2BMarketplaceCoordinator(
        marketplace_repo=mkt_repo,
        work_order_repo=wo_repo,
        circuit_breaker=cb,
        receipt_secret_key=secret,
    )
    ledger = DoubleEntryLedger(initial_treasury=1000)
    policy_engine = PolicyEngine(signing_secret=secret, human_approval_threshold=1000)
    agent = AgentRecord(
        id="agent-client",
        organisation_id="org-test",
        role=AgentRole.STRATEGIST,
        authority_ceiling=1000,
        allowed_action_types=[ActionType.PUBLISH_MARKETPLACE_ORDER, ActionType.INTERNAL_ANALYSIS],
    )
    org = Organisation(id="org-test", mission="Test Mission", state=OrgState.PLANNING)
    org.agents[agent.id] = agent

    return {
        "db": db,
        "cb": cb,
        "gov": gov,
        "mkt_repo": mkt_repo,
        "coordinator": coordinator,
        "ledger": ledger,
        "policy_engine": policy_engine,
        "agent": agent,
        "org": org,
        "secret": secret,
    }


def test_circuit_breaker_normal_allows_publication(env):
    """When circuit breaker is NORMAL, order publication proceeds cleanly."""
    cb = env["cb"]
    assert not cb.is_paused("tenant-test", "org-test")
    assert cb.get_state("tenant-test", "org-test") == CircuitBreakerState.NORMAL

    order = env["coordinator"].publish_b2b_order(
        client_tenant_id="tenant-test",
        client_org_id="org-test",
        title="Valid Order Under Normal State",
        description="Will succeed",
        required_capability="CODE_REVIEW",
        bounty_amount=100,
        client_ledger=env["ledger"],
        policy_engine=env["policy_engine"],
        client_agent=env["agent"],
        client_org=env["org"],
    )
    assert order is not None
    assert order.bounty_amount == 100


def test_circuit_breaker_pause_denies_new_publication(env):
    """When circuit breaker is PAUSED, new order publication is immediately blocked."""
    cb = env["cb"]
    cb.pause(
        tenant_id="tenant-test",
        organisation_id="org-test",
        operator_id="operator-1",
        reason="Security anomaly detected",
    )
    assert cb.is_paused("tenant-test", "org-test")
    assert cb.get_state("tenant-test", "org-test") == CircuitBreakerState.PAUSED

    with pytest.raises(PermissionError) as exc:
        env["coordinator"].publish_b2b_order(
            client_tenant_id="tenant-test",
            client_org_id="org-test",
            title="Order Blocked By Circuit Breaker",
            description="Should fail",
            required_capability="CODE_REVIEW",
            bounty_amount=100,
            client_ledger=env["ledger"],
            policy_engine=env["policy_engine"],
            client_agent=env["agent"],
            client_org=env["org"],
        )
    assert "circuit breaker is PAUSED" in str(exc.value)


def test_circuit_breaker_resume_strict_2_of_2_governance(env):
    """Circuit breaker resumption requires 2 distinct, verified, unconsumed admin signatures."""
    cb = env["cb"]
    gov = env["gov"]
    tenant_id = "tenant-test"
    org_id = "org-test"
    cb.pause(tenant_id, org_id, "op-1", "Emergency stop")
    action_type = "CIRCUIT_BREAKER_RESUME"
    payload = {"organisation_id": org_id}
    app1 = gov.create_approval(tenant_id, action_type, org_id, payload, approver_id="admin-alpha")
    app2 = gov.create_approval(tenant_id, action_type, org_id, payload, approver_id="admin-beta")
    with pytest.raises(ValueError, match="requires at least 2 distinct"):
        cb.resume(tenant_id, org_id, ["admin-alpha"], [app1.to_dict()])
    with pytest.raises(ValueError, match="distinct"):
        cb.resume(tenant_id, org_id, ["admin-alpha", "admin-alpha"], [app1.to_dict(), app1.to_dict()])
    tampered = AdminApproval(
        approval_id=app1.approval_id, tenant_id=app1.tenant_id,
        action_type=app1.action_type, target_id=app1.target_id,
        payload_hash=app1.payload_hash, approver_id=app1.approver_id,
        expires_at=app1.expires_at, signature="forged_signature_hex",
    )
    with pytest.raises(ValueError, match="governance rejected"):
        cb.resume(tenant_id, org_id, ["admin-alpha", "admin-beta"], [tampered.to_dict(), app2.to_dict()])
    assert cb.is_paused(tenant_id, org_id)
    audit_id = cb.resume(
        tenant_id, org_id, ["admin-alpha", "admin-beta"],
        [app1.to_dict(), app2.to_dict()],
    )
    assert audit_id
    assert not cb.is_paused(tenant_id, org_id)
    replayed_ok, replayed_err = gov.verify_and_consume_quorum(
        approvals=[app1, app2], required_approvals=2,
        expected_tenant_id=tenant_id, expected_action_type=action_type,
        expected_target_id=org_id, actual_payload=payload,
    )
    assert replayed_ok is False
    assert "already been consumed" in replayed_err
