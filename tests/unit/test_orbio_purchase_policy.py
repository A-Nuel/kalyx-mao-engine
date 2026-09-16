"""Unit tests for OrbioPurchasePolicy — Phase 14B.2.

Covers autonomous allow, hard denials, human confirmation band,
intent-hash-bound approval, and determinism. No RPC / signing.
"""
from __future__ import annotations

import time

import pytest

from src.domain.blockchain import (
    ORBIO_EXCHANGE_MAINNET,
    USDG_MAINNET,
    OrbioPurchaseIntent,
)
from src.governance.orbio_purchase_rules import (
    OrbioPurchasePolicy,
    PurchaseDecisionResult,
    PurchaseDenialCode,
)


def _intent(**overrides) -> OrbioPurchaseIntent:
    base = dict(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        mission_id="m-1",
        operation_id="cop-p-1",
        chain_id=46630,
        network="robinhood-testnet",
        usdg_in=3_000_000,  # $3 — under default autonomous $5
        min_credit_out=2_500_000,
        beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        max_fills=5,
        amount_credits=3,
        idempotency_key="org-alpha:cop-p-1",
        policy_decision_id="dec-p-1",
        authorization_token_hash="tok-hash",
    )
    base.update(overrides)
    return OrbioPurchaseIntent(**base)


def _policy(**kwargs) -> OrbioPurchasePolicy:
    return OrbioPurchasePolicy(**kwargs)


# ---------------------------------------------------------------------------
# Allowed
# ---------------------------------------------------------------------------

def test_allow_within_autonomous_ceiling():
    policy = _policy()
    decision = policy.evaluate(_intent(usdg_in=3_000_000, min_credit_out=2_000_000))
    assert decision.result == PurchaseDecisionResult.ALLOW
    assert decision.is_allowed()
    assert not decision.requires_human_confirmation
    assert decision.intent_hash == _intent(usdg_in=3_000_000, min_credit_out=2_000_000).compute_purchase_intent_hash()


def test_allow_at_exact_autonomous_ceiling():
    policy = _policy(autonomous_usdg_ceiling=5_000_000)
    decision = policy.evaluate(_intent(usdg_in=5_000_000, min_credit_out=4_000_000))
    assert decision.result == PurchaseDecisionResult.ALLOW


# ---------------------------------------------------------------------------
# Denied — hard bounds
# ---------------------------------------------------------------------------

def test_deny_max_usdg_exceeded():
    policy = _policy(absolute_usdg_ceiling=10_000_000)
    decision = policy.evaluate(_intent(usdg_in=11_000_000, min_credit_out=5_000_000))
    assert decision.result == PurchaseDecisionResult.DENY
    assert PurchaseDenialCode.MAX_USDG_EXCEEDED.value in decision.denial_codes


def test_deny_wrong_exchange():
    policy = _policy()
    decision = policy.evaluate(
        _intent(exchange_contract="0x0000000000000000000000000000000000000001")
    )
    assert decision.is_denied()
    assert PurchaseDenialCode.UNAUTHORIZED_EXCHANGE.value in decision.denial_codes


def test_deny_wrong_payment_token():
    policy = _policy()
    decision = policy.evaluate(
        _intent(payment_token="0x00000000000000000000000000000000000000aa")
    )
    assert PurchaseDenialCode.UNAUTHORIZED_PAYMENT_TOKEN.value in decision.denial_codes


def test_deny_wrong_network():
    policy = _policy()
    decision = policy.evaluate(_intent(chain_id=1, network="ethereum"))
    assert PurchaseDenialCode.UNAUTHORIZED_NETWORK.value in decision.denial_codes


