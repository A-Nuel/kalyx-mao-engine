"""Phase 14B.5 — Adversarial security tests for Orbio CREDIT purchase path.

Attack classes:
1. Intent / calldata tampering after authorization
2. Policy bypass (wrong exchange, chain, beneficiary, ceilings)
3. Human-approval reuse on modified intents
4. Cross-tenant / cross-org isolation
5. Post-auth operation parameter mutation
6. Double-spend / idempotency abuse via simulated exchange
7. Economic / slippage attacks
8. Deadline and approval expiry races

No RPC. No real keys. Offline only.
"""
from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from src.domain.blockchain import (
    ORBIO_EXCHANGE_MAINNET,
    OrbioPurchaseIntent,
    encode_buy_and_activate_calldata,
)
from src.domain.entities import Organisation
from src.domain.enums import ProviderOutcome
from src.domain.exceptions import PolicyViolationError, UnauthorizedActionError
from src.execution.orbio_purchase import OrbioPurchaseBridge
from src.governance.orbio_purchase_rules import (
    OrbioPurchasePolicy,
    PurchaseDecisionResult,
    PurchaseDenialCode,
)
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider


def _intent(**overrides) -> OrbioPurchaseIntent:
    base = dict(
        tenant_id="tenant-a",
        organisation_id="org-a",
        mission_id="m-1",
        operation_id="cop-adv-1",
        chain_id=46630,
        network="robinhood-testnet",
        usdg_in=3_000_000,
        min_credit_out=2_500_000,
        beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        max_fills=5,
        amount_credits=3,
        idempotency_key="org-a:cop-adv-1",
        policy_decision_id="dec-adv-1",
        authorization_token_hash="tok-adv",
    )
    base.update(overrides)
    return OrbioPurchaseIntent(**base)


def _org(org_id: str = "org-a", tenant_id: str = "tenant-a") -> Organisation:
    return Organisation(id=org_id, mission="adv", tenant_id=tenant_id, treasury_balance=100)


# ===========================================================================
# 1. Intent / calldata integrity
# ===========================================================================

def test_calldata_changes_when_usdg_in_tampered():
    a = _intent(usdg_in=1_000_000)
    b = _intent(usdg_in=9_000_000)
    assert a.encode_calldata() != b.encode_calldata()
    assert a.compute_purchase_intent_hash() != b.compute_purchase_intent_hash()


