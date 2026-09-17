"""Phase 14B Hardening Test Suite.

Rigorously verifies all 12 authorization, escrow, settlement, idempotency,
and evidence-verification findings identified in the Codex review:
  1. Success settles escrow to EXTERNAL_SINK and marks SUCCEEDED.
  2. Failure refunds escrow to TREASURY and marks FAILED (provider failure & verification rejection).
  3. Mismatched idempotency key override raises ValueError.
  4. Non-fabricable human approvals (HMAC issuance token, revoked approval fail-closed).
  5. Generic PolicyEngine without OrbioPurchaseGovernanceRule fails closed.
  6. Verifier fails closed on missing critical receipt fields; no manufacturing from params.
  7. Simulator detects idempotency replay collision with mismatched fingerprint.
  8. Background timeout settlement runs economic preflight, never goes negative balance.
  9. Insufficient treasury balance halts loop before provider execution.
  10. Operation reflects injected provider name.
  11. Mismatched chain/network/contract deployment tuple rejected.
  12. Phase 12 BlockchainReceiptEvidence parsed and verified without dropping credit_out to 0.
"""
from __future__ import annotations

import copy
import time
import pytest

from src.agents.orbio_purchase_loop import (
    AgentLoopState,
    LoopStatus,
    OrbioPurchaseAgentLoop,
)
from src.domain.blockchain import (
    ORBIO_CREDIT_MAINNET,
    ORBIO_EXCHANGE_MAINNET,
    USDG_MAINNET,
    BlockchainReceiptEvidence,
    OrbioPurchaseIntent,
)
from src.domain.entities import ActionProposal, AgentRecord, ConsequentialOperation, Organisation
from src.domain.enums import ActionType, AgentRole, OperationState, OrgState, PolicyResult, ProviderOutcome
from src.economy.ledger import (
    DoubleEntryLedger,
    ESCROW,
    EXTERNAL_SINK,
    TREASURY,
)
from src.execution.orbio_purchase import OrbioPurchaseBridge
from src.governance.orbio_purchase_rules import (
    HumanPurchaseApproval,
    OrbioDeployment,
    OrbioPurchaseGovernanceRule,
    OrbioPurchasePolicy,
    PurchaseDecisionResult,
    PurchaseDenialCode,
)
from src.governance.policy_engine import PolicyEngine
from src.governance.rules import TargetAllowlistRule
from src.settlement.adapter import ProviderExecutionResult
from src.settlement.orbio_purchase_reconciliation import OrbioPurchaseReconciliation
from src.settlement.orbio_purchase_verifier import (
    OrbioPurchaseEvidence,
    OrbioPurchaseVerifier,
    PurchaseVerificationCode,
    PurchaseVerificationResult,
    extract_evidence_from_execution,
)
from src.settlement.orbio_simulated_exchange import (
    SimulatedOrbioExchangeProvider,
    compute_operation_fingerprint,
)


