"""Phase 14B.8 — Orbio purchase UNKNOWN reconciliation tests."""
from __future__ import annotations

from src.domain.blockchain import OrbioPurchaseIntent
from src.domain.entities import Organisation
from src.domain.enums import OperationState, ProviderOutcome
from src.economy.ledger import DoubleEntryLedger, ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.orbio_purchase import OrbioPurchaseBridge
from src.settlement.orbio_purchase_reconciliation import OrbioPurchaseReconciliation
from src.settlement.orbio_purchase_verifier import PurchaseVerificationResult
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider


def _intent(**overrides) -> OrbioPurchaseIntent:
    base = dict(
        tenant_id="tenant-a",
        organisation_id="org-a",
        mission_id="m-1",
        operation_id="cop-rec-1",
        chain_id=46630,
        network="robinhood-testnet",
        usdg_in=3_000_000,
        min_credit_out=2_500_000,
        beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        max_fills=5,
        amount_credits=5,
        idempotency_key="org-a:cop-rec-1",
        policy_decision_id="dec-rec-1",
        authorization_token_hash="tok-rec",
    )
    base.update(overrides)
    return OrbioPurchaseIntent(**base)


def _org() -> Organisation:
    return Organisation(id="org-a", mission="r", tenant_id="tenant-a", treasury_balance=100)


def _escrowed_unknown_op(provider: SimulatedOrbioExchangeProvider, background_success: bool = False):
    """Create CREATED op, mark UNKNOWN after timed-out submit, with credits in ESCROW."""
    intent = _intent()
    bridge = OrbioPurchaseBridge()
    _, op = bridge.prepare_and_create_operation(intent, _org())
    provider.set_usdg_balance("org-a", 20_000_000)
    if background_success:
        provider.set_timeout_rule(op.idempotency_key, provider_executes_in_background=True)
    else:
        provider.set_timeout_rule(op.idempotency_key)

    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.TIMEOUT.value

    # Simulate Phase 10: SUBMITTED → UNKNOWN, credits already escrowed
    op.transition_to(OperationState.SUBMITTED)
    op.transition_to(OperationState.UNKNOWN, error_message="timeout")
    return intent, op


def test_reconcile_pending_leaves_unknown_and_escrow():
    ledger = DoubleEntryLedger(initial_treasury=100)
    ledger.transfer(TREASURY, ESCROW, 5, memo="escrow for cop-rec-1", transaction_id="esc-1")
    provider = SimulatedOrbioExchangeProvider()
    intent, op = _escrowed_unknown_op(provider, background_success=False)

    svc = OrbioPurchaseReconciliation(provider=provider, ledger=ledger)
    outcome = svc.reconcile(op, intent, _org())

    assert outcome.economic_action == "none"
    assert outcome.operation.state == OperationState.UNKNOWN
    assert outcome.verification is not None
    assert outcome.verification.result == PurchaseVerificationResult.PENDING_EVIDENCE
    assert ledger.get_balance(ESCROW) == 5
    assert ledger.get_balance(EXTERNAL_SINK) == 0


def test_reconcile_background_success_settles_after_verify():
    ledger = DoubleEntryLedger(initial_treasury=100)
    ledger.transfer(TREASURY, ESCROW, 5, memo="escrow for cop-rec-1", transaction_id="esc-2")
    provider = SimulatedOrbioExchangeProvider()
    intent, op = _escrowed_unknown_op(provider, background_success=True)

    svc = OrbioPurchaseReconciliation(provider=provider, ledger=ledger)
    outcome = svc.reconcile(op, intent, _org())

    assert outcome.economic_action == "settled"
    assert outcome.operation.state == OperationState.RECONCILED
    assert outcome.verification is not None
    assert outcome.verification.is_verified()
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 5


def test_reconcile_idempotent_second_call_no_double_settle():
    ledger = DoubleEntryLedger(initial_treasury=100)
    ledger.transfer(TREASURY, ESCROW, 5, memo="escrow for cop-rec-1", transaction_id="esc-3")
    provider = SimulatedOrbioExchangeProvider()
    intent, op = _escrowed_unknown_op(provider, background_success=True)

    svc = OrbioPurchaseReconciliation(provider=provider, ledger=ledger)
    o1 = svc.reconcile(op, intent, _org())
    o2 = svc.reconcile(op, intent, _org())

    assert o1.economic_action == "settled"
    assert o2.economic_action == "none"  # already RECONCILED
    assert ledger.get_balance(EXTERNAL_SINK) == 5


def test_reconcile_does_not_trust_status_success_with_wrong_intent():
    """Provider SUCCESS for a different economic reality must not settle."""
    ledger = DoubleEntryLedger(initial_treasury=100)
    ledger.transfer(TREASURY, ESCROW, 5, memo="escrow for cop-rec-1", transaction_id="esc-4")
    provider = SimulatedOrbioExchangeProvider()
    intent, op = _escrowed_unknown_op(provider, background_success=True)

    # Different intent hash (higher spend) than what was executed
    other = intent.model_copy(update={"usdg_in": 9_000_000, "min_credit_out": 8_000_000})
    # Operation still has original purchase_intent_hash → hard error before status
    svc = OrbioPurchaseReconciliation(provider=provider, ledger=ledger)
    try:
        svc.reconcile(op, other, _org())
        raised = False
    except Exception:
        raised = True
    assert raised
    assert ledger.get_balance(ESCROW) == 5


def test_reconcile_rejected_evidence_refunds_escrow():
    """Force verification reject by clearing provider records after timeout with no settle."""
    ledger = DoubleEntryLedger(initial_treasury=100)
    ledger.transfer(TREASURY, ESCROW, 5, memo="escrow for cop-rec-1", transaction_id="esc-5")
    provider = SimulatedOrbioExchangeProvider()
    intent, op = _escrowed_unknown_op(provider, background_success=False)

    # Inject a FAILURE status record with mismatched economics via failure rule path:
    # execute a separate success then overwrite is hard; instead set failure on a fresh key.
    # Here: seed failure record with wrong amounts by calling _settle_failure style via set_failure
    # and re-using same key after clearing timeout — simplest path: force failure rule before status
    provider._timeout_keys.clear()
    provider.set_failure_rule(op.idempotency_key, "forced reject path")
    # Execute again to materialize FAILURE record (idempotency may hit empty then fail)
    # records empty for key if only timeout without background — execute again:
    provider._records.pop(op.idempotency_key, None)
    provider.execute(op)  # should FAILURE now

    svc = OrbioPurchaseReconciliation(provider=provider, ledger=ledger)
    outcome = svc.reconcile(op, intent, _org())

    # FAILURE outcome without valid purchase evidence → REJECTED → refund
    assert outcome.economic_action == "refunded"
    assert outcome.operation.state == OperationState.RECONCILED
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(TREASURY) == 100