def test_calldata_changes_when_beneficiary_tampered():
    a = _intent(beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
    b = _intent(beneficiary="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")
    assert a.encode_calldata() != b.encode_calldata()


def test_projection_recipient_is_always_exchange_not_beneficiary():
    """Attack: agent tries to send value to self by setting beneficiary as recipient."""
    intent = _intent(beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
    bc = intent.to_blockchain_intent()
    assert bc.recipient == intent.exchange_contract.lower()
    assert bc.recipient != intent.beneficiary.lower()
    assert bc.amount_wei == 0  # no native value transfer


def test_agent_cannot_supply_arbitrary_calldata_field():
    """OrbioPurchaseIntent has no free-form data_payload — only encode_calldata()."""
    intent = _intent()
    assert not hasattr(intent, "data_payload") or intent.encode_calldata().startswith("0x6ebadb6e")
    # Forged selector must not appear from legitimate encoding
    assert not intent.encode_calldata().startswith("0xdeadbeef")


def test_invalid_beneficiary_rejected_at_model_layer():
    with pytest.raises(ValidationError):
        _intent(beneficiary="not-an-address")


def test_zero_zero_purchase_rejected_at_model_layer():
    with pytest.raises(ValidationError):
        _intent(usdg_in=0, min_credit_out=0)


# ===========================================================================
# 2. Policy bypass attempts
# ===========================================================================

def test_cannot_point_at_attacker_exchange():
    policy = OrbioPurchasePolicy()
    decision = policy.evaluate(
        _intent(exchange_contract="0x1111111111111111111111111111111111111111")
    )
    assert decision.is_denied()
    assert PurchaseDenialCode.UNAUTHORIZED_EXCHANGE.value in decision.denial_codes


def test_cannot_use_mainnet_ethereum_chain():
    policy = OrbioPurchasePolicy()
    decision = policy.evaluate(_intent(chain_id=1, network="ethereum"))
    assert PurchaseDenialCode.UNAUTHORIZED_NETWORK.value in decision.denial_codes


def test_cannot_exceed_absolute_ceiling_even_with_human_approval():
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=5_000_000,
        absolute_usdg_ceiling=10_000_000,
    )
    intent = _intent(usdg_in=50_000_000, min_credit_out=1)
    approval = policy.issue_human_approval(intent)
    decision = policy.evaluate(intent, human_approval=approval)
    assert decision.is_denied()
    assert PurchaseDenialCode.MAX_USDG_EXCEEDED.value in decision.denial_codes


def test_unbounded_slippage_denied():
    policy = OrbioPurchasePolicy()
    decision = policy.evaluate(_intent(usdg_in=1_000_000, min_credit_out=0))
    assert PurchaseDenialCode.SLIPPAGE_BOUND_INVALID.value in decision.denial_codes


def test_min_credit_out_greater_than_usdg_in_denied():
    policy = OrbioPurchasePolicy()
    decision = policy.evaluate(_intent(usdg_in=1_000_000, min_credit_out=2_000_000))
    assert PurchaseDenialCode.SLIPPAGE_BOUND_INVALID.value in decision.denial_codes


def test_beneficiary_allowlist_blocks_unknown_recipient():
    policy = OrbioPurchasePolicy(
        require_beneficiary_allowlist=True,
        allowed_beneficiaries={"0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"},
    )
    decision = policy.evaluate(
        _intent(beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
    )
    assert PurchaseDenialCode.UNAUTHORIZED_BENEFICIARY.value in decision.denial_codes


def test_bridge_refuses_to_create_operation_on_deny():
    bridge = OrbioPurchaseBridge()
    prep = bridge.prepare(_intent(chain_id=999, network="evil-net"))
    assert not prep.is_authorized
    with pytest.raises(PolicyViolationError):
        bridge.create_operation_from_preparation(prep, _org())


# ===========================================================================
# 3. Human approval binding
# ===========================================================================

def test_human_approval_cannot_be_replayed_on_higher_amount():
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=5_000_000,
        absolute_usdg_ceiling=25_000_000,
    )
    original = _intent(usdg_in=8_000_000, min_credit_out=7_000_000)
    approval = policy.issue_human_approval(original, operator_id="ops")

    escalated = original.model_copy(update={"usdg_in": 20_000_000, "min_credit_out": 15_000_000})
    decision = policy.evaluate(escalated, human_approval=approval)
    assert decision.is_denied()
    assert PurchaseDenialCode.HUMAN_APPROVAL_INTENT_MISMATCH.value in decision.denial_codes


def test_human_approval_cannot_be_replayed_on_different_beneficiary():
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=5_000_000,
        absolute_usdg_ceiling=25_000_000,
    )
    original = _intent(usdg_in=8_000_000, min_credit_out=7_000_000)
    approval = policy.issue_human_approval(original)

    diverted = original.model_copy(
        update={"beneficiary": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"}
    )
    decision = policy.evaluate(diverted, human_approval=approval)
    assert decision.is_denied()
    assert PurchaseDenialCode.HUMAN_APPROVAL_INTENT_MISMATCH.value in decision.denial_codes


def test_expired_human_approval_cannot_authorize():
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=5_000_000,
        absolute_usdg_ceiling=25_000_000,
        human_approval_ttl_seconds=30.0,
    )
    intent = _intent(usdg_in=8_000_000, min_credit_out=7_000_000)
    now = 1_800_000_000.0
    approval = policy.issue_human_approval(intent, current_time=now)
    decision = policy.evaluate(intent, human_approval=approval, current_time=now + 31)
    assert decision.is_denied()
    assert PurchaseDenialCode.HUMAN_APPROVAL_EXPIRED.value in decision.denial_codes


