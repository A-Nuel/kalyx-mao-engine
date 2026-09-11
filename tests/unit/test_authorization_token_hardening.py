import time
import pytest
from src.domain.entities import Organisation, AgentRecord, ActionProposal, PolicyDecision
from src.domain.enums import OrgState, AgentRole, ActionType, PolicyResult
from src.domain.exceptions import UnauthorizedActionError
from src.governance.policy_engine import PolicyEngine
from src.execution.executor import SandboxExecutor
from src.economy.ledger import DoubleEntryLedger
from src.audit.auditor import Auditor

@pytest.fixture
def hardening_env():
    ledger = DoubleEntryLedger(initial_treasury=100)
    engine = PolicyEngine(signing_secret="hardening-secret-key-12345", token_ttl_seconds=60.0)
    executor = SandboxExecutor(engine, ledger)
    auditor = Auditor(verification_secret="hardening-secret-key-12345")

    org = Organisation(id="org-main", mission="Hardening Test", treasury_balance=100, state=OrgState.EXECUTING)
    ceo = AgentRecord(
        id="agent-ceo",
        role=AgentRole.CEO,
        authority_ceiling=30,
        allowed_action_types=[ActionType.SIMULATED_ALLOCATION]
    )
    org.agents["agent-ceo"] = ceo

    proposal = ActionProposal(
        id="prop-h-01",
        task_id="task-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=15,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Authorized index investment"
    )

    return {
        "ledger": ledger,
        "engine": engine,
        "executor": executor,
        "auditor": auditor,
        "org": org,
        "proposal": proposal
    }

def test_valid_unexpired_token(hardening_env):
    engine = hardening_env["engine"]
    proposal = hardening_env["proposal"]
    org = hardening_env["org"]
    executor = hardening_env["executor"]
    auditor = hardening_env["auditor"]

    decision = engine.evaluate(proposal, org)
    assert decision.result == PolicyResult.APPROVED
    assert decision.authorization_token is not None

    # Verify via PolicyEngine
    valid, reason = engine.verify_token(decision.authorization_token, proposal, org, decision)
    assert valid is True
    assert reason is None

    # Verify via independent Auditor
    a_valid, a_reason = auditor.verify_authorization_token(
        decision.authorization_token,
        proposal,
        org,
        decision=decision,
        expected_policy_version=engine.get_policy_version_hash()
    )
    assert a_valid is True
    assert a_reason is None

    # Execute successfully
    receipt = executor.execute(proposal, decision, org)
    assert receipt.cost_credits == 15
    assert receipt.authorization_token == decision.authorization_token

def test_expired_token_fails_verification(hardening_env):
    engine = hardening_env["engine"]
    proposal = hardening_env["proposal"]
    org = hardening_env["org"]
    executor = hardening_env["executor"]
    auditor = hardening_env["auditor"]

    # Generate token with negative TTL (already expired)
    expired_token = engine.generate_token(proposal, org, decision_id="dec-exp", ttl_seconds=-10.0)
    decision = PolicyDecision(
        id="dec-exp",
        proposal_id=proposal.id,
        result=PolicyResult.APPROVED,
        authorization_token=expired_token
    )

    valid, reason = engine.verify_token(expired_token, proposal, org, decision)
    assert valid is False
    assert "Authorization token expired" in reason

    # Independent Auditor check
    a_valid, a_reason = auditor.verify_authorization_token(
        expired_token, proposal, org, decision=decision, verification_secret=engine.get_signing_secret()
    )
    assert a_valid is False
    assert "Authorization token expired" in a_reason

    # SandboxExecutor rejects execution of expired token
    with pytest.raises(UnauthorizedActionError) as exc:
        executor.execute(proposal, decision, org)
    assert "Authorization token expired" in str(exc.value)

def test_token_from_wrong_organisation_fails(hardening_env):
    engine = hardening_env["engine"]
    proposal = hardening_env["proposal"]
    org = hardening_env["org"]
    executor = hardening_env["executor"]

    decision = engine.evaluate(proposal, org)
    token = decision.authorization_token

    rogue_org = Organisation(id="org-rogue", mission="Malicious Hijack", treasury_balance=100, state=OrgState.EXECUTING)
    rogue_org.agents["agent-ceo"] = org.agents["agent-ceo"]

    valid, reason = engine.verify_token(token, proposal, rogue_org, decision)
    assert valid is False
    assert "Token organisation mismatch" in reason

    with pytest.raises(UnauthorizedActionError) as exc:
        executor.execute(proposal, decision, rogue_org)
    assert "Token organisation mismatch" in str(exc.value)

def test_token_from_wrong_proposal_fails(hardening_env):
    engine = hardening_env["engine"]
    proposal = hardening_env["proposal"]
    org = hardening_env["org"]
    executor = hardening_env["executor"]

    decision = engine.evaluate(proposal, org)
    token = decision.authorization_token

    different_proposal = proposal.model_copy(update={"id": "prop-different-999"})

    valid, reason = engine.verify_token(token, different_proposal, org, decision)
    assert valid is False
    assert "Token proposal ID mismatch" in reason

    with pytest.raises(UnauthorizedActionError) as exc:
        executor.execute(different_proposal, decision, org)
    assert "Token proposal ID mismatch" in str(exc.value)

def test_token_from_wrong_policy_version_fails(hardening_env):
    engine = hardening_env["engine"]
    proposal = hardening_env["proposal"]
    org = hardening_env["org"]
    executor = hardening_env["executor"]

    decision = engine.evaluate(proposal, org)
    token = decision.authorization_token

    # Mutate policy configuration (simulating governance rule update)
    engine.human_approval_threshold = 10  # Changed threshold modifies policy version hash

    valid, reason = engine.verify_token(token, proposal, org, decision)
    assert valid is False
    assert "Policy version mismatch" in reason

    with pytest.raises(UnauthorizedActionError) as exc:
        executor.execute(proposal, decision, org)
    assert "Policy version mismatch" in str(exc.value)

def test_replayed_token_fails_execution(hardening_env):
    engine = hardening_env["engine"]
    proposal = hardening_env["proposal"]
    org = hardening_env["org"]
    executor = hardening_env["executor"]

    decision = engine.evaluate(proposal, org)

    # First execution succeeds
    receipt = executor.execute(proposal, decision, org)
    assert receipt is not None

    # Replay attempt with same token fails immediately
    with pytest.raises(UnauthorizedActionError) as exc:
        executor.execute(proposal, decision, org)
    assert "already been consumed (replay attack detected)" in str(exc.value)

def test_forged_token_signature_fails(hardening_env):
    engine = hardening_env["engine"]
    proposal = hardening_env["proposal"]
    org = hardening_env["org"]
    executor = hardening_env["executor"]

    decision = engine.evaluate(proposal, org)
    parts = decision.authorization_token.split(".")
    forged_token = f"{parts[0]}.{parts[1]}.badbadbadsignaturedeadbeef00000000000000000000000000000000"
    decision.authorization_token = forged_token

    valid, reason = engine.verify_token(forged_token, proposal, org, decision)
    assert valid is False
    assert "signature does not match" in reason or "forged" in reason

    with pytest.raises(UnauthorizedActionError) as exc:
        executor.execute(proposal, decision, org)
    assert "forged" in str(exc.value) or "signature does not match" in str(exc.value)
