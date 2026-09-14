import pytest
from src.domain.entities import ConsequentialOperation
from src.domain.enums import ActionType, OperationState
from src.domain.exceptions import InvalidStateTransitionError


def _make_op(state: OperationState = OperationState.CREATED) -> ConsequentialOperation:
    return ConsequentialOperation(
        id="op-1",
        tenant_id="tenant-demo",
        organisation_id="org-1",
        proposal_id="prop-1",
        decision_id="dec-1",
        idempotency_key="idem-key-1",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="https://api.example.com/action",
        parameters={"foo": "bar"},
        amount=10,
        provider_name="simulated",
        state=state,
    )


def test_valid_happy_path_transitions():
    op = _make_op(OperationState.CREATED)
    assert op.state == OperationState.CREATED

    op.transition_to(OperationState.AUTHORIZED)
    assert op.state == OperationState.AUTHORIZED

    op.transition_to(OperationState.ESCROWED)
    assert op.state == OperationState.ESCROWED

    op.transition_to(OperationState.SUBMITTED)
    assert op.state == OperationState.SUBMITTED

    op.transition_to(OperationState.SUCCEEDED, provider_reference="ref-123")
    assert op.state == OperationState.SUCCEEDED
    assert op.provider_reference == "ref-123"


def test_valid_ambiguity_and_reconciliation_path():
    op = _make_op(OperationState.SUBMITTED)
    op.transition_to(OperationState.UNKNOWN, error_message="Provider timeout after 5s")
    assert op.state == OperationState.UNKNOWN
    assert op.error_message == "Provider timeout after 5s"

    op.transition_to(OperationState.RECONCILING)
    assert op.state == OperationState.RECONCILING

    op.transition_to(OperationState.RECONCILED, provider_reference="ref-rec-456")
    assert op.state == OperationState.RECONCILED
    assert op.provider_reference == "ref-rec-456"


def test_reconciliation_remains_unresolved_returns_to_unknown():
    op = _make_op(OperationState.RECONCILING)
    op.transition_to(OperationState.UNKNOWN, error_message="Provider status still pending")
    assert op.state == OperationState.UNKNOWN


def test_invalid_transitions_raise():
    # Direct jump from CREATED to SUCCEEDED
    op = _make_op(OperationState.CREATED)
    with pytest.raises(InvalidStateTransitionError):
        op.transition_to(OperationState.SUCCEEDED)

    # Direct jump from CREATED to SUBMITTED
    with pytest.raises(InvalidStateTransitionError):
        op.transition_to(OperationState.SUBMITTED)

    # UNKNOWN cannot transition directly to FAILED without RECONCILING
    op_unknown = _make_op(OperationState.UNKNOWN)
    with pytest.raises(InvalidStateTransitionError):
        op_unknown.transition_to(OperationState.FAILED)

    # Terminal states cannot transition
    op_succ = _make_op(OperationState.SUCCEEDED)
    with pytest.raises(InvalidStateTransitionError):
        op_succ.transition_to(OperationState.RECONCILING)

    op_failed = _make_op(OperationState.FAILED)
    with pytest.raises(InvalidStateTransitionError):
        op_failed.transition_to(OperationState.CREATED)

    op_rec = _make_op(OperationState.RECONCILED)
    with pytest.raises(InvalidStateTransitionError):
        op_rec.transition_to(OperationState.SUBMITTED)


def test_operation_fingerprint_deterministic_and_sensitive():
    op1 = _make_op()
    op2 = _make_op()
    assert op1.get_fingerprint() == op2.get_fingerprint()

    # Changing amount changes fingerprint
    op_diff_amount = _make_op()
    op_diff_amount.amount = 20
    assert op1.get_fingerprint() != op_diff_amount.get_fingerprint()

    # Changing target changes fingerprint
    op_diff_target = _make_op()
    op_diff_target.target = "https://api.example.com/other"
    assert op1.get_fingerprint() != op_diff_target.get_fingerprint()