def test_revoked_approval_requires_confirmation_again():
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=5_000_000,
        absolute_usdg_ceiling=25_000_000,
    )
    intent = _intent(usdg_in=8_000_000, min_credit_out=7_000_000)
    approval = policy.issue_human_approval(intent)
    assert policy.revoke_human_approval(intent.compute_purchase_intent_hash()) is True
    decision = policy.evaluate(intent)  # no approval passed, store cleared
    assert decision.result == PurchaseDecisionResult.HUMAN_CONFIRMATION_REQUIRED


# ===========================================================================
# 4. Cross-tenant / cross-org isolation
# ===========================================================================

def test_bridge_rejects_org_mismatch():
    bridge = OrbioPurchaseBridge()
    with pytest.raises(UnauthorizedActionError):
        bridge.prepare_and_create_operation(
            _intent(organisation_id="org-attacker"),
            _org(org_id="org-a"),
        )


def test_bridge_rejects_tenant_mismatch():
    bridge = OrbioPurchaseBridge()
    with pytest.raises(UnauthorizedActionError):
        bridge.prepare_and_create_operation(
            _intent(tenant_id="tenant-attacker"),
            _org(tenant_id="tenant-a"),
        )


def test_human_approval_from_other_tenant_rejected():
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=5_000_000,
        absolute_usdg_ceiling=25_000_000,
    )
    intent_a = _intent(tenant_id="tenant-a", organisation_id="org-a", usdg_in=8_000_000, min_credit_out=7_000_000)
    approval_a = policy.issue_human_approval(intent_a)

    # Same economic params but different tenant — hash differs; approval invalid
    intent_b = intent_a.model_copy(update={"tenant_id": "tenant-b", "organisation_id": "org-b"})
    decision = policy.evaluate(intent_b, human_approval=approval_a)
    assert decision.is_denied()


def test_simulated_exchange_balances_are_per_org():
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 10_000_000)
    provider.set_usdg_balance("org-b", 1_000_000)

    bridge = OrbioPurchaseBridge()
    _, op_a = bridge.prepare_and_create_operation(_intent(), _org())
    result = provider.execute(op_a)
    assert result.outcome == ProviderOutcome.SUCCESS.value
    assert provider.get_usdg_balance("org-a") == 7_000_000
    assert provider.get_usdg_balance("org-b") == 1_000_000  # untouched


# ===========================================================================
# 5. Post-authorization mutation
# ===========================================================================

def test_verify_detects_data_payload_swap():
    bridge = OrbioPurchaseBridge()
    prep, op = bridge.prepare_and_create_operation(_intent(), _org())
    # Attacker swaps calldata to a different buyAndActivate encoding
    forged = encode_buy_and_activate_calldata(
        usdg_in=99_000_000,
        min_credit_out=1,
        beneficiary="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        max_fills=1,
    )
    op.parameters = {**op.parameters, "data_payload": forged}
    ok, reason = bridge.verify_operation_matches_intent(op, prep.purchase_intent)
    assert not ok
    assert "data_payload" in (reason or "")


def test_verify_detects_recipient_swap_to_attacker():
    bridge = OrbioPurchaseBridge()
    prep, op = bridge.prepare_and_create_operation(_intent(), _org())
    op.parameters = {
        **op.parameters,
        "recipient": "0x1111111111111111111111111111111111111111",
    }
    ok, reason = bridge.verify_operation_matches_intent(op, prep.purchase_intent)
    assert not ok
    assert "recipient" in (reason or "")


def test_verify_detects_intent_hash_rewrite():
    bridge = OrbioPurchaseBridge()
    prep, op = bridge.prepare_and_create_operation(_intent(), _org())
    op.parameters = {**op.parameters, "purchase_intent_hash": "ff" * 32}
    ok, _ = bridge.verify_operation_matches_intent(op, prep.purchase_intent)
    assert not ok


