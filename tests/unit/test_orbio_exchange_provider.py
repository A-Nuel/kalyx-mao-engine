"""Unit tests for OrbioExchangeProvider (Phase 14B.10 / Testnet execution boundary)."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from src.domain.entities import ConsequentialOperation
from src.domain.enums import ActionType, ProviderOutcome
from src.settlement.orbio_exchange_provider import OrbioExchangeProvider
from src.settlement.blockchain.nonce_manager import NonceManager
from src.settlement.blockchain.signer import LocalKeySigner


FAKE_PRIVATE_KEY = "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
FAKE_CONTRACT = "0x1111111111111111111111111111111111111111"


@pytest.fixture
def mock_rpc_client():
    client = MagicMock()
    client.get_chain_id.return_value = 11155111
    client.get_gas_price.return_value = 20_000_000_000
    client.estimate_gas.return_value = 120_000
    client.get_transaction_count.return_value = 0
    client.send_raw_transaction.return_value = "0xdeadbeef" + "0" * 56
    return client


@pytest.fixture
def signer():
    return LocalKeySigner(FAKE_PRIVATE_KEY)


@pytest.fixture
def nonce_manager():
    return NonceManager()


@pytest.fixture
def provider(mock_rpc_client, signer, nonce_manager):
    return OrbioExchangeProvider(
        rpc_client=mock_rpc_client,
        signer=signer,
        nonce_manager=nonce_manager,
        default_chain_id=11155111,
        wait_for_receipt_seconds=1.0,
    )


def test_prepare_validation(provider):
    op = ConsequentialOperation(
        id="cop-1",
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        proposal_id="prop-1",
        decision_id="dec-1",
        idempotency_key="key-1",
        action_type=ActionType.ORBIO_CREDIT_PURCHASE,
        target=FAKE_CONTRACT,
        amount=5,
        provider_name="orbio-exchange",
        parameters={"exchange_contract": FAKE_CONTRACT},
    )
    assert provider.prepare(op) is True

    op_invalid = ConsequentialOperation(
        id="cop-2",
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        proposal_id="prop-2",
        decision_id="dec-2",
        idempotency_key="key-2",
        action_type=ActionType.ORBIO_CREDIT_PURCHASE,
        target="",
        amount=5,
        provider_name="orbio-exchange",
        parameters={},
    )
    assert provider.prepare(op_invalid) is False


def test_execute_transaction_confirmed_with_receipt(provider, mock_rpc_client):
    op = ConsequentialOperation(
        id="cop-exec-1",
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        proposal_id="prop-1",
        decision_id="dec-1",
        idempotency_key="key-exec-1",
        action_type=ActionType.ORBIO_CREDIT_PURCHASE,
        target=FAKE_CONTRACT,
        amount=10,
        provider_name="orbio-exchange",
        parameters={
            "exchange_contract": FAKE_CONTRACT,
            "chain_id": 11155111,
            "data_payload": "0x40c10f19000000000000000000000000",
        },
    )

    tx_hash = "0xdeadbeef" + "0" * 56
    mock_rpc_client.send_raw_transaction.return_value = tx_hash
    mock_rpc_client.get_transaction_receipt.return_value = {
        "status": 1,
        "blockNumber": 123456,
        "gasUsed": 85000,
        "logs": [
            {
                # Orbio Activated event signature topic
                "topics": ["0x93309a9f074d2847ffb09462b0833118cf5ee1c9bf4108ce8a30cf1ef2e86fcf"],
                "data": "0x",
            }
        ],
    }

    result = provider.execute(op)
    assert result.outcome == "SUCCESS"
    assert result.provider_reference == tx_hash
    assert result.raw_response["raw_receipt"]["blockNumber"] == 123456
    assert result.raw_response["raw_receipt"]["gasUsed"] == 85000
    assert len(result.raw_response["events"]) == 1
    assert result.evidence_hash is not None


def test_execute_transaction_pending_returns_unknown(provider, mock_rpc_client):
    op = ConsequentialOperation(
        id="cop-exec-2",
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        proposal_id="prop-2",
        decision_id="dec-2",
        idempotency_key="key-exec-2",
        action_type=ActionType.ORBIO_CREDIT_PURCHASE,
        target=FAKE_CONTRACT,
        amount=5,
        provider_name="orbio-exchange",
        parameters={
            "exchange_contract": FAKE_CONTRACT,
            "chain_id": 11155111,
            "data_payload": "0x40c10f19",
        },
    )

    tx_hash = "0xdeadbeef" + "0" * 56
    mock_rpc_client.send_raw_transaction.return_value = tx_hash
    mock_rpc_client.get_transaction_receipt.return_value = None  # Still pending confirmation

    result = provider.execute(op)
    assert result.outcome == ProviderOutcome.UNKNOWN
    assert result.provider_reference == tx_hash
    assert result.raw_response.get("status") == "pending"
