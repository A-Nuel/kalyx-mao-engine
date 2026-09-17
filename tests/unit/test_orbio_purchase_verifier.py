"""Phase 14B.7 — Orbio purchase receipt/event verification tests."""
from __future__ import annotations

from src.domain.blockchain import OrbioPurchaseIntent
from src.domain.entities import Organisation
from src.domain.enums import ProviderOutcome
from src.execution.orbio_purchase import OrbioPurchaseBridge
from src.settlement.orbio_purchase_verifier import (
    OrbioPurchaseEvidence,
    OrbioPurchaseVerifier,
    PurchaseVerificationCode,
    PurchaseVerificationResult,
    extract_evidence_from_execution,
)
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider


def _intent(**overrides) -> OrbioPurchaseIntent:
    base = dict(
        tenant_id="tenant-a",
        organisation_id="org-a",
        mission_id="m-1",
        operation_id="cop-ver-1",
        chain_id=46630,
        network="robinhood-testnet",
        usdg_in=3_000_000,
        min_credit_out=2_500_000,
        beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        max_fills=5,
        amount_credits=3,
        idempotency_key="org-a:cop-ver-1",
        policy_decision_id="dec-ver-1",
        authorization_token_hash="tok-ver",
    )
    base.update(overrides)
    return OrbioPurchaseIntent(**base)


def _org() -> Organisation:
    return Organisation(id="org-a", mission="v", tenant_id="tenant-a", treasury_balance=50)


def _run_success(usdg_in: int = 3_000_000, min_credit_out: int = 2_500_000):
    intent = _intent(usdg_in=usdg_in, min_credit_out=min_credit_out)
    bridge = OrbioPurchaseBridge()
    prep, op = bridge.prepare_and_create_operation(intent, _org())
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 50_000_000)
    result = provider.execute(op)
    return intent, op, result, provider


def test_valid_evidence_verified():
    intent, op, result, _ = _run_success()
    verifier = OrbioPurchaseVerifier()
    report = verifier.verify_execution(intent, result, operation=op)
    assert report.result == PurchaseVerificationResult.VERIFIED
    assert report.is_verified()
    assert PurchaseVerificationCode.OK.value in report.codes
    assert report.intent_hash == intent.compute_purchase_intent_hash()


