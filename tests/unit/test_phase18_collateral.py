import pytest

from src.domain.collateral import (
    CollateralStatus,
    CollateralTransitionError,
    CreditCollateralPosition,
)


def _make_position(**overrides):
    defaults = dict(
        tenant_id="tenant-a",
        organisation_id="org-a",
        position_id="pos-1",
        obligation_reference="order-1",
        pledging_org_id="org-b",
        beneficiary_org_id="org-a",
        amount=20_000_000,  # 20 CREDIT at 6 decimals
    )
    defaults.update(overrides)
    return CreditCollateralPosition.create(**defaults)


def test_create_rejects_non_positive_amount():
    with pytest.raises(ValueError):
        _make_position(amount=0)
    with pytest.raises(ValueError):
        _make_position(amount=-5)


def test_create_rejects_self_pledge():
    with pytest.raises(ValueError):
        _make_position(pledging_org_id="org-a", beneficiary_org_id="org-a")


def test_happy_path_success_release():
    pos = _make_position()
    assert pos.status == CollateralStatus.PROPOSED

    pos.authorize(evidence_hash="auth-hash-1")
    assert pos.status == CollateralStatus.AUTHORIZED

    pos.lock(onchain_tx_hash="0xsimulated-lock-1")
    assert pos.status == CollateralStatus.LOCKED
    assert pos.onchain_tx_hash == "0xsimulated-lock-1"
    assert pos.locked_at is not None

    pos.activate_obligation()
    assert pos.status == CollateralStatus.OBLIGATION_ACTIVE

    pos.record_verification(success=True, evidence_hash="audit-hash-1")
    assert pos.status == CollateralStatus.VERIFIED_SUCCESS

    final = pos.settle(settlement_tx_hash="0xsimulated-release-1")
    assert final == CollateralStatus.RELEASED
    assert pos.settlement_tx_hash == "0xsimulated-release-1"
    assert pos.settled_at is not None


def test_failure_path_forfeit():
    pos = _make_position()
    pos.authorize(evidence_hash="auth-hash-2")
    pos.lock(onchain_tx_hash="0xsimulated-lock-2")
    pos.activate_obligation()

    pos.record_verification(success=False, evidence_hash="audit-hash-2")
    assert pos.status == CollateralStatus.VERIFIED_FAILURE

    final = pos.settle(settlement_tx_hash="0xsimulated-forfeit-2")
    assert final == CollateralStatus.FORFEITED


def test_cannot_skip_states():
    pos = _make_position()
    # Cannot lock before authorization.
    with pytest.raises(CollateralTransitionError):
        pos.lock(onchain_tx_hash="0xshould-fail")
    # Cannot settle from PROPOSED.
    with pytest.raises(CollateralTransitionError):
        pos.settle(settlement_tx_hash="0xshould-fail")


def test_cannot_settle_before_verification():
    pos = _make_position()
    pos.authorize(evidence_hash="auth-hash-3")
    pos.lock(onchain_tx_hash="0xlock-3")
    pos.activate_obligation()
    with pytest.raises(CollateralTransitionError):
        pos.settle(settlement_tx_hash="0xshould-fail")


def test_replay_release_after_settlement_is_rejected():
    """Invariant 6: replay of release/forfeit against an already-settled
    position must be rejected, not silently accepted or double-paid."""
    pos = _make_position()
    pos.authorize(evidence_hash="auth-hash-4")
    pos.lock(onchain_tx_hash="0xlock-4")
    pos.activate_obligation()
    pos.record_verification(success=True, evidence_hash="audit-hash-4")
    pos.settle(settlement_tx_hash="0xrelease-4")
    assert pos.status == CollateralStatus.RELEASED

    # Any further transition attempt — including a second settle() call,
    # or an attacker trying to force FORFEITED after RELEASED — must fail.
    with pytest.raises(CollateralTransitionError):
        pos.settle(settlement_tx_hash="0xreplay-attempt")
    with pytest.raises(CollateralTransitionError):
        pos.record_verification(success=False, evidence_hash="audit-hash-replay")
    with pytest.raises(CollateralTransitionError):
        pos.transition(CollateralStatus.FORFEITED, actor="attacker")

    # State must be unchanged by the rejected attempts.
    assert pos.status == CollateralStatus.RELEASED
    assert pos.settlement_tx_hash == "0xrelease-4"


def test_replay_forfeit_after_forfeit_is_rejected():
    pos = _make_position()
    pos.authorize(evidence_hash="auth-hash-5")
    pos.lock(onchain_tx_hash="0xlock-5")
    pos.activate_obligation()
    pos.record_verification(success=False, evidence_hash="audit-hash-5")
    pos.settle(settlement_tx_hash="0xforfeit-5")
    assert pos.status == CollateralStatus.FORFEITED

    with pytest.raises(CollateralTransitionError):
        pos.settle(settlement_tx_hash="0xreplay-5")


def test_unknown_verification_does_not_default_to_forfeiture():
    """Invariant 7: there is no transition path that lets an UNKNOWN/timeout
    outcome silently resolve to FORFEITED. Callers must explicitly decide
    success=True/False from real evidence; there is no third implicit branch."""
    pos = _make_position()
    pos.authorize(evidence_hash="auth-hash-6")
    pos.lock(onchain_tx_hash="0xlock-6")
    pos.activate_obligation()
    # No call to record_verification at all — position must simply remain
    # OBLIGATION_ACTIVE, never drift to a terminal state on its own.
    assert pos.status == CollateralStatus.OBLIGATION_ACTIVE
    with pytest.raises(CollateralTransitionError):
        pos.settle(settlement_tx_hash="0xshould-fail")
    assert pos.status == CollateralStatus.OBLIGATION_ACTIVE


def test_to_dict_round_trips_identity_fields():
    pos = _make_position()
    d = pos.to_dict()
    assert d["tenant_id"] == "tenant-a"
    assert d["organisation_id"] == "org-a"
    assert d["position_id"] == "pos-1"
    assert d["pledging_org_id"] == "org-b"
    assert d["beneficiary_org_id"] == "org-a"
    assert d["amount"] == 20_000_000
    assert d["asset"] == "CREDIT"
    assert d["status"] == "PROPOSED"
