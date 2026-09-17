"""Unit tests for SimulatedOrbioExchangeProvider — Phase 14B.4.

Offline path: policy → bridge → CREATED operation → simulated execute.
No RPC / signing / real funds.
"""
from __future__ import annotations

from src.domain.blockchain import OrbioPurchaseIntent
from src.domain.entities import Organisation
from src.domain.enums import ProviderOutcome
from src.execution.orbio_purchase import OrbioPurchaseBridge
from src.governance.orbio_purchase_rules import OrbioPurchasePolicy
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider


def _intent(**overrides) -> OrbioPurchaseIntent:
    base = dict(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        mission_id="m-1",
        operation_id="cop-sim-1",
        chain_id=46630,
        network="robinhood-testnet",
        usdg_in=3_000_000,
        min_credit_out=2_500_000,
        beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        max_fills=5,
        amount_credits=3,
        idempotency_key="org-alpha:cop-sim-1",
        policy_decision_id="dec-sim-1",
        authorization_token_hash="tok-sim",
    )
    base.update(overrides)
    return OrbioPurchaseIntent(**base)


def _org() -> Organisation:
    return Organisation(id="org-alpha", mission="test", tenant_id="tenant-alpha", treasury_balance=50)


def _prepared_operation(usdg_in: int = 3_000_000, min_credit_out: int = 2_500_000):
    bridge = OrbioPurchaseBridge()
    intent = _intent(usdg_in=usdg_in, min_credit_out=min_credit_out)
    prep, op = bridge.prepare_and_create_operation(intent, _org())
    assert prep.is_authorized
    return prep, op


def test_successful_purchase_debits_usdg_and_credits_credit():
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-alpha", 10_000_000)
    _, op = _prepared_operation()

    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.SUCCESS.value
    assert result.provider_reference is not None
    assert result.raw_response["simulated"] is True
    assert result.raw_response["events"][0]["name"] == "Activated"
    assert provider.get_usdg_balance("org-alpha") == 10_000_000 - 3_000_000
    # 98% fill of 3_000_000 = 2_940_000
    assert provider.get_credit_balance("org-alpha") == 2_940_000
    assert result.raw_response["credit_out"] == 2_940_000


def test_insufficient_usdg_fails_without_debit():
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-alpha", 1_000_000)  # less than 3M
    _, op = _prepared_operation()

    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.FAILURE.value
    assert "Insufficient USDG" in (result.error_message or "")
    assert provider.get_usdg_balance("org-alpha") == 1_000_000
    assert provider.get_credit_balance("org-alpha") == 0


def test_slippage_failure_when_fill_below_min():
    # Force poor fill so credit_out < min_credit_out
    provider = SimulatedOrbioExchangeProvider(fill_rate_bps=5000)  # 50%
    provider.set_usdg_balance("org-alpha", 10_000_000)
    _, op = _prepared_operation(usdg_in=3_000_000, min_credit_out=2_500_000)

    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.FAILURE.value
    assert "Slippage" in (result.error_message or "")
    assert provider.get_usdg_balance("org-alpha") == 10_000_000


def test_idempotent_second_submit_does_not_double_spend():
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-alpha", 10_000_000)
    _, op = _prepared_operation()

    r1 = provider.execute(op)
    r2 = provider.execute(op)
    assert r1.outcome == ProviderOutcome.SUCCESS.value
    assert r2.outcome == ProviderOutcome.SUCCESS.value
    assert r2.raw_response.get("duplicate_submission") is True
    assert provider.get_usdg_balance("org-alpha") == 7_000_000  # only one debit
    assert provider.get_credit_balance("org-alpha") == 2_940_000


def test_configured_failure_rule():
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-alpha", 10_000_000)
    _, op = _prepared_operation()
    provider.set_failure_rule(op.idempotency_key, "book closed")

    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.FAILURE.value
    assert result.error_message == "book closed"


def test_timeout_without_background_settlement():
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-alpha", 10_000_000)
    _, op = _prepared_operation()
    provider.set_timeout_rule(op.idempotency_key)

    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.TIMEOUT.value
    assert provider.get_usdg_balance("org-alpha") == 10_000_000
    status = provider.status(op.id, idempotency_key=op.idempotency_key)
    assert status.outcome == ProviderOutcome.UNKNOWN.value


def test_timeout_with_background_success_reconciles_via_status():
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-alpha", 10_000_000)
    _, op = _prepared_operation()
    provider.set_timeout_rule(op.idempotency_key, provider_executes_in_background=True)

    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.TIMEOUT.value
    # Background settled
    assert provider.get_usdg_balance("org-alpha") == 7_000_000
    status = provider.status(op.id, idempotency_key=op.idempotency_key)
    assert status.outcome == ProviderOutcome.SUCCESS.value
    assert status.provider_reference is not None


def test_status_unknown_for_unseen_operation():
    provider = SimulatedOrbioExchangeProvider()
    status = provider.status("cop-never", idempotency_key="never-seen")
    assert status.outcome == ProviderOutcome.UNKNOWN.value


def test_human_gated_purchase_then_simulated_execute():
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=5_000_000,
        absolute_usdg_ceiling=25_000_000,
    )
    bridge = OrbioPurchaseBridge(purchase_policy=policy)
    intent = _intent(usdg_in=10_000_000, min_credit_out=8_000_000, amount_credits=10)
    # First attempt needs human
    prep = bridge.prepare(intent)
    assert not prep.is_authorized

    approval = policy.issue_human_approval(intent, operator_id="ops-1")
    prep, op = bridge.prepare_and_create_operation(intent, _org(), human_approval=approval)

    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-alpha", 20_000_000)
    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.SUCCESS.value
    assert result.raw_response["usdg_spent"] == 10_000_000
    assert result.raw_response["credit_out"] == 9_800_000  # 98% of 10M


def test_prepare_rejects_negative_amount():
    provider = SimulatedOrbioExchangeProvider()
    _, op = _prepared_operation()
    op.amount = -1
    assert provider.prepare(op) is False
