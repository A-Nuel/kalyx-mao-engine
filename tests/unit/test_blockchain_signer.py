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


def test_external_signer_initialization_and_validation():
    from src.settlement.blockchain.signer import ExternalTransactionSigner
    from web3 import Web3

    signer = ExternalTransactionSigner(EXPECTED_ADDRESS.lower())
    assert signer.address == Web3.to_checksum_address(EXPECTED_ADDRESS)
    assert "***REDACTED***" not in repr(signer)  # No secret to redact
    assert EXPECTED_ADDRESS in repr(signer)

    with pytest.raises(ValueError, match="Invalid Ethereum address"):
        ExternalTransactionSigner("not-an-address")


def test_external_signer_build_transaction_payload():
    from src.settlement.blockchain.signer import ExternalTransactionSigner

    signer = ExternalTransactionSigner(EXPECTED_ADDRESS)
    intent = BlockchainTransactionIntent(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        operation_id="cop-ext-1",
        chain_id=4663,
        recipient="0xe33322da1380e61e5ae5dfb21e7f62924c73004c",
        amount_wei=0,
        amount_credits=1,
        max_fee_per_gas=25_000_000_000,
        max_priority_fee_per_gas=1_500_000_000,
        gas_limit=150_000,
        data_payload="0xb260c42a00000000000000000000000000000000000000000000000000000000000f4240",
        idempotency_key="key-ext-1",
        policy_decision_id="dec-1",
        authorization_token_hash="auth-1",
    )
    payload = signer.build_transaction_payload(intent, nonce=3)
    assert payload["type"] == 2
    assert payload["chainId"] == 4663
    assert payload["nonce"] == 3
    assert payload["to"].lower() == "0xe33322da1380e61e5ae5dfb21e7f62924c73004c"
    assert payload["data"] == "0xb260c42a00000000000000000000000000000000000000000000000000000000000f4240"
    assert payload["value"] == 0
    assert payload["gas"] == 150_000


def test_external_signer_sign_transaction_valid():
    from eth_account import Account
    from src.settlement.blockchain.signer import ExternalTransactionSigner

    acct = Account.create()
    signer = ExternalTransactionSigner(acct.address)

    intent = BlockchainTransactionIntent(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        operation_id="cop-ext-2",
        chain_id=4663,
        recipient="0xe33322da1380e61e5ae5dfb21e7f62924c73004c",
        amount_wei=0,
        amount_credits=1,
        max_fee_per_gas=25_000_000_000,
        max_priority_fee_per_gas=1_500_000_000,
        gas_limit=150_000,
        data_payload="0xb260c42a00000000000000000000000000000000000000000000000000000000000f4240",
        idempotency_key="key-ext-2",
        policy_decision_id="dec-1",
        authorization_token_hash="auth-1",
    )
    tx_dict = signer.build_transaction_payload(intent, nonce=0)
    signed_tx = Account.sign_transaction(tx_dict, acct.key)
    raw_hex = signed_tx.raw_transaction.hex()

    signer.set_signed_raw_tx_hex(raw_hex)
    raw_bytes, tx_hash = signer.sign_transaction(intent, nonce=0)

    assert isinstance(raw_bytes, bytes)
    expected_tx_hash = "0x" + signed_tx.hash.hex().lower()
    assert tx_hash == expected_tx_hash


def test_external_signer_sign_transaction_mismatched_address_raises():
    from eth_account import Account
    from src.settlement.blockchain.signer import ExternalTransactionSigner

    acct_a = Account.create()
    acct_b = Account.create()

    # Signer expects acct_a
    signer = ExternalTransactionSigner(acct_a.address)

    intent = BlockchainTransactionIntent(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        operation_id="cop-ext-3",
        chain_id=4663,
        recipient="0xe33322da1380e61e5ae5dfb21e7f62924c73004c",
        amount_wei=0,
        amount_credits=1,
        idempotency_key="key-ext-3",
        policy_decision_id="dec-1",
        authorization_token_hash="auth-1",
    )
    tx_dict = signer.build_transaction_payload(intent, nonce=0)
    # But transaction is signed by acct_b!
    signed_by_b = Account.sign_transaction(tx_dict, acct_b.key)

    signer.set_signed_raw_tx_hex(signed_by_b.raw_transaction.hex())
    with pytest.raises(ValueError, match="Signer address mismatch"):
        signer.sign_transaction(intent, nonce=0)


def test_external_signer_missing_raw_tx_raises_with_payload():
    from src.settlement.blockchain.signer import ExternalTransactionSigner

    signer = ExternalTransactionSigner(EXPECTED_ADDRESS)
    intent = BlockchainTransactionIntent(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        operation_id="cop-ext-4",
        chain_id=4663,
        recipient="0xe33322da1380e61e5ae5dfb21e7f62924c73004c",
        amount_wei=0,
        amount_credits=1,
        idempotency_key="key-ext-4",
        policy_decision_id="dec-1",
        authorization_token_hash="auth-1",
    )
    with pytest.raises(ValueError, match="External signature required"):
        signer.sign_transaction(intent, nonce=1)

