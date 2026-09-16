"""Unit tests for LocalKeySigner and NonceManager."""

import re
import pytest

from src.domain.blockchain import BlockchainTransactionIntent
from src.settlement.blockchain.nonce_manager import NonceManager
from src.settlement.blockchain.signer import LocalKeySigner

TEST_PRIVKEY = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
EXPECTED_ADDRESS = "0x90F8bf6A479f320ead074411a4B0e7944Ea8c9C1"


def test_signer_address_and_secret_redaction():
    signer = LocalKeySigner(TEST_PRIVKEY)
    assert signer.address.lower() == EXPECTED_ADDRESS.lower()

    # Secret masking in string and repr representations
    repr_str = repr(signer)
    str_str = str(signer)
    assert "***REDACTED***" in repr_str
    assert "***REDACTED***" in str_str
    assert TEST_PRIVKEY[2:] not in repr_str
    assert TEST_PRIVKEY[2:] not in str_str


def test_signer_invalid_key_raises():
    with pytest.raises(ValueError):
        LocalKeySigner("0xinvalid_short_key")


def test_signer_signs_valid_eip1559_transaction():
    signer = LocalKeySigner(TEST_PRIVKEY)
    intent = BlockchainTransactionIntent(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        operation_id="cop-signer-1",
        chain_id=11155111,
        recipient="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        amount_wei=10_000_000,
        amount_credits=10,
        idempotency_key="key-signer-1",
        policy_decision_id="dec-1",
        authorization_token_hash="auth-1",
    )
    raw_tx_bytes, tx_hash = signer.sign_transaction(intent, nonce=0)

    assert isinstance(raw_tx_bytes, bytes)
    assert len(raw_tx_bytes) > 0
    assert re.match(r"^0x[0-9a-fA-F]{64}$", tx_hash)


def test_nonce_manager_sequential_and_idempotent():
    manager = NonceManager()
    sender = EXPECTED_ADDRESS
    chain_id = 11155111

    # First allocation starts at on-chain nonce (e.g. 5)
    nonce1 = manager.get_or_allocate_nonce(
        sender=sender,
        chain_id=chain_id,
        operation_id="op-1",
        idempotency_key="idemp-1",
        on_chain_nonce_fetcher=lambda: 5,
    )
    assert nonce1 == 5

    # Retrying the exact same operation and idempotency key returns identical nonce!
    nonce1_retry = manager.get_or_allocate_nonce(
        sender=sender,
        chain_id=chain_id,
        operation_id="op-1",
        idempotency_key="idemp-1",
        on_chain_nonce_fetcher=lambda: 5,
    )
    assert nonce1_retry == 5

    # New operation increments sequentially to 6
    nonce2 = manager.get_or_allocate_nonce(
        sender=sender,
        chain_id=chain_id,
        operation_id="op-2",
        idempotency_key="idemp-2",
        on_chain_nonce_fetcher=lambda: 5,
    )
    assert nonce2 == 6