def test_deny_unauthorized_beneficiary_when_allowlist_required():
    policy = _policy(
        require_beneficiary_allowlist=True,
        allowed_beneficiaries={"0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"},
    )
    decision = policy.evaluate(
        _intent(beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
    )
    assert PurchaseDenialCode.UNAUTHORIZED_BENEFICIARY.value in decision.denial_codes


def test_allow_beneficiary_on_allowlist():
    addr = "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"
    policy = _policy(
        require_beneficiary_allowlist=True,
        allowed_beneficiaries={addr},
    )
    decision = policy.evaluate(_intent(beneficiary=addr))
    assert decision.is_allowed()


def test_deny_slippage_min_credit_exceeds_usdg_in():
    policy = _policy()
    decision = policy.evaluate(_intent(usdg_in=1_000_000, min_credit_out=2_000_000))
    assert PurchaseDenialCode.SLIPPAGE_BOUND_INVALID.value in decision.denial_codes


def test_deny_zero_min_credit_out():
    policy = _policy()
    decision = policy.evaluate(_intent(usdg_in=1_000_000, min_credit_out=0))
    assert PurchaseDenialCode.SLIPPAGE_BOUND_INVALID.value in decision.denial_codes


def test_deny_max_fills_exceeded():
    policy = _policy(max_fills=3)
    decision = policy.evaluate(_intent(max_fills=10))
    assert PurchaseDenialCode.MAX_FILLS_EXCEEDED.value in decision.denial_codes


def test_deny_gas_bound_exceeded():
    policy = _policy(max_gas_limit=100_000)
    decision = policy.evaluate(_intent(gas_limit=350_000))
    assert PurchaseDenialCode.GAS_BOUND_EXCEEDED.value in decision.denial_codes


def test_deny_deadline_expired():
    policy = _policy()
    past = int(time.time()) - 60
    decision = policy.evaluate(_intent(deadline=past), current_time=time.time())
    assert PurchaseDenialCode.DEADLINE_EXPIRED.value in decision.denial_codes


# ---------------------------------------------------------------------------
# Human confirmation gate
# ---------------------------------------------------------------------------

def test_human_confirmation_required_above_autonomous():
    policy = _policy(autonomous_usdg_ceiling=5_000_000, absolute_usdg_ceiling=25_000_000)
    decision = policy.evaluate(_intent(usdg_in=10_000_000, min_credit_out=8_000_000))
    assert decision.result == PurchaseDecisionResult.HUMAN_CONFIRMATION_REQUIRED
    assert decision.requires_human_confirmation
    assert PurchaseDenialCode.MISSING_HUMAN_APPROVAL.value in decision.denial_codes


def test_human_approval_allows_purchase():
    policy = _policy(autonomous_usdg_ceiling=5_000_000, absolute_usdg_ceiling=25_000_000)
    intent = _intent(usdg_in=10_000_000, min_credit_out=8_000_000)
    approval = policy.issue_human_approval(intent, operator_id="ops-1")
    decision = policy.evaluate(intent, human_approval=approval)
    assert decision.result == PurchaseDecisionResult.ALLOW
    assert any("Human approval" in r for r in decision.reasons)


def test_human_approval_stored_and_looked_up_by_hash():
    policy = _policy(autonomous_usdg_ceiling=5_000_000, absolute_usdg_ceiling=25_000_000)
    intent = _intent(usdg_in=10_000_000, min_credit_out=8_000_000)
    policy.issue_human_approval(intent, operator_id="ops-1")
    # No explicit approval arg — should find stored one
    decision = policy.evaluate(intent)
    assert decision.is_allowed()


def test_human_approval_rejected_for_modified_intent():
    policy = _policy(autonomous_usdg_ceiling=5_000_000, absolute_usdg_ceiling=25_000_000)
    intent = _intent(usdg_in=10_000_000, min_credit_out=8_000_000)
    approval = policy.issue_human_approval(intent, operator_id="ops-1")

    # Tamper amount after approval
    modified = intent.model_copy(update={"usdg_in": 12_000_000})
    decision = policy.evaluate(modified, human_approval=approval)
    assert decision.is_denied()
    assert PurchaseDenialCode.HUMAN_APPROVAL_INTENT_MISMATCH.value in decision.denial_codes


def test_human_approval_rejected_when_expired():
    policy = _policy(
        autonomous_usdg_ceiling=5_000_000,
        absolute_usdg_ceiling=25_000_000,
        human_approval_ttl_seconds=10.0,
    )
    intent = _intent(usdg_in=10_000_000, min_credit_out=8_000_000)
    now = 1_700_000_000.0
    approval = policy.issue_human_approval(intent, operator_id="ops-1", current_time=now)
    decision = policy.evaluate(intent, human_approval=approval, current_time=now + 11)
    assert decision.is_denied()
    assert PurchaseDenialCode.HUMAN_APPROVAL_EXPIRED.value in decision.denial_codes


def test_human_approval_tenant_mismatch():
    policy = _policy(autonomous_usdg_ceiling=5_000_000, absolute_usdg_ceiling=25_000_000)
    intent = _intent(usdg_in=10_000_000, min_credit_out=8_000_000)
    approval = policy.issue_human_approval(intent, operator_id="ops-1")
    other = intent.model_copy(update={"tenant_id": "tenant-other"})
    # Approval hash was for original tenant; evaluating other tenant with same approval
    # fails either hash mismatch (tenant is in hash) or explicit tenant check.
    decision = policy.evaluate(other, human_approval=approval)
    assert decision.is_denied()
    assert decision.denial_codes[0] in {
        PurchaseDenialCode.HUMAN_APPROVAL_INTENT_MISMATCH.value,
        PurchaseDenialCode.HUMAN_APPROVAL_TENANT_MISMATCH.value,
        PurchaseDenialCode.MISSING_HUMAN_APPROVAL.value,
    }


def test_still_deny_above_absolute_even_with_approval():
    policy = _policy(autonomous_usdg_ceiling=5_000_000, absolute_usdg_ceiling=15_000_000)
    intent = _intent(usdg_in=20_000_000, min_credit_out=10_000_000)
    approval = policy.issue_human_approval(intent, operator_id="ops-1")
    decision = policy.evaluate(intent, human_approval=approval)
    assert decision.is_denied()
    assert PurchaseDenialCode.MAX_USDG_EXCEEDED.value in decision.denial_codes


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_decision_deterministic():
    policy = _policy()
    intent = _intent()
    d1 = policy.evaluate(intent)
    d2 = policy.evaluate(intent)
    assert d1.result == d2.result
    assert d1.intent_hash == d2.intent_hash
    assert d1.policy_version == d2.policy_version
    assert d1.denial_codes == d2.denial_codes


def test_policy_version_changes_with_ceiling():
    p1 = _policy(autonomous_usdg_ceiling=5_000_000)
    p2 = _policy(autonomous_usdg_ceiling=6_000_000)
    assert p1.get_policy_version() != p2.get_policy_version()


def test_audit_dict_serializable():
    decision = _policy().evaluate(_intent())
    d = decision.to_audit_dict()
    assert d["result"] == "ALLOW"
    assert "intent_hash" in d
    assert isinstance(d["reasons"], list)
