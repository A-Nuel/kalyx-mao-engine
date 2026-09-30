"""Tests for Phase 21 OrbioActivationReconciliation."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from src.domain.entities import ConsequentialOperation, Organisation
from src.domain.entities import ConsequentialOperation, Organisation
from src.domain.enums import ActionType, OperationState, OrgState
from src.domain.exceptions import ReconciliationError, UnauthorizedActionError
from src.domain.orbio_activation import (
    MAX_ACTIVATION_AMOUNT,
    ORBIO_ACTIVATION_CHAIN_ID,
    ORBIO_CREDIT_ACTIVATION_CONTRACT,
    OrbioCreditActivationIntent,
)
from src.economy.ledger import DoubleEntryLedger, ESCROW, EXTERNAL_SINK, TREASURY
from src.settlement.orbio_activation_reconciliation import (
    ActivationReconciliationOutcome,
    OrbioActivationReconciliation,
)
from src.settlement.orbio_activation_verifier import (
    ActivationVerificationReport,
    ActivationVerificationResult,
)
from src.settlement.orbio_api_verifier import (
    OrbioApiStatus,
    OrbioApiVerificationReport,
)


@pytest.fixture
def test_org() -> Organisation:
    return Organisation(
        id=str(uuid.uuid4()),
        name="Test Org",
        mission="Autonomous test org",
        tenant_id="tenant-123",
        state=OrgState.EXECUTING,
        treasury_balance=Decimal("1000.00"),
    )


@pytest.fixture
def test_intent(test_org: Organisation) -> OrbioCreditActivationIntent:
    op_id = "op-orbio-1"
    return OrbioCreditActivationIntent(
        tenant_id=test_org.tenant_id,
        organisation_id=test_org.id,
        operation_id=op_id,
        chain_id=ORBIO_ACTIVATION_CHAIN_ID,
        credit_contract=ORBIO_CREDIT_ACTIVATION_CONTRACT,
        amount=MAX_ACTIVATION_AMOUNT,
        amount_credits=0,
        idempotency_key=f"orbio-activate-1credit-{op_id}",
        policy_decision_id="dec-1",
        authorization_token_hash="hash-1",
    )


@pytest.fixture
def test_operation(test_org: Organisation) -> ConsequentialOperation:
    return ConsequentialOperation(
        id="op-orbio-1",
        tenant_id=test_org.tenant_id,
        organisation_id=test_org.id,
        proposal_id="prop-1",
        decision_id="dec-1",
        idempotency_key="idem-key-orbio",
        action_type=ActionType.ORBIO_CREDIT_ACTIVATION,
        target=ORBIO_CREDIT_ACTIVATION_CONTRACT,
        parameters={"activation_amount": MAX_ACTIVATION_AMOUNT},
        amount=1.00,
        provider_name="blockchain_robinhood",
        state=OperationState.ESCROWED,
    )


@pytest.fixture
def ledger_with_escrow() -> DoubleEntryLedger:
    ledger = DoubleEntryLedger(initial_treasury=1000)
    ledger.transfer(from_account=TREASURY, to_account=ESCROW, amount=100, memo="Fund escrow")
    return ledger


def test_reconciliation_verified_on_chain_and_api(
    test_org: Organisation,
    test_intent: OrbioCreditActivationIntent,
    test_operation: ConsequentialOperation,
    ledger_with_escrow: DoubleEntryLedger,
):
    expected_operator = "0x4675b9d0323479b1af399c87331d1d2436e6be99"
    mock_on_chain = MagicMock()
    mock_on_chain.verify.return_value = ActivationVerificationReport(
        result=ActivationVerificationResult.VERIFIED,
        tx_hash="0x80b75114f43bd9b957cd9194eebe15f2ae9d0516be5672376b612cd3ba1c8fa1",
        activation_id=567,
        sender=expected_operator,
        beneficiary=expected_operator,
        burned_amount=MAX_ACTIVATION_AMOUNT,
        fee_atoms=50_000,
    )

    mock_api = MagicMock()
    mock_api.verify.return_value = OrbioApiVerificationReport(
        status=OrbioApiStatus.VERIFIED,
        available_balance=0.95,
        account_id=expected_operator,
        raw_response={"credits": "0.95"},
    )

    rec = OrbioActivationReconciliation(
        ledger=ledger_with_escrow,
        on_chain_verifier=mock_on_chain,
        api_verifier=mock_api,
    )

    outcome = rec.reconcile(
        operation=test_operation,
        intent=test_intent,
        org=test_org,
        receipt={"dummy": "receipt"},
        expected_sender=expected_operator,
    )

    assert outcome.status == "VERIFIED"
    assert outcome.economic_action == "settled"
    assert test_operation.state == OperationState.SUCCEEDED
    assert ledger_with_escrow.get_balance(ESCROW) == 99
    assert ledger_with_escrow.get_balance(EXTERNAL_SINK) == 1


def test_reconciliation_verified_on_chain_pending_api(
    test_org: Organisation,
    test_intent: OrbioCreditActivationIntent,
    test_operation: ConsequentialOperation,
    ledger_with_escrow: DoubleEntryLedger,
):
    expected_operator = "0x4675b9d0323479b1af399c87331d1d2436e6be99"
    mock_on_chain = MagicMock()
    mock_on_chain.verify.return_value = ActivationVerificationReport(
        result=ActivationVerificationResult.VERIFIED,
        tx_hash="0x80b75114f43bd9b957cd9194eebe15f2ae9d0516be5672376b612cd3ba1c8fa1",
        activation_id=567,
        sender=expected_operator,
        beneficiary=expected_operator,
        burned_amount=MAX_ACTIVATION_AMOUNT,
        fee_atoms=50_000,
    )

    mock_api = MagicMock()
    mock_api.verify.return_value = OrbioApiVerificationReport(
        status=OrbioApiStatus.PENDING,
        available_balance=None,
        account_id=None,
        raw_response=None,
        error_message="ORBIO_API_KEY is not configured",
    )

    rec = OrbioActivationReconciliation(
        ledger=ledger_with_escrow,
        on_chain_verifier=mock_on_chain,
        api_verifier=mock_api,
    )

    outcome = rec.reconcile(
        operation=test_operation,
        intent=test_intent,
        org=test_org,
        receipt={"dummy": "receipt"},
        expected_sender=expected_operator,
    )

    assert outcome.status == "VERIFIED"
    assert outcome.economic_action == "settled"
    assert test_operation.state == OperationState.RECONCILED
    assert not outcome.api_verification.is_confirmed


def test_reconciliation_rejected_on_chain_refunds_escrow(
    test_org: Organisation,
    test_intent: OrbioCreditActivationIntent,
    test_operation: ConsequentialOperation,
    ledger_with_escrow: DoubleEntryLedger,
):
    mock_on_chain = MagicMock()
    mock_on_chain.verify.return_value = ActivationVerificationReport(
        result=ActivationVerificationResult.REJECTED,
        reasons=["Transaction reverted on chain"],
    )

    rec = OrbioActivationReconciliation(
        ledger=ledger_with_escrow,
        on_chain_verifier=mock_on_chain,
    )

    outcome = rec.reconcile(
        operation=test_operation,
        intent=test_intent,
        org=test_org,
        receipt={"dummy": "reverted_receipt"},
    )

    assert outcome.status == "REJECTED"
    assert outcome.economic_action == "refunded"
    assert test_operation.state == OperationState.FAILED
    assert ledger_with_escrow.get_balance(ESCROW) == 99
    assert ledger_with_escrow.get_balance(TREASURY) == 901


def test_reconciliation_isolation_enforcement(
    test_org: Organisation,
    test_intent: OrbioCreditActivationIntent,
    test_operation: ConsequentialOperation,
    ledger_with_escrow: DoubleEntryLedger,
):
    rec = OrbioActivationReconciliation(ledger=ledger_with_escrow)

    # Different tenant
    foreign_org = Organisation(
        id=test_org.id,
        name="Foreign Org",
        mission="Foreign mission",
        tenant_id="attacker-tenant",
        state=OrgState.EXECUTING,
    )
    with pytest.raises(UnauthorizedActionError, match="Isolation violation"):
        rec.reconcile(
            operation=test_operation,
            intent=test_intent,
            org=foreign_org,
        )

    # Wrong action type
    wrong_op = ConsequentialOperation(
        id="op-wrong-1",
        tenant_id=test_org.tenant_id,
        organisation_id=test_org.id,
        proposal_id="prop-1",
        decision_id="dec-1",
        idempotency_key="idem-wrong-1",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="some_target",
        state=OperationState.AUTHORIZED,
        amount=1.00,
        provider_name="payment_provider",
    )
    with pytest.raises(ReconciliationError, match="Invalid action_type"):
        rec.reconcile(
            operation=wrong_op,
            intent=test_intent,
            org=test_org,
        )


def test_reconciliation_idempotency(
    test_org: Organisation,
    test_intent: OrbioCreditActivationIntent,
    test_operation: ConsequentialOperation,
    ledger_with_escrow: DoubleEntryLedger,
):
    test_operation.state = OperationState.SUCCEEDED
    rec = OrbioActivationReconciliation(ledger=ledger_with_escrow)

    outcome = rec.reconcile(
        operation=test_operation,
        intent=test_intent,
        org=test_org,
    )
    assert outcome.status == "VERIFIED"
    assert outcome.economic_action == "none"
    assert ledger_with_escrow.get_balance(ESCROW) == Decimal("100.00")
