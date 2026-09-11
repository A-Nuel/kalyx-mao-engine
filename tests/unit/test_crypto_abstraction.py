import pytest
from src.governance.crypto import (
    ITokenSigner,
    ITokenVerifier,
    HmacSha256TokenSigner,
    HmacSha256TokenVerifier
)
from src.governance.policy_engine import PolicyEngine
from src.audit.auditor import Auditor
from src.domain.entities import Organisation, AgentRecord, ActionProposal
from src.domain.enums import OrgState, AgentRole, ActionType, PolicyResult

def test_hmac_signer_and_verifier():
    signer = HmacSha256TokenSigner("test-secret-123")
    verifier = HmacSha256TokenVerifier("test-secret-123")

    data = b"important-proposal-payload-data"
    signature = signer.sign(data)

    assert isinstance(signature, str)
    assert len(signature) == 64
    assert verifier.verify(data, signature) is True
    assert verifier.verify(b"different-data", signature) is False
    assert verifier.verify(data, "bad" + signature[3:]) is False

def test_different_secret_fails_verification():
    signer = HmacSha256TokenSigner("secret-alpha")
    verifier = HmacSha256TokenVerifier("secret-beta")

    data = b"message-to-verify"
    sig = signer.sign(data)
    assert verifier.verify(data, sig) is False

def test_custom_signer_and_verifier_injection():
    secret = "custom-crypto-injection-secret"
    signer = HmacSha256TokenSigner(secret)
    verifier = HmacSha256TokenVerifier(secret)

    engine = PolicyEngine(signer=signer, verifier=verifier)
    auditor = Auditor(verifier=verifier)

    org = Organisation(id="org-crypto", mission="Crypto Test", treasury_balance=100, state=OrgState.EXECUTING)
    ceo = AgentRecord(id="agent-ceo", role=AgentRole.CEO, authority_ceiling=30, allowed_action_types=[ActionType.SIMULATED_ALLOCATION])
    org.agents["agent-ceo"] = ceo

    proposal = ActionProposal(
        id="prop-cr-01",
        task_id="t-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=10,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Testing crypto abstraction"
    )

    decision = engine.evaluate(proposal, org)
    assert decision.result == PolicyResult.APPROVED

    # Auditor verifies using injected verifier
    valid, reason = auditor.verify_authorization_token(
        decision.authorization_token,
        proposal,
        org,
        decision=decision,
        expected_policy_version=engine.get_policy_version_hash()
    )
    assert valid is True
    assert reason is None
