import pytest
from src.domain.entities import ConsequentialOperation
from src.domain.enums import ActionType, OperationState, ProviderOutcome
from src.settlement.simulated_provider import SimulatedConsequentialProvider


def _make_op(op_id="op-1", key="idem-1", amount=15) -> ConsequentialOperation:
    return ConsequentialOperation(
        id=op_id,
        tenant_id="tenant-demo",
        organisation_id="org-1",
        proposal_id="prop-1",
        decision_id="dec-1",
        idempotency_key=key,
        action_type=ActionType.EXTERNAL_API_CALL,
        target="https://api.example.com/pay",
        parameters={"currency": "USD"},
        amount=amount,
        provider_name="simulated",
        state=OperationState.SUBMITTED,
    )


def test_simulated_provider_success():
    provider = SimulatedConsequentialProvider()
    op = _make_op()
    res = provider.execute(op)

    assert res.outcome == ProviderOutcome.SUCCESS.value
    assert res.provider_reference.startswith("ext-ref-")
    assert res.evidence_hash is not None
    assert provider.submission_attempts["idem-1"] == 1

    # Check status
    st = provider.status(op.id, op.idempotency_key)
    assert st.outcome == ProviderOutcome.SUCCESS.value
    assert st.provider_reference == res.provider_reference


def test_simulated_provider_explicit_failure():
    provider = SimulatedConsequentialProvider()
    provider.set_failure_rule("idem-fail", "Account frozen by compliance")

    op = _make_op(op_id="op-fail", key="idem-fail")
    res = provider.execute(op)

    assert res.outcome == ProviderOutcome.FAILURE.value
    assert "compliance" in res.error_message

    st = provider.status(op.id, op.idempotency_key)
    assert st.outcome == ProviderOutcome.FAILURE.value


def test_simulated_provider_timeout():
    provider = SimulatedConsequentialProvider()
    provider.set_timeout_rule("idem-timeout", provider_executes_in_background=False)

    op = _make_op(op_id="op-timeout", key="idem-timeout")
    res = provider.execute(op)

    assert res.outcome == ProviderOutcome.TIMEOUT.value
    assert res.provider_reference is None

    # Status without background execution returns UNKNOWN
    st = provider.status(op.id, op.idempotency_key)
    assert st.outcome == ProviderOutcome.UNKNOWN.value


def test_simulated_provider_timeout_with_background_success():
    provider = SimulatedConsequentialProvider()
    provider.set_timeout_rule("idem-bg", provider_executes_in_background=True)

    op = _make_op(op_id="op-bg", key="idem-bg")
    res = provider.execute(op)

    # Client saw timeout during transit
    assert res.outcome == ProviderOutcome.TIMEOUT.value

    # But provider actually processed it in the background!
    st = provider.status(op.id, op.idempotency_key)
    assert st.outcome == ProviderOutcome.SUCCESS.value
    assert st.provider_reference.startswith("ext-ref-")


def test_simulated_provider_duplicate_submission_is_idempotent():
    provider = SimulatedConsequentialProvider()
    op = _make_op(op_id="op-dup", key="idem-dup")

    res1 = provider.execute(op)
    assert res1.outcome == ProviderOutcome.SUCCESS.value

    # Resubmit identical key
    res2 = provider.execute(op)
    assert res2.outcome == ProviderOutcome.SUCCESS.value
    assert res2.provider_reference == res1.provider_reference
    assert res2.evidence_hash == res1.evidence_hash
    assert res2.raw_response["duplicate_submission"] is True
    assert provider.submission_attempts["idem-dup"] == 2