def test_wrong_intent_hash_rejected():
    intent, op, result, _ = _run_success()
    evidence = extract_evidence_from_execution(result, operation=op, intent=intent)
    assert evidence is not None
    tampered = OrbioPurchaseEvidence(
        **{**evidence.__dict__, "purchase_intent_hash": "00" * 32}
    )
    report = OrbioPurchaseVerifier().verify(intent, tampered, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert report.result == PurchaseVerificationResult.REJECTED
    assert PurchaseVerificationCode.WRONG_INTENT_HASH.value in report.codes


def test_wrong_exchange_rejected():
    intent, op, result, _ = _run_success()
    evidence = extract_evidence_from_execution(result, operation=op, intent=intent)
    assert evidence is not None
    tampered = OrbioPurchaseEvidence(
        **{**evidence.__dict__, "exchange_contract": "0x1111111111111111111111111111111111111111"}
    )
    report = OrbioPurchaseVerifier().verify(intent, tampered, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert PurchaseVerificationCode.WRONG_EXCHANGE.value in report.codes


def test_wrong_chain_rejected():
    intent, op, result, _ = _run_success()
    evidence = extract_evidence_from_execution(result, operation=op, intent=intent)
    assert evidence is not None
    tampered = OrbioPurchaseEvidence(**{**evidence.__dict__, "chain_id": 1})
    report = OrbioPurchaseVerifier().verify(intent, tampered, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert PurchaseVerificationCode.WRONG_CHAIN.value in report.codes


def test_wrong_network_rejected():
    intent, op, result, _ = _run_success()
    evidence = extract_evidence_from_execution(result, operation=op, intent=intent)
    assert evidence is not None
    tampered = OrbioPurchaseEvidence(**{**evidence.__dict__, "network": "ethereum"})
    report = OrbioPurchaseVerifier().verify(intent, tampered, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert PurchaseVerificationCode.WRONG_NETWORK.value in report.codes


def test_wrong_beneficiary_rejected():
    intent, op, result, _ = _run_success()
    evidence = extract_evidence_from_execution(result, operation=op, intent=intent)
    assert evidence is not None
    tampered = OrbioPurchaseEvidence(
        **{
            **evidence.__dict__,
            "beneficiary": "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
        }
    )
    report = OrbioPurchaseVerifier().verify(intent, tampered, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert PurchaseVerificationCode.WRONG_BENEFICIARY.value in report.codes


def test_wrong_usdg_amount_rejected():
    intent, op, result, _ = _run_success()
    evidence = extract_evidence_from_execution(result, operation=op, intent=intent)
    assert evidence is not None
    tampered = OrbioPurchaseEvidence(**{**evidence.__dict__, "usdg_spent": 999})
    report = OrbioPurchaseVerifier().verify(intent, tampered, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert PurchaseVerificationCode.WRONG_USDG_AMOUNT.value in report.codes


def test_credit_below_minimum_rejected():
    intent, op, result, _ = _run_success(min_credit_out=2_500_000)
    evidence = extract_evidence_from_execution(result, operation=op, intent=intent)
    assert evidence is not None
    # Force credit_out below authorized minimum
    tampered = OrbioPurchaseEvidence(**{**evidence.__dict__, "credit_out": 100})
    report = OrbioPurchaseVerifier().verify(intent, tampered, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert PurchaseVerificationCode.CREDIT_BELOW_MINIMUM.value in report.codes


def test_cross_tenant_evidence_rejected():
    intent, op, result, _ = _run_success()
    evidence = extract_evidence_from_execution(result, operation=op, intent=intent)
    assert evidence is not None
    tampered = OrbioPurchaseEvidence(**{**evidence.__dict__, "tenant_id": "tenant-attacker"})
    report = OrbioPurchaseVerifier().verify(intent, tampered, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert PurchaseVerificationCode.CROSS_TENANT.value in report.codes


def test_mutated_activation_missing_rejected():
    intent, op, result, _ = _run_success()
    evidence = extract_evidence_from_execution(result, operation=op, intent=intent)
    assert evidence is not None
    tampered = OrbioPurchaseEvidence(
        **{**evidence.__dict__, "activation_id": "", "events": []}
    )
    report = OrbioPurchaseVerifier().verify(intent, tampered, provider_outcome=ProviderOutcome.SUCCESS.value)
    assert PurchaseVerificationCode.MISSING_ACTIVATION.value in report.codes


def test_repeated_verification_idempotent():
    intent, op, result, _ = _run_success()
    verifier = OrbioPurchaseVerifier()
    r1 = verifier.verify_execution(intent, result, operation=op)
    r2 = verifier.verify_execution(intent, result, operation=op)
    assert r1.is_verified()
    assert r2.is_verified()
    assert PurchaseVerificationCode.ALREADY_VERIFIED.value in r2.codes
    assert verifier.was_verified(intent.compute_purchase_intent_hash())


def test_timeout_without_evidence_is_pending_not_verified():
    intent = _intent()
    bridge = OrbioPurchaseBridge()
    _, op = bridge.prepare_and_create_operation(intent, _org())
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 10_000_000)
    provider.set_timeout_rule(op.idempotency_key)
    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.TIMEOUT.value

    report = OrbioPurchaseVerifier().verify_execution(intent, result, operation=op)
    assert report.result == PurchaseVerificationResult.PENDING_EVIDENCE
    assert not report.is_verified()


def test_evidence_after_timeout_background_success_can_verify_via_status():
    """14B.8-compatible: TIMEOUT first, then status reveals SUCCESS + evidence → VERIFIED."""
    intent = _intent()
    bridge = OrbioPurchaseBridge()
    _, op = bridge.prepare_and_create_operation(intent, _org())
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 10_000_000)
    provider.set_timeout_rule(op.idempotency_key, provider_executes_in_background=True)

    exec_result = provider.execute(op)
    assert exec_result.outcome == ProviderOutcome.TIMEOUT.value

    verifier = OrbioPurchaseVerifier()
    pending = verifier.verify_execution(intent, exec_result, operation=op)
    assert pending.result == PurchaseVerificationResult.PENDING_EVIDENCE

    status = provider.status(op.id, idempotency_key=op.idempotency_key)
    assert status.outcome == ProviderOutcome.SUCCESS.value
    final = verifier.verify_status(intent, status, operation=op)
    assert final.result == PurchaseVerificationResult.VERIFIED


def test_does_not_trust_success_flag_without_matching_fields():
    """Provider says SUCCESS but wrong beneficiary → REJECTED."""
    intent, op, result, _ = _run_success()
    evidence = extract_evidence_from_execution(result, operation=op, intent=intent)
    assert evidence is not None
    bad = OrbioPurchaseEvidence(
        **{
            **evidence.__dict__,
            "beneficiary": "0x0000000000000000000000000000000000000001",
        }
    )
    # Even with SUCCESS outcome, fields must match
    report = OrbioPurchaseVerifier().verify(
        intent, bad, provider_outcome=ProviderOutcome.SUCCESS.value
    )
    assert report.result == PurchaseVerificationResult.REJECTED
