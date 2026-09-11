import pytest
from src.audit.auditor import Auditor, AuditVerificationError
from src.domain.entities import ActionProposal, PolicyDecision, ExecutionReceipt, Organisation, Task, AgentRecord
from src.domain.enums import OrgState, AgentRole, ActionType, PolicyResult, TaskStatus
from src.governance.policy_engine import PolicyEngine
from src.economy.ledger import DoubleEntryLedger, TREASURY, EXTERNAL_SINK
from src.audit.event_store import AppendOnlyEventStore
from src.execution.executor import SandboxExecutor

@pytest.fixture
def audit_env():
    ledger = DoubleEntryLedger(initial_treasury=100)
    engine = PolicyEngine(signing_secret="auditor-test-secret")
    executor = SandboxExecutor(engine, ledger)
    event_store = AppendOnlyEventStore()
    auditor = Auditor()

    org = Organisation(id="org-aud", mission="Audit Test", treasury_balance=100, state=OrgState.EXECUTING)
    ceo = AgentRecord(id="agent-ceo", role=AgentRole.CEO, authority_ceiling=25, allowed_action_types=[ActionType.SIMULATED_ALLOCATION])
    org.agents["agent-ceo"] = ceo

    task = Task(id="t-01", mission_id="org-aud", assigned_agent_id="agent-ceo", objective="Work", allocated_credits=20, status=TaskStatus.APPROVED)

    proposal = ActionProposal(
        id="prop-aud-01",
        task_id="t-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=20,
        expected_value_score=0.85,
        risk_assessment="Low",
        rationale="Sound proposal"
    )

    decision = engine.evaluate(proposal, org)
    assert decision.result == PolicyResult.APPROVED

    receipt = executor.execute(proposal, decision, org)
    task.status = TaskStatus.COMPLETED

    return {
        "auditor": auditor,
        "proposal": proposal,
        "decision": decision,
        "receipt": receipt,
        "org": org,
        "task": task,
        "ledger": ledger,
        "event_store": event_store,
        "engine": engine
    }

def test_auditor_positive_verification(audit_env):
    v = audit_env["auditor"].verify_execution(
        proposal=audit_env["proposal"],
        decision=audit_env["decision"],
        receipt=audit_env["receipt"],
        org=audit_env["org"],
        task=audit_env["task"],
        ledger=audit_env["ledger"],
        event_store=audit_env["event_store"],
        policy_engine=audit_env["engine"]
    )
    assert v.verified is True
    assert len(v.failures) == 0
    assert len(v.checks) == 8
    assert len(v.evidence_hash) == 64

def test_auditor_detects_mismatched_target(audit_env):
    # Alter receipt target
    bad_receipt = audit_env["receipt"].model_copy(update={"target": "http://rogue-sink.com"})
    with pytest.raises(AuditVerificationError) as exc:
        audit_env["auditor"].verify_execution(
            proposal=audit_env["proposal"],
            decision=audit_env["decision"],
            receipt=bad_receipt,
            org=audit_env["org"],
            task=audit_env["task"],
            ledger=audit_env["ledger"],
            event_store=audit_env["event_store"],
            policy_engine=audit_env["engine"]
        )
    assert "Receipt target" in str(exc.value)

def test_auditor_detects_mismatched_credits(audit_env):
    bad_receipt = audit_env["receipt"].model_copy(update={"cost_credits": 25})
    with pytest.raises(AuditVerificationError) as exc:
        audit_env["auditor"].verify_execution(
            proposal=audit_env["proposal"],
            decision=audit_env["decision"],
            receipt=bad_receipt,
            org=audit_env["org"],
            task=audit_env["task"],
            ledger=audit_env["ledger"],
            event_store=audit_env["event_store"],
            policy_engine=audit_env["engine"]
        )
    assert "cost credits" in str(exc.value)

def test_auditor_detects_post_approval_proposal_tampering(audit_env):
    # Tamper with proposal parameters after execution
    tampered_proposal = audit_env["proposal"].model_copy()
    tampered_proposal.parameters["secret_injection"] = "true"

    with pytest.raises(AuditVerificationError) as exc:
        audit_env["auditor"].verify_execution(
            proposal=tampered_proposal,
            decision=audit_env["decision"],
            receipt=audit_env["receipt"],
            org=audit_env["org"],
            task=audit_env["task"],
            ledger=audit_env["ledger"],
            event_store=audit_env["event_store"],
            policy_engine=audit_env["engine"]
        )
    assert "Authorization token verification failed" in str(exc.value)

def test_auditor_detects_paused_state_at_audit(audit_env):
    audit_env["org"].state = OrgState.PAUSED
    with pytest.raises(AuditVerificationError) as exc:
        audit_env["auditor"].verify_execution(
            proposal=audit_env["proposal"],
            decision=audit_env["decision"],
            receipt=audit_env["receipt"],
            org=audit_env["org"],
            task=audit_env["task"],
            ledger=audit_env["ledger"],
            event_store=audit_env["event_store"],
            policy_engine=audit_env["engine"]
        )
    assert "PAUSED" in str(exc.value)
