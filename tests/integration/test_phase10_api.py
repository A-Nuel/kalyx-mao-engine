import os
import pytest
from fastapi.testclient import TestClient

from src.api.server import app, get_settlement_provider
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, OperationState
from src.domain.exceptions import ExternalExecutionError
from src.execution.consequential import ConsequentialExecutionManager
from src.governance.policy_engine import PolicyEngine
from src.persistence.factory import create_database
from src.persistence.repositories import SqliteRepository
from src.settlement.simulated_provider import SimulatedConsequentialProvider
from src.tenancy.ledger import TenantScopedLedger
from src.tenancy.organisation_ledger import OrganisationScopedLedger
from src.persistence.repositories import SqliteLedger


@pytest.fixture
def client():
    return TestClient(app)


def test_consequential_operations_api_lifecycle(client, monkeypatch, tmp_path):
    db_file = str(tmp_path / "phase10_api.db")
    monkeypatch.setenv("KALYX_DB", db_file)
    monkeypatch.setenv("KALYX_DATABASE_URL", "")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "false")

    db = create_database()
    repo = SqliteRepository(db)

    org = Organisation(
        id="org-api-p10",
        tenant_id="tenant-demo",
        mission="Test Phase 10 API",
        treasury_balance=100,
    )
    repo.save_organisation(org)

    agent = AgentRecord(
        id="agent-fin",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=50,
        allowed_action_types=[ActionType.EXTERNAL_API_CALL],
    )
    org.agents["agent-fin"] = agent
    repo.save_agent(agent, org.id)

    # Initialize org ledger with funds
    tenant_ledger = TenantScopedLedger(SqliteLedger(db, initial_treasury=0), "tenant-demo")
    org_ledger = OrganisationScopedLedger(tenant_ledger, org.id, initial_treasury=100)

    # Use simulated provider
    provider = SimulatedConsequentialProvider()
    app.state.settlement_provider = provider

    policy = PolicyEngine(signing_secret="api-secret")
    manager = ConsequentialExecutionManager(
        policy_engine=policy,
        ledger=org_ledger,
        provider=provider,
        db_conn=db.conn,
    )

    prop = ActionProposal(
        id="prop-api-rec",
        task_id="t-api-1",
        proposing_agent_id="agent-fin",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="sandbox://market_index_fund",
        parameters={"asset": "AAPL"},
        requested_credits=25,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="API test",
    )
    decision = policy.evaluate(prop, org, ledger=org_ledger)

    # Configure timeout with background success
    key = f"{org.id}:{prop.id}"
    provider.set_timeout_rule(key, provider_executes_in_background=True)

    with pytest.raises(ExternalExecutionError):
        manager.execute_proposal(prop, decision, org)

    # 1. List operations
    res = client.get(f"/api/organisations/{org.id}/operations")
    assert res.status_code == 200
    ops = res.json()["operations"]
    assert len(ops) == 1
    assert ops[0]["state"] == "unknown"
    op_id = ops[0]["id"]

    # 2. Filter by state
    res_filtered = client.get(f"/api/organisations/{org.id}/operations?state=unknown")
    assert res_filtered.status_code == 200
    assert len(res_filtered.json()["operations"]) == 1

    res_empty = client.get(f"/api/organisations/{org.id}/operations?state=succeeded")
    assert res_empty.status_code == 200
    assert len(res_empty.json()["operations"]) == 0

    # 3. Get single operation
    res_op = client.get(f"/api/organisations/{org.id}/operations/{op_id}")
    assert res_op.status_code == 200
    assert res_op.json()["operation"]["id"] == op_id

    # 4. Trigger reconciliation via POST
    res_rec = client.post(f"/api/organisations/{org.id}/operations/{op_id}/reconcile")
    assert res_rec.status_code == 200
    reconciled = res_rec.json()["operation"]
    assert reconciled["state"] == "reconciled"
    assert reconciled["provider_reference"] is not None

    # Verify ledger settled after reconciliation
    res_summary = client.get(f"/api/organisations/{org.id}/ledger")
    assert res_summary.status_code == 200
    assert res_summary.json()["treasury"] == 75
    assert res_summary.json()["external_sink"] == 25
    assert res_summary.json()["escrow"] == 0

    db.close()


def test_operations_api_404_handling(client, monkeypatch, tmp_path):
    db_file = str(tmp_path / "phase10_404.db")
    monkeypatch.setenv("KALYX_DB", db_file)
    monkeypatch.setenv("KALYX_DATABASE_URL", "")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "false")

    db = create_database()
    repo = SqliteRepository(db)
    org = Organisation(id="org-api-404", tenant_id="tenant-demo", mission="404 test")
    repo.save_organisation(org)

    res = client.get("/api/organisations/org-api-404/operations/cop-nonexistent")
    assert res.status_code == 404

    res_rec = client.post("/api/organisations/org-api-404/operations/cop-nonexistent/reconcile")
    assert res_rec.status_code == 404

    db.close()


def test_operations_api_identity_isolation(client, monkeypatch, tmp_path):
    db_file = str(tmp_path / "phase10_id.db")
    monkeypatch.setenv("KALYX_DB", db_file)
    monkeypatch.setenv("KALYX_DATABASE_URL", "")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")

    from src.identity.models import Membership, MembershipRole, Principal
    from src.identity.repository import IdentityRepository

    db = create_database()
    db.conn.execute("INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)", ("tenant-a", "Tenant A", "2026-09-13T00:00:00"))
    db.conn.execute("INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)", ("tenant-b", "Tenant B", "2026-09-13T00:00:00"))
    identity = IdentityRepository(db)
    identity.save_principal(Principal(id="principal-a", name="Alice"))
    identity.save_principal(Principal(id="principal-b", name="Bob"))
    identity.save_membership(Membership(principal_id="principal-a", tenant_id="tenant-a", role=MembershipRole.OWNER))
    identity.save_membership(Membership(principal_id="principal-b", tenant_id="tenant-b", role=MembershipRole.OWNER))

    repo = SqliteRepository(db)
    org = Organisation(id="org-api-tenant-a", tenant_id="tenant-a", mission="Tenant A test")
    repo.save_organisation(org)
    db.conn.commit()

    # Missing identity headers -> 401
    res = client.get(f"/api/organisations/{org.id}/operations")
    assert res.status_code == 401

    # Cross-tenant request -> 404 or 403 (out of scope for tenant-b)
    headers = {"X-Tenant-ID": "tenant-b", "X-Principal-ID": "principal-b"}
    res_forbidden = client.get(f"/api/organisations/{org.id}/operations", headers=headers)
    assert res_forbidden.status_code in (403, 404)

    # Valid tenant and principal -> 200
    headers_valid = {"X-Tenant-ID": "tenant-a", "X-Principal-ID": "principal-a"}
    res_valid = client.get(f"/api/organisations/{org.id}/operations", headers=headers_valid)
    assert res_valid.status_code == 200

    db.close()
