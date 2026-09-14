import pytest

from src.audit.auditor import Auditor, AuditVerificationError
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, OperationState
from src.economy.ledger import DoubleEntryLedger
from src.execution.consequential import ConsequentialExecutionManager, ConsequentialOperationRepository
from src.governance.policy_engine import PolicyEngine
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore
from src.settlement.simulated_provider import SimulatedConsequentialProvider


@pytest.fixture
def audit_env():
    db = Database(":memory:")
    ledger = DoubleEntryLedger(initial_treasury=100)
    policy = PolicyEngine(signing_secret="audit-secret")
    provider = SimulatedConsequentialProvider()
    event_store = SqliteEventStore(db, verify_on_startup=False)
    manager = ConsequentialExecutionManager(
        policy_engine=policy,
        ledger=ledger,
        provider=provider,
        db_conn=db.conn,
        event_store=event_store,
    )
    auditor = Auditor(verification_secret="audit-secret")

    org = Organisation(
        id="org-audit-1",
        tenant_id="tenant-demo",
        mission="Audit verification test",
        treasury_balance=100,
    )
    analyst = AgentRecord(
        id="agent-fin",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=50,
        allowed_action_types=[ActionType.EXTERNAL_API_CALL],
    )
    org.agents["agent-fin"] = analyst

    return {
        "db": db,
        "ledger": ledger,
        "policy": policy,
        "provider": provider,
        "manager": manager,
        "auditor": auditor,
        "event_store": event_store,
        "org": org,
    }


def _proposal(cost=30, op_id="prop-audit-1") -> ActionProposal:
    return ActionProposal(
        id=op_id,
        task_id="t-audit-1",
        proposing_agent_id="agent-fin",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="sandbox://market_index_fund",
        parameters={"asset": "AAPL", "quantity": 10},
        requested_credits=cost,
        expected_value_score=0.9,
        risk_assessment="Low risk",
        rationale="Audit test proposal",
    )


def test_auditor_verifies_successful_consequential_operation(audit_env):
    """Auditor independently verifies a successfully executed consequential operation."""
    manager = audit_env["manager"]
    ledger = audit_env["ledger"]
    policy = audit_env["policy"]
    auditor = audit_env["auditor"]
    event_store = audit_env["event_store"]
    org = audit_env["org"]

    prop = _proposal(cost=25, op_id="prop-aud-success")
    decision = policy.evaluate(prop, org, ledger=ledger)
    receipt = manager.execute_proposal(prop, decision, org)

    op = manager.repo.get_by_idempotency_key(f"{org.id}:{prop.id}")
    assert op.state == OperationState.SUCCEEDED

    verification = auditor.verify_consequential_operation(
        operation=op,
        proposal=prop,
        decision=decision,
        org=org,
        ledger=ledger,
        event_store=event_store,
        policy_engine=policy,
    )

    assert verification.verified is True
    assert len(verification.failures) == 0
    assert "OPERATION_FINGERPRINT" in verification.checks
    assert "PROVIDER_EVIDENCE_INTEGRITY" in verification.checks
    assert "LEDGER_SETTLEMENT" in verification.checks


def test_auditor_detects_tampered_amount(audit_env):
    """Auditor catches if operation amount was altered after authorization."""
    manager = audit_env["manager"]
    ledger = audit_env["ledger"]
    policy = audit_env["policy"]
    auditor = audit_env["auditor"]
    event_store = audit_env["event_store"]
    org = audit_env["org"]

    prop = _proposal(cost=20, op_id="prop-aud-tamper")
    decision = policy.evaluate(prop, org, ledger=ledger)
    receipt = manager.execute_proposal(prop, decision, org)

    op = manager.repo.get_by_idempotency_key(f"{org.id}:{prop.id}")
    # Tamper with the in-memory operation amount
    op.amount = 999

    with pytest.raises(AuditVerificationError) as exc_info:
        auditor.verify_consequential_operation(
            operation=op,
            proposal=prop,
            decision=decision,
            org=org,
            ledger=ledger,
            event_store=event_store,
            policy_engine=policy,
        )

    assert any("Operation amount" in f for f in exc_info.value.failures)


def test_auditor_detects_missing_provider_reference(audit_env):
    """Auditor catches if a SUCCEEDED operation claims success without external evidence."""
    manager = audit_env["manager"]
    ledger = audit_env["ledger"]
    policy = audit_env["policy"]
    auditor = audit_env["auditor"]
    event_store = audit_env["event_store"]
    org = audit_env["org"]

    prop = _proposal(cost=15, op_id="prop-aud-no-ref")
    decision = policy.evaluate(prop, org, ledger=ledger)
    receipt = manager.execute_proposal(prop, decision, org)

    op = manager.repo.get_by_idempotency_key(f"{org.id}:{prop.id}")
    op.provider_reference = None  # Strip external proof

    with pytest.raises(AuditVerificationError) as exc_info:
        auditor.verify_consequential_operation(
            operation=op,
            proposal=prop,
            decision=decision,
            org=org,
            ledger=ledger,
            event_store=event_store,
            policy_engine=policy,
        )

    assert any("lacks provider reference" in f for f in exc_info.value.failures)