BENEFICIARY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def _intent(**overrides) -> OrbioPurchaseIntent:
    usdg_in = overrides.get("usdg_in", 3_000_000)
    min_credit_out = overrides.get("min_credit_out", max(1, (usdg_in * 80) // 100))
    base = dict(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        mission_id="m-1",
        operation_id="cop-h-1",
        chain_id=46630,
        network="robinhood-testnet",
        usdg_in=usdg_in,
        min_credit_out=min_credit_out,
        beneficiary=BENEFICIARY,
        max_fills=5,
        amount_credits=3,
        idempotency_key="org-alpha:cop-h-1",
        policy_decision_id="dec-h-1",
        authorization_token_hash="tok-h-1",
    )
    base.update(overrides)
    return OrbioPurchaseIntent(**base)


def _org() -> Organisation:
    org = Organisation(
        id="org-alpha",
        mission="hardening",
        tenant_id="tenant-alpha",
        treasury_balance=100,
        state=OrgState.EXECUTING,
    )
    org.agents["agent-1"] = AgentRecord(
        id="agent-1",
        role=AgentRole.FINANCIAL_ANALYST,
        credit_balance=100,
        authority_ceiling=100,
        allowed_action_types=[
            ActionType.INTERNAL_ANALYSIS,
            ActionType.ORBIO_CREDIT_PURCHASE,
        ],
    )
    return org


def _loop(
    *,
    initial_treasury: int = 100,
    target_credit: int = 2_500_000,
    usdg_balance: int = 50_000_000,
    provider_name: str = "orbio-exchange-simulated",
) -> tuple[OrbioPurchaseAgentLoop, SimulatedOrbioExchangeProvider, DoubleEntryLedger]:
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=5_000_000,
        absolute_usdg_ceiling=25_000_000,
    )
    bridge = OrbioPurchaseBridge(purchase_policy=policy)
    provider = SimulatedOrbioExchangeProvider(name=provider_name)
    provider.set_usdg_balance("org-alpha", usdg_balance)
    ledger = DoubleEntryLedger(initial_treasury=initial_treasury)
    verifier = OrbioPurchaseVerifier()
    reconciler = OrbioPurchaseReconciliation(
        provider=provider,
        ledger=ledger,
        verifier=verifier,
    )
    state = AgentLoopState(
        agent_id="agent-h",
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        mission_id="mission-h",
        objective="Hardening test loop",
        target_credit=target_credit,
    )
    loop = OrbioPurchaseAgentLoop(
        state=state,
        org=_org(),
        policy=policy,
        bridge=bridge,
        provider=provider,
        verifier=verifier,
        reconciler=reconciler,
        ledger=ledger,
        default_beneficiary=BENEFICIARY,
    )
    return loop, provider, ledger


# ===========================================================================
# Finding 1: Escrow Settlement on Verified Success
# ===========================================================================

def test_finding_1_success_settles_escrow_to_external_sink():
    """Verified purchase success transfers ESCROW -> EXTERNAL_SINK and transitions to SUCCEEDED."""
    loop, _, ledger = _loop(initial_treasury=100, target_credit=2_500_000)
    result = loop.step()

    assert result.status == LoopStatus.OBJECTIVE_COMPLETE
    assert result.verification_result == "VERIFIED"
    assert result.operation is not None
    assert result.operation.state == OperationState.SUCCEEDED
    assert result.operation.provider_reference is not None

    amount = result.operation.amount
    assert amount > 0
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == amount
    assert ledger.get_balance(TREASURY) == 100 - amount
    # Double-entry invariant holds
    assert ledger.get_balance(TREASURY) + ledger.get_balance(ESCROW) + ledger.get_balance(EXTERNAL_SINK) == 100


# ===========================================================================
# Finding 2: Escrow Refund on Provider Failure & Verification Rejection
# ===========================================================================

def test_finding_2_provider_failure_refunds_escrow_to_treasury():
    """Provider failure refunds ESCROW -> TREASURY and marks operation FAILED."""
    loop, provider, ledger = _loop(initial_treasury=100)
    proposal = loop.propose_purchase(usdg_in=3_000_000, min_credit_out=2_500_000)
    provider.set_failure_rule(proposal.intent.idempotency_key, "Simulated network revert")

    result = loop.step()

    assert result.status == LoopStatus.STOPPED_FAILURE
    assert result.operation is not None
    assert result.operation.state == OperationState.FAILED
    assert "Simulated network revert" in (result.operation.error_message or "")

    # Escrow fully refunded
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0
    assert ledger.get_balance(TREASURY) == 100


def test_finding_2_verification_rejection_refunds_escrow_to_treasury():
    """Verification rejection refunds ESCROW -> TREASURY and marks operation FAILED."""
    loop, provider, ledger = _loop(initial_treasury=100)

    # Corrupt the provider's execution outcome to return wrong credit_out (below minimum)
    real_exec = provider.execute
    def corrupt_exec(op):
        res = real_exec(op)
        tampered_raw = dict(res.raw_response)
        tampered_raw["credit_out"] = 10  # Way below 2_500_000
        return ProviderExecutionResult(
            provider_name=res.provider_name,
            operation_id=res.operation_id,
            outcome=res.outcome,
            provider_reference=res.provider_reference,
            raw_response=tampered_raw,
            evidence_hash=res.evidence_hash,
        )
    provider.execute = corrupt_exec

    result = loop.step()

    assert result.status == LoopStatus.STOPPED_FAILURE
    assert result.operation is not None
    assert result.operation.state == OperationState.FAILED
    assert "CREDIT_BELOW_MINIMUM" in (result.operation.error_message or "")

    # Escrow refunded to treasury
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0
    assert ledger.get_balance(TREASURY) == 100


# ===========================================================================
# Finding 3: Strict Idempotency Key Binding
# ===========================================================================

def test_finding_3_mismatched_idempotency_key_override_raises():
    """create_operation_from_preparation rejects any idempotency_key override mismatch."""
    intent = _intent()
    bridge = OrbioPurchaseBridge()
    prep = bridge.prepare(intent)
    assert prep.is_authorized

    # Matching idempotency key succeeds
    op = bridge.create_operation_from_preparation(
        prep, _org(), idempotency_key=intent.idempotency_key
    )
    assert op.idempotency_key == intent.idempotency_key

    # Mismatched idempotency key raises ValueError
    with pytest.raises(ValueError, match="does not match authorized purchase intent"):
        bridge.create_operation_from_preparation(
            prep, _org(), idempotency_key="attacker-supplied-different-key"
        )


# ===========================================================================
# Finding 4: Non-Fabricable Human Approvals
# ===========================================================================

def test_finding_4_caller_fabricated_approval_rejected():
    """Policy rejects caller-fabricated HumanPurchaseApproval lacking policy issuance HMAC."""
    policy = OrbioPurchasePolicy(autonomous_usdg_ceiling=1_000_000, absolute_usdg_ceiling=25_000_000)
    intent = _intent(usdg_in=3_000_000)  # Requires human approval

    # Caller fabricates approval without policy issuance
    fabricated = HumanPurchaseApproval(
        approval_id="app-fake-1",
        intent_hash=intent.compute_purchase_intent_hash(),
        tenant_id=intent.tenant_id,
        organisation_id=intent.organisation_id,
        operator_id="malicious-caller",
        issued_at=time.time(),
        expires_at=time.time() + 3600,
        issuance_token="fabricated-token",
    )
    decision = policy.evaluate(intent, human_approval=fabricated)
    assert decision.result == PurchaseDecisionResult.DENY
    assert PurchaseDenialCode.UNAUTHORIZED_HUMAN_APPROVAL.value in decision.denial_codes


def test_finding_4_revoked_approval_rejected():
    """Policy rejects previously issued approval after revocation."""
    policy = OrbioPurchasePolicy(autonomous_usdg_ceiling=1_000_000, absolute_usdg_ceiling=25_000_000)
    intent = _intent(usdg_in=3_000_000)

    # Authoritative issuance by policy
    approval = policy.issue_human_approval(
        intent,
        operator_id="treasurer-bob",
    )
    # Valid before revocation
    d1 = policy.evaluate(intent, human_approval=approval)
    assert d1.result == PurchaseDecisionResult.ALLOW

    # Revoke approval
    assert policy.revoke_human_approval(approval.approval_id) is True

    # Re-evaluation fails closed with HUMAN_APPROVAL_REVOKED
    d2 = policy.evaluate(intent, human_approval=approval)
    assert d2.result == PurchaseDecisionResult.DENY
    assert PurchaseDenialCode.HUMAN_APPROVAL_REVOKED.value in d2.denial_codes


# ===========================================================================
# Finding 5: Generic PolicyEngine Fail-Closed without Orbio Rule
# ===========================================================================

def test_finding_5_generic_policy_engine_fails_closed():
    """Generic PolicyEngine without OrbioPurchaseGovernanceRule rejects ORBIO_CREDIT_PURCHASE."""
    engine = PolicyEngine(rules=[TargetAllowlistRule()])
    prop = ActionProposal(
        id="prop-test-1",
        task_id="task-1",
        proposing_agent_id="agent-1",
        action_type=ActionType.ORBIO_CREDIT_PURCHASE,
        target="orbio://exchange/0x1234567890123456789012345678901234567890",
        parameters={"amount": 10},
        requested_credits=10,
        expected_value_score=0.5,
        risk_assessment="low",
        rationale="Testing fail closed",
    )
    decision = engine.evaluate(prop, _org())
    assert decision.result == PolicyResult.REJECTED
    assert "OrbioPurchaseGovernanceRule is required" in (decision.violated_rule_description or "")

    # Adding OrbioPurchaseGovernanceRule allows proper evaluation
    orbio_rule = OrbioPurchaseGovernanceRule(OrbioPurchasePolicy())
    engine_with_rule = PolicyEngine(rules=[TargetAllowlistRule(), orbio_rule])
    intent = _intent()
    prop.parameters = {
        "chain_id": intent.chain_id,
        "network": intent.network,
        "usdg_in": intent.usdg_in,
        "min_credit_out": intent.min_credit_out,
        "beneficiary": intent.beneficiary,
        "max_fills": intent.max_fills,
        "exchange_contract": intent.exchange_contract,
        "purchase_intent_hash": intent.compute_purchase_intent_hash(),
    }
    decision2 = engine_with_rule.evaluate(prop, _org())
    assert decision2.result == PolicyResult.APPROVED


# ===========================================================================
# Finding 6: Verifier Fails Closed on Missing Critical Receipt Evidence
# ===========================================================================

def test_finding_6_missing_critical_receipt_evidence_rejected():
    """Verifier rejects evidence missing any critical identity fields as MISSING_EVIDENCE."""
    intent = _intent()
    verifier = OrbioPurchaseVerifier()

    # Provider returned receipt missing exchange_contract
    bad_evidence = OrbioPurchaseEvidence(
        purchase_intent_hash=intent.compute_purchase_intent_hash(),
        provider_reference="0x" + "a" * 64,
        chain_id=46630,
        network="robinhood-testnet",
        exchange_contract="",  # Missing!
        usdg_spent=3_000_000,
        credit_out=2_500_000,
        min_credit_out=2_500_000,
        beneficiary=BENEFICIARY,
        activation_id="act-1",
    )
    report = verifier.verify(intent, bad_evidence, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert report.result == PurchaseVerificationResult.REJECTED
    assert PurchaseVerificationCode.MISSING_EVIDENCE.value in report.codes

    # Missing chain_id
    bad_chain = OrbioPurchaseEvidence(
        purchase_intent_hash=intent.compute_purchase_intent_hash(),
        provider_reference="0x" + "a" * 64,
        chain_id=None,  # Missing!
        network="robinhood-testnet",
        exchange_contract=ORBIO_EXCHANGE_MAINNET,
        usdg_spent=3_000_000,
        credit_out=2_500_000,
        min_credit_out=2_500_000,
        beneficiary=BENEFICIARY,
        activation_id="act-1",
    )
    report2 = verifier.verify(intent, bad_chain, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert report2.result == PurchaseVerificationResult.REJECTED
    assert PurchaseVerificationCode.MISSING_EVIDENCE.value in report2.codes


def test_finding_6_extractor_does_not_manufacture_from_params():
    """extract_evidence_from_execution does not manufacture identity from operation.parameters."""
    op = ConsequentialOperation(
        id="cop-1",
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        proposal_id="p-1",
        decision_id="d-1",
        idempotency_key="key-1",
        action_type=ActionType.ORBIO_CREDIT_PURCHASE,
        target="orbio://test",
        parameters={
            "exchange_contract": "0xManufacturedExchange",
            "beneficiary": "0xManufacturedBeneficiary",
            "chain_id": 99999,
            "network": "manufactured-net",
        },
        provider_name="test-prov",
    )
    result = ProviderExecutionResult(
        provider_name="test-prov",
        operation_id=op.id,
        outcome=ProviderOutcome.SUCCESS.value,
        provider_reference="0x123",
        raw_response={"usdg_spent": 100, "credit_out": 100, "activation_id": "act-1"},
    )
    evidence = extract_evidence_from_execution(result, operation=op)
    assert evidence is not None
    # Must NOT copy parameters into receipt evidence
    assert evidence.exchange_contract == ""
    assert evidence.beneficiary == ""
    assert evidence.chain_id is None
    assert evidence.network is None


# ===========================================================================
# Finding 7: Simulator Replay Collision with Mismatched Fingerprint
# ===========================================================================

def test_finding_7_simulator_replay_collision_with_different_fingerprint():
    """Simulator rejects idempotency replay collision when operation fingerprint does not match."""
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-alpha", 50_000_000)

    intent1 = _intent(usdg_in=1_000_000)
    bridge = OrbioPurchaseBridge()
    _, op1 = bridge.prepare_and_create_operation(intent1, _org())
    res1 = provider.execute(op1)
    assert res1.outcome == ProviderOutcome.SUCCESS.value

    # Second operation with SAME idempotency key but DIFFERENT parameters (e.g. usdg_in=2_000_000)
    intent2 = _intent(usdg_in=2_000_000, idempotency_key=op1.idempotency_key)
    _, op2 = bridge.prepare_and_create_operation(intent2, _org())

    res2 = provider.execute(op2)
    assert res2.outcome != ProviderOutcome.SUCCESS.value
    assert "fingerprint does not match" in (res2.error_message or "").lower()


# ===========================================================================
# Finding 8: Background Timeout Settlement Preflight Checks
# ===========================================================================

def test_finding_8_background_timeout_insufficient_usdg_settles_failure():
    """Background timeout settlement checks economic preflight and settles failure without going negative."""
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-alpha", 0)  # Insufficient balance!

    intent = _intent(usdg_in=3_000_000)
    bridge = OrbioPurchaseBridge()
    _, op = bridge.prepare_and_create_operation(intent, _org())
    provider.set_timeout_rule(op.idempotency_key, provider_executes_in_background=True)

    # Initial call triggers timeout
    res = provider.execute(op)
    assert res.outcome == ProviderOutcome.TIMEOUT.value

    # Subsequent status query reveals background settlement outcome
    status = provider.status(op.id, idempotency_key=op.idempotency_key)
    assert status.outcome == ProviderOutcome.FAILURE.value
    assert "Insufficient USDG" in (status.error_message or "")
    # Balance remains 0, never negative
    assert provider.get_usdg_balance("org-alpha") == 0


# ===========================================================================
# Finding 9: Insufficient Treasury Balance Blocks Execution
# ===========================================================================

def test_finding_9_insufficient_treasury_blocks_loop_before_provider():
    """Agent loop halts before provider call if treasury cannot fund required escrow."""
    # Treasury has 0 balance, but purchase requires 3 credits
    loop, provider, ledger = _loop(initial_treasury=0)

    result = loop.step()

    assert result.status == LoopStatus.STOPPED_SPEND_LIMIT
    assert result.operation is not None
    assert result.operation.state == OperationState.FAILED
    assert "Insufficient treasury balance" in (result.operation.error_message or "")
    assert provider.submission_attempts.get(result.operation.idempotency_key, 0) == 0
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0


# ===========================================================================
# Finding 10: Operation Reflects Injected Provider Name
# ===========================================================================

def test_finding_10_operation_reflects_injected_provider_name():
    """Operation created in agent loop binds to the injected provider's name."""
    loop, provider, _ = _loop(provider_name="custom-orbio-exchange-v2")
    result = loop.step()

    assert result.operation is not None
    assert result.operation.provider_name == "custom-orbio-exchange-v2"


# ===========================================================================
# Finding 11: Deployment Tuple Allowlist Validation
# ===========================================================================

def test_finding_11_mismatched_deployment_tuple_rejected():
    """Policy rejects any cross-deployment or unauthorized chain/exchange/token combination."""
    policy = OrbioPurchasePolicy()

    # Valid Robinhood testnet intent
    valid = _intent()
    assert policy.evaluate(valid).result == PurchaseDecisionResult.ALLOW

    # Tamper with exchange contract to use unauthorized contract address
    tampered_exchange = _intent(exchange_contract="0x000000000000000000000000000000000000dead")
    d1 = policy.evaluate(tampered_exchange)
    assert d1.result == PurchaseDecisionResult.DENY
    assert (
        PurchaseDenialCode.UNAUTHORIZED_DEPLOYMENT.value in d1.denial_codes
        or PurchaseDenialCode.UNAUTHORIZED_EXCHANGE.value in d1.denial_codes
    )

    # Tamper with network name (cross-network mismatch)
    tampered_network = _intent(network="ethereum-sepolia")
    d2 = policy.evaluate(tampered_network)
    assert d2.result == PurchaseDecisionResult.DENY
    assert (
        PurchaseDenialCode.UNAUTHORIZED_DEPLOYMENT.value in d2.denial_codes
        or PurchaseDenialCode.UNAUTHORIZED_NETWORK.value in d2.denial_codes
    )


# ===========================================================================
# Finding 12: Phase 12 BlockchainReceiptEvidence Verified Without Zeroing Credit
# ===========================================================================

def test_finding_12_phase12_blockchain_receipt_evidence_verified():
    """Phase 12 BlockchainReceiptEvidence is parsed and verified without dropping credit_out to 0."""
    intent = _intent(usdg_in=3_000_000, min_credit_out=2_500_000)
    verifier = OrbioPurchaseVerifier()

    receipt = BlockchainReceiptEvidence(
        provider="evm",
        network="robinhood-testnet",
        chain_id=46630,
        transaction_hash="0x" + "b" * 64,
        status="confirmed",
        sender="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        recipient=ORBIO_EXCHANGE_MAINNET,
        amount_wei=3_000_000,
        intent_hash=intent.compute_purchase_intent_hash(),
        raw_receipt={
            "credit_out": 2_940_000,
            "activation_id": "act-blockchain-test-1",
            "beneficiary": BENEFICIARY,
            "events": [
                {
                    "name": "Activated",
                    "activation_id": "act-blockchain-test-1",
                    "credit_out": 2_940_000,
                    "beneficiary": BENEFICIARY,
                }
            ],
        },
    )

    exec_result = ProviderExecutionResult(
        provider_name="blockchain-evm",
        operation_id="cop-h-1",
        outcome=ProviderOutcome.SUCCESS.value,
        provider_reference=receipt.transaction_hash,
        raw_response=receipt.model_dump(),
        evidence_hash=receipt.compute_evidence_hash(),
    )

    evidence = extract_evidence_from_execution(exec_result, intent=intent)
    assert evidence is not None
    assert evidence.credit_out == 2_940_000  # Explicitly NOT 0
    assert evidence.usdg_spent == 3_000_000
    assert evidence.activation_id == "act-blockchain-test-1"
    assert evidence.exchange_contract.lower() == ORBIO_EXCHANGE_MAINNET.lower()
    assert evidence.beneficiary.lower() == BENEFICIARY.lower()

    report = verifier.verify(intent, evidence, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert report.result == PurchaseVerificationResult.VERIFIED
    assert report.is_verified()
    assert PurchaseVerificationCode.OK.value in report.codes
