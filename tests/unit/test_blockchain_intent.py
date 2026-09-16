"""Unit tests for BlockchainTransactionIntent and BlockchainReceiptEvidence."""

import copy
import pytest
from pydantic import ValidationError

from src.domain.blockchain import BlockchainReceiptEvidence, BlockchainTransactionIntent


def test_blockchain_transaction_intent_valid_creation():
    intent = BlockchainTransactionIntent(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        mission_id="m-1",
        operation_id="cop-1",
        chain_id=11155111,
        network="sepolia",
        recipient="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        amount_wei=10_000_000_000_000_000,
        amount_credits=10,
        idempotency_key="org-alpha:cop-1",
        policy_decision_id="dec-1",
        authorization_token_hash="token-hash-123",
    )
    assert intent.recipient == "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"
    assert intent.chain_id == 11155111
    assert intent.amount_credits == 10
    hash_val = intent.compute_intent_hash()
    assert len(hash_val) == 64


def test_blockchain_transaction_intent_invalid_recipient_raises():
    with pytest.raises(ValidationError):
        BlockchainTransactionIntent(
            tenant_id="tenant-alpha",
            organisation_id="org-alpha",
            operation_id="cop-1",
            recipient="not-an-eth-address",
            idempotency_key="key-1",
            policy_decision_id="dec-1",
            authorization_token_hash="hash-1",
        )


def test_blockchain_transaction_intent_invalid_token_contract_raises():
    with pytest.raises(ValidationError):
        BlockchainTransactionIntent(
            tenant_id="tenant-alpha",
            organisation_id="org-alpha",
            operation_id="cop-1",
            recipient="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
            token_contract="0xinvalid",
            idempotency_key="key-1",
            policy_decision_id="dec-1",
            authorization_token_hash="hash-1",
        )


def test_blockchain_transaction_intent_hash_tamper_detection():
    intent1 = BlockchainTransactionIntent(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        operation_id="cop-1",
        recipient="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        amount_wei=1000,
        amount_credits=10,
        idempotency_key="key-1",
        policy_decision_id="dec-1",
        authorization_token_hash="hash-1",
    )
    h1 = intent1.compute_intent_hash()

    # Tampering with recipient changes hash
    intent_tampered_recipient = intent1.model_copy(update={"recipient": "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"})
    assert intent_tampered_recipient.compute_intent_hash() != h1

    # Tampering with amount changes hash
    intent_tampered_amount = intent1.model_copy(update={"amount_wei": 2000})
    assert intent_tampered_amount.compute_intent_hash() != h1

    # Tampering with chain changes hash
    intent_tampered_chain = intent1.model_copy(update={"chain_id": 1})
    assert intent_tampered_chain.compute_intent_hash() != h1


def test_blockchain_receipt_evidence_hash():
    evidence = BlockchainReceiptEvidence(
        network="sepolia",
        chain_id=11155111,
        transaction_hash="0x" + "a" * 64,
        status="confirmed",
        block_number=12345,
        gas_used=21000,
        effective_gas_price=25000000000,
        network_fee_wei=525000000000000,
        sender="0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
        recipient="0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
        amount_wei=1000000,
        intent_hash="intent-hash-xyz",
    )
    ev_hash = evidence.compute_evidence_hash()
    assert len(ev_hash) == 64