def test_authorized_calldata_matches_projection():
    bridge = OrbioPurchaseBridge()
    prep, op = bridge.prepare_and_create_operation(_intent(), _org())
    assert op.parameters["data_payload"] == prep.blockchain_intent.data_payload
    assert op.parameters["data_payload"] == prep.purchase_intent.encode_calldata()
    assert op.parameters["recipient"] == ORBIO_EXCHANGE_MAINNET.lower()


# ===========================================================================
# 6. Double-spend / idempotency on simulated exchange
# ===========================================================================

def test_double_submit_does_not_double_debit():
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 20_000_000)
    bridge = OrbioPurchaseBridge()
    _, op = bridge.prepare_and_create_operation(_intent(), _org())

    r1 = provider.execute(op)
    r2 = provider.execute(op)
    assert r1.outcome == ProviderOutcome.SUCCESS.value
    assert r2.raw_response.get("duplicate_submission") is True
    assert provider.get_usdg_balance("org-a") == 17_000_000
    assert provider.submission_attempts[op.idempotency_key] == 2


def test_different_idempotency_keys_are_independent_spends():
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 20_000_000)
    bridge = OrbioPurchaseBridge()

    i1 = _intent(idempotency_key="org-a:op-1", operation_id="op-1")
    i2 = _intent(idempotency_key="org-a:op-2", operation_id="op-2")
    _, op1 = bridge.prepare_and_create_operation(i1, _org())
    _, op2 = bridge.prepare_and_create_operation(i2, _org())

    assert provider.execute(op1).outcome == ProviderOutcome.SUCCESS.value
    assert provider.execute(op2).outcome == ProviderOutcome.SUCCESS.value
    assert provider.get_usdg_balance("org-a") == 14_000_000


# ===========================================================================
# 7. Economic / timeout edge cases
# ===========================================================================

def test_insufficient_balance_cannot_be_forced_success():
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 100)  # far below 3M
    bridge = OrbioPurchaseBridge()
    _, op = bridge.prepare_and_create_operation(_intent(), _org())
    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.FAILURE.value
    assert provider.get_credit_balance("org-a") == 0


def test_timeout_without_background_leaves_no_credit():
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 10_000_000)
    bridge = OrbioPurchaseBridge()
    _, op = bridge.prepare_and_create_operation(_intent(), _org())
    provider.set_timeout_rule(op.idempotency_key)

    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.TIMEOUT.value
    assert provider.get_credit_balance("org-a") == 0
    assert provider.get_usdg_balance("org-a") == 10_000_000


def test_timeout_background_success_visible_on_status_only():
    """Caller sees TIMEOUT; reconciliation via status reveals SUCCESS — classic UNKNOWN path."""
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 10_000_000)
    bridge = OrbioPurchaseBridge()
    _, op = bridge.prepare_and_create_operation(_intent(), _org())
    provider.set_timeout_rule(op.idempotency_key, provider_executes_in_background=True)

    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.TIMEOUT.value
    status = provider.status(op.id, idempotency_key=op.idempotency_key)
    assert status.outcome == ProviderOutcome.SUCCESS.value
    assert provider.get_usdg_balance("org-a") == 7_000_000


def test_deadline_expiry_denies_stale_intent():
    policy = OrbioPurchasePolicy()
    past = int(time.time()) - 120
    decision = policy.evaluate(_intent(deadline=past), current_time=time.time())
    assert PurchaseDenialCode.DEADLINE_EXPIRED.value in decision.denial_codes


# ===========================================================================
# 8. End-to-end adversarial happy-path control
# ===========================================================================

def test_clean_path_still_works_after_adversarial_suite_setup():
    """Control: legitimate small purchase still succeeds under default policy."""
    policy = OrbioPurchasePolicy()
    bridge = OrbioPurchaseBridge(purchase_policy=policy)
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 10_000_000)

    prep, op = bridge.prepare_and_create_operation(_intent(), _org())
    assert prep.is_authorized
    ok, reason = bridge.verify_operation_matches_intent(op, prep.purchase_intent)
    assert ok, reason

    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.SUCCESS.value
    assert result.raw_response["events"][0]["name"] == "Activated"
