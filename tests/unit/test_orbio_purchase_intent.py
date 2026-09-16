"""Unit tests for OrbioPurchaseIntent — Phase 14B.1 domain integrity.

Focus: typed construction, deterministic calldata, hash tamper detection,
projection onto BlockchainTransactionIntent, rejection of invalid inputs.
No RPC, no signing, no policy engine.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domain.blockchain import (
    BUY_AND_ACTIVATE_SELECTOR,
    ORBIO_EXCHANGE_MAINNET,
    USDG_MAINNET,
    BlockchainTransactionIntent,
    OrbioPurchaseIntent,
    address_to_beneficiary_bytes32,
    encode_buy_and_activate_calldata,
)


def _valid_purchase(**overrides):
    base = dict(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        mission_id="m-1",
        operation_id="cop-purchase-1",
        chain_id=46630,  # testnet id for safety in tests
        network="robinhood-testnet",
        usdg_in=5_000_000,  # $5 at 6 decimals
        min_credit_out=4_000_000,
        beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        max_fills=5,
        amount_credits=5,
        idempotency_key="org-alpha:cop-purchase-1",
        policy_decision_id="dec-purchase-1",
        authorization_token_hash="token-hash-abc",
    )
    base.update(overrides)
    return OrbioPurchaseIntent(**base)


def test_orbio_purchase_intent_valid_creation():
    intent = _valid_purchase()
    assert intent.exchange_contract == ORBIO_EXCHANGE_MAINNET
    assert intent.payment_token == USDG_MAINNET
    assert intent.usdg_in == 5_000_000
    assert intent.beneficiary == "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"
    h = intent.compute_purchase_intent_hash()
    assert len(h) == 64


def test_orbio_purchase_intent_rejects_invalid_exchange():
    with pytest.raises(ValidationError):
        _valid_purchase(exchange_contract="not-an-address")


def test_orbio_purchase_intent_rejects_invalid_beneficiary():
    with pytest.raises(ValidationError):
        _valid_purchase(beneficiary="0xdead")


def test_orbio_purchase_intent_rejects_negative_amounts():
    with pytest.raises(ValidationError):
        _valid_purchase(usdg_in=-1)


def test_orbio_purchase_intent_rejects_zero_zero_economy():
    with pytest.raises(ValidationError):
        _valid_purchase(usdg_in=0, min_credit_out=0)


def test_beneficiary_address_to_bytes32():
    addr = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    b32 = address_to_beneficiary_bytes32(addr)
    assert b32.startswith("0x")
    assert len(b32) == 66
    assert b32.endswith(addr.lower()[2:])


def test_encode_calldata_starts_with_selector():
    calldata = encode_buy_and_activate_calldata(
        usdg_in=1_000_000,
        min_credit_out=900_000,
        beneficiary="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        max_fills=3,
    )
    assert calldata.startswith("0x")
    selector_hex = BUY_AND_ACTIVATE_SELECTOR.hex()
    assert calldata[2:10] == selector_hex
    # 4-byte selector + 4 * 32-byte words
    assert len(bytes.fromhex(calldata[2:])) == 4 + 4 * 32


def test_encode_calldata_deterministic():
    a = encode_buy_and_activate_calldata(1_000_000, 900_000, "0x70997970C51812dc3A010C7d01b50e0d17dc79C8", 3)
    b = encode_buy_and_activate_calldata(1_000_000, 900_000, "0x70997970c51812dc3a010c7d01b50e0d17dc79c8", 3)
    assert a == b


def test_encode_calldata_changes_with_params():
    base = encode_buy_and_activate_calldata(1_000_000, 900_000, "0x70997970C51812dc3A010C7d01b50e0d17dc79C8", 3)
    changed_amount = encode_buy_and_activate_calldata(2_000_000, 900_000, "0x70997970C51812dc3A010C7d01b50e0d17dc79C8", 3)
    changed_beneficiary = encode_buy_and_activate_calldata(
        1_000_000, 900_000, "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc", 3
    )
    assert base != changed_amount
    assert base != changed_beneficiary


def test_purchase_intent_hash_tamper_detection():
    intent = _valid_purchase()
    h0 = intent.compute_purchase_intent_hash()

    assert intent.model_copy(update={"usdg_in": intent.usdg_in + 1}).compute_purchase_intent_hash() != h0
    assert intent.model_copy(update={"min_credit_out": intent.min_credit_out + 1}).compute_purchase_intent_hash() != h0
    assert (
        intent.model_copy(
            update={"beneficiary": "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"}
        ).compute_purchase_intent_hash()
        != h0
    )
    assert intent.model_copy(update={"chain_id": 1}).compute_purchase_intent_hash() != h0
    assert (
        intent.model_copy(update={"exchange_contract": "0x0000000000000000000000000000000000000001"}).compute_purchase_intent_hash()
        != h0
    )
    assert intent.model_copy(update={"max_fills": intent.max_fills + 1}).compute_purchase_intent_hash() != h0
    assert intent.model_copy(update={"policy_decision_id": "other-dec"}).compute_purchase_intent_hash() != h0


def test_to_blockchain_intent_projection():
    purchase = _valid_purchase()
    bc = purchase.to_blockchain_intent()

    assert isinstance(bc, BlockchainTransactionIntent)
    assert bc.recipient == purchase.exchange_contract
    assert bc.amount_wei == 0
    assert bc.asset == "USDG"
    assert bc.token_contract == purchase.payment_token
    assert bc.chain_id == purchase.chain_id
    assert bc.network == purchase.network
    assert bc.amount_credits == purchase.amount_credits
    assert bc.idempotency_key == purchase.idempotency_key
    assert bc.policy_decision_id == purchase.policy_decision_id
    assert bc.authorization_token_hash == purchase.authorization_token_hash
    assert bc.data_payload == purchase.encode_calldata()
    assert bc.data_payload.startswith("0x")
    assert len(bc.compute_intent_hash()) == 64


def test_to_blockchain_intent_hash_stable_for_identical_purchase():
    p1 = _valid_purchase()
    p2 = _valid_purchase()
    assert p1.compute_purchase_intent_hash() == p2.compute_purchase_intent_hash()
    assert p1.to_blockchain_intent().compute_intent_hash() == p2.to_blockchain_intent().compute_intent_hash()


def test_beneficiary_bytes32_form_accepted():
    addr = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    b32 = address_to_beneficiary_bytes32(addr)
    intent = _valid_purchase(beneficiary=b32)
    assert intent.beneficiary_as_bytes32() == b32.lower()
    calldata = intent.encode_calldata()
    assert calldata == encode_buy_and_activate_calldata(
        intent.usdg_in, intent.min_credit_out, addr, intent.max_fills
    )


def test_action_type_enum_includes_purchase():
    from src.domain.enums import ActionType

    assert ActionType.ORBIO_CREDIT_PURCHASE.value == "ORBIO_CREDIT_PURCHASE"
    assert ActionType.ORBIO_CREDIT_PURCHASE != ActionType.BLOCKCHAIN_TRANSACTION
