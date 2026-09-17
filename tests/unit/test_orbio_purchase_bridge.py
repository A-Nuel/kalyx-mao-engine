"""Unit tests for OrbioPurchaseBridge — Phase 14B.3.

Verifies policy gating, projection onto BlockchainTransactionIntent,
ConsequentialOperation creation, and mutation detection. No RPC.
"""
from __future__ import annotations

import pytest

from src.domain.blockchain import OrbioPurchaseIntent
from src.domain.entities import Organisation
from src.domain.enums import ActionType, OperationState
from src.domain.exceptions import PolicyViolationError, UnauthorizedActionError
from src.execution.orbio_purchase import OrbioPurchaseBridge
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
        operation_id="cop-bridge-1",
        chain_id=46630,
        network="robinhood-testnet",
        usdg_in=3_000_000,
        min_credit_out=2_500_000,
        beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        max_fills=5,
        amount_credits=3,
        idempotency_key="org-alpha:cop-bridge-1",
        policy_decision_id="dec-bridge-1",
        authorization_token_hash="tok-bridge",
    )
    base.update(overrides)
    return OrbioPurchaseIntent(**base)


def _org() -> Organisation:
    return Organisation(
        id="org-alpha",
        mission="test",
        tenant_id="tenant-alpha",
        treasury_balance=100,
    )


def test_prepare_allow_projects_blockchain_intent():
    bridge = OrbioPurchaseBridge()
    prep = bridge.prepare(_intent())
    assert prep.is_authorized
    assert prep.blockchain_intent is not None
    assert prep.blockchain_intent.recipient == prep.purchase_intent.exchange_contract
    assert prep.blockchain_intent.amount_wei == 0
    assert prep.blockchain_intent.data_payload == prep.purchase_intent.encode_calldata()
    assert prep.operation_parameters["data_payload"] == prep.blockchain_intent.data_payload
    assert prep.operation_parameters["purchase_intent_hash"] == prep.purchase_intent_hash
    assert prep.operation_parameters["usdg_in"] == 3_000_000


def test_prepare_deny_has_no_blockchain_intent():
    bridge = OrbioPurchaseBridge()
    prep = bridge.prepare(_intent(exchange_contract="0x0000000000000000000000000000000000000001"))
    assert not prep.is_authorized
    assert prep.blockchain_intent is None
    assert prep.operation_parameters == {}
    assert PurchaseDenialCode.UNAUTHORIZED_EXCHANGE.value in prep.policy_decision.denial_codes


def test_prepare_human_required_not_authorized():
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=5_000_000,
        absolute_usdg_ceiling=25_000_000,
    )
    bridge = OrbioPurchaseBridge(purchase_policy=policy)
    prep = bridge.prepare(_intent(usdg_in=10_000_000, min_credit_out=8_000_000))
    assert prep.policy_decision.result == PurchaseDecisionResult.HUMAN_CONFIRMATION_REQUIRED
    assert not prep.is_authorized


def test_prepare_with_human_approval_allows():
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=5_000_000,
        absolute_usdg_ceiling=25_000_000,
    )
    bridge = OrbioPurchaseBridge(purchase_policy=policy)
    intent = _intent(usdg_in=10_000_000, min_credit_out=8_000_000)
    approval = policy.issue_human_approval(intent, operator_id="ops-1")
    prep = bridge.prepare(intent, human_approval=approval)
    assert prep.is_authorized
    assert prep.blockchain_intent is not None


def test_create_operation_from_preparation():
    bridge = OrbioPurchaseBridge()
    prep = bridge.prepare(_intent())
    op = bridge.create_operation_from_preparation(prep, _org())
    assert op.state == OperationState.CREATED
    assert op.action_type == ActionType.ORBIO_CREDIT_PURCHASE
    assert op.organisation_id == "org-alpha"
    assert op.tenant_id == "tenant-alpha"
    assert op.amount == 3
    assert op.parameters["data_payload"].startswith("0x6ebadb6e")
    assert op.parameters["recipient"] == prep.purchase_intent.exchange_contract
    assert op.provider_name == "blockchain"


def test_create_operation_rejected_when_not_allowed():
    bridge = OrbioPurchaseBridge()
    prep = bridge.prepare(_intent(chain_id=1, network="ethereum"))
    with pytest.raises(PolicyViolationError):
        bridge.create_operation_from_preparation(prep, _org())


def test_prepare_and_create_operation_happy_path():
    bridge = OrbioPurchaseBridge()
    prep, op = bridge.prepare_and_create_operation(_intent(), _org())
    assert prep.is_authorized
    assert op.state == OperationState.CREATED
    ok, reason = bridge.verify_operation_matches_intent(op, prep.purchase_intent)
    assert ok, reason


def test_prepare_and_create_rejects_org_mismatch():
    bridge = OrbioPurchaseBridge()
    with pytest.raises(UnauthorizedActionError):
        bridge.prepare_and_create_operation(
            _intent(organisation_id="org-other"),
            _org(),
        )


def test_verify_detects_calldata_mutation():
    bridge = OrbioPurchaseBridge()
    prep, op = bridge.prepare_and_create_operation(_intent(), _org())
    # Mutate durable parameters after authorization
    op.parameters = {**op.parameters, "data_payload": "0xdeadbeef"}
    ok, reason = bridge.verify_operation_matches_intent(op, prep.purchase_intent)
    assert not ok
    assert reason is not None
    assert "data_payload" in reason


def test_verify_detects_intent_hash_mutation():
    bridge = OrbioPurchaseBridge()
    prep, op = bridge.prepare_and_create_operation(_intent(), _org())
    op.parameters = {**op.parameters, "purchase_intent_hash": "0" * 64}
    ok, reason = bridge.verify_operation_matches_intent(op, prep.purchase_intent)
    assert not ok


def test_build_action_proposal_shape():
    bridge = OrbioPurchaseBridge()
    intent = _intent()
    proposal = bridge.build_action_proposal(
        intent,
        proposing_agent_id="agent-1",
        task_id="task-1",
    )
    assert proposal.action_type == ActionType.ORBIO_CREDIT_PURCHASE
    assert proposal.requested_credits == intent.amount_credits
    assert proposal.parameters["purchase_intent_hash"] == intent.compute_purchase_intent_hash()
    assert proposal.target.startswith("orbio://exchange/")
