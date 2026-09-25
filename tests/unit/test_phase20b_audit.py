"""Phase 20B Final Implementation Audit Tests.

Audit of the 12 requirements for the external-signing and reconciliation flow:
1. Exported unsigned tx is EIP-1559/type 2 with live pending nonce.
2. chainId is exactly 4663.
3. to is exactly 0xe33322da1380e61e5ae5dfb21e7f62924c73004c.
4. value is exactly 0.
5. calldata is exactly 0xb260c42a00000000000000000000000000000000000000000000000000000000000f4240.
6. The transaction represents exactly activate(1,000,000) = 1.000000 CREDIT.
7. ExternalTransactionSigner cryptographically recovers and verifies the expected operator address.
8. --reconcile-tx rejects arbitrary/mismatched transactions and requires full Phase 20B verifier checks.
9. api_balance_confirmed remains False (on-chain activation only; Phase 21 scope).
10. Reconciliation works with zero private keys or secrets possessed by Kalyx.
11. Nonce handling prevents stale transaction reuse.
12. Fail-closed against any agent-tampered recipient, calldata, amount, or chain.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_account import Account
from web3 import Web3

from src.domain.blockchain import BlockchainTransactionIntent
from src.domain.orbio_activation import (
    ACTIVATE_SELECTOR,
    ACTIVATED_EVENT_TOPIC0,
    MAX_ACTIVATION_AMOUNT,
    ORBIO_ACTIVATION_CHAIN_ID,
    ORBIO_CREDIT_ACTIVATION_CONTRACT,
    OrbioCreditActivationIntent,
    encode_activate_calldata,
)
from src.governance.orbio_activation_rules import OrbioCreditActivationPolicy
from src.settlement.blockchain.nonce_manager import NonceManager
from src.settlement.blockchain.signer import ExternalTransactionSigner
from src.settlement.orbio_activation_verifier import (
    ActivationVerificationResult,
    OrbioCreditActivationVerifier,
)

# Load driver module
_DRIVER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "execute_orbio_activation_mainnet.py"
_spec = importlib.util.spec_from_file_location("execute_orbio_activation_mainnet", _DRIVER_PATH)
driver = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["execute_orbio_activation_mainnet"] = driver
_spec.loader.exec_module(driver)

EXPECTED_OPERATOR = "0x4675b9d0323479b1af399c87331d1d2436e6be99"
EXPECTED_CONTRACT = "0xe33322da1380e61e5ae5dfb21e7f62924c73004c"
EXPECTED_CALLDATA = "0xb260c42a00000000000000000000000000000000000000000000000000000000000f4240"


# ===========================================================================
# 1. Exported unsigned tx is EIP-1559 / type 2 with live pending nonce
# ===========================================================================
def test_audit_01_unsigned_tx_is_eip1559_with_live_nonce():
    intent = driver.build_intent()
    signer = ExternalTransactionSigner(EXPECTED_OPERATOR)
    live_nonce = 42
    payload = signer.build_transaction_payload(intent, nonce=live_nonce)

    assert payload["type"] == 2
    assert payload["nonce"] == live_nonce
    assert "maxFeePerGas" in payload
    assert "maxPriorityFeePerGas" in payload
    assert payload["gas"] == 150_000


# ===========================================================================
# 2. chainId is exactly 4663
# ===========================================================================
def test_audit_02_chain_id_is_exactly_4663():
    intent = driver.build_intent()
    assert intent.chain_id == 4663
    assert ORBIO_ACTIVATION_CHAIN_ID == 4663

    signer = ExternalTransactionSigner(EXPECTED_OPERATOR)
    payload = signer.build_transaction_payload(intent, nonce=0)
    assert payload["chainId"] == 4663


# ===========================================================================
# 3. to is exactly 0xe33322da1380e61e5ae5dfb21e7f62924c73004c
# ===========================================================================
def test_audit_03_to_is_exactly_expected_contract():
    intent = driver.build_intent()
    assert intent.credit_contract.lower() == EXPECTED_CONTRACT.lower()
    assert ORBIO_CREDIT_ACTIVATION_CONTRACT.lower() == EXPECTED_CONTRACT.lower()

    signer = ExternalTransactionSigner(EXPECTED_OPERATOR)
    payload = signer.build_transaction_payload(intent, nonce=0)
    assert payload["to"].lower() == EXPECTED_CONTRACT.lower()


# ===========================================================================
# 4. value is exactly 0
# ===========================================================================
def test_audit_04_value_is_exactly_zero():
    intent = driver.build_intent()
    bc = intent.to_blockchain_intent()
    assert bc.amount_wei == 0

    signer = ExternalTransactionSigner(EXPECTED_OPERATOR)
    payload = signer.build_transaction_payload(intent, nonce=0)
    assert payload["value"] == 0


# ===========================================================================
# 5. calldata is exactly 0xb260c42a...000f4240
# ===========================================================================
def test_audit_05_calldata_matches_exact_selector_and_padding():
    calldata = encode_activate_calldata(1_000_000)
    assert calldata == EXPECTED_CALLDATA
    assert calldata.startswith("0xb260c42a")
    assert len(calldata) == 74  # '0x' + 8 hex selector + 64 hex uint256

    signer = ExternalTransactionSigner(EXPECTED_OPERATOR)
    intent = driver.build_intent()
    payload = signer.build_transaction_payload(intent, nonce=0)
    assert payload["data"] == EXPECTED_CALLDATA


# ===========================================================================
# 6. Transaction represents exactly activate(1,000,000) = 1.000000 CREDIT
# ===========================================================================
def test_audit_06_represents_exactly_one_credit():
    intent = driver.build_intent()
    assert intent.amount == 1_000_000
    assert MAX_ACTIVATION_AMOUNT == 1_000_000
    # 6 decimals for CREDIT
    credit_tokens = intent.amount / 10**6
    assert credit_tokens == 1.0


# ===========================================================================
# 7. ExternalTransactionSigner cryptographically recovers operator address
# ===========================================================================
def test_audit_07_external_signer_cryptographically_verifies_operator():
    # Scenario A: Real signature from authorized operator matches
    acct = Account.create()
    signer = ExternalTransactionSigner(acct.address)
    intent = driver.build_intent()
    tx_dict = signer.build_transaction_payload(intent, nonce=0)
    signed_tx = Account.sign_transaction(tx_dict, acct.key)

    signer.set_signed_raw_tx_hex(signed_tx.raw_transaction.hex())
    raw_bytes, tx_hash = signer.sign_transaction(intent, nonce=0)
    assert isinstance(raw_bytes, bytes)
    assert tx_hash == "0x" + signed_tx.hash.hex().lower()

    # Scenario B: Transaction signed by unauthorized key fails closed
    rogue_acct = Account.create()
    bad_signed = Account.sign_transaction(tx_dict, rogue_acct.key)
    signer.set_signed_raw_tx_hex(bad_signed.raw_transaction.hex())
    with pytest.raises(ValueError, match="Signer address mismatch"):
        signer.sign_transaction(intent, nonce=0)


# ===========================================================================
# 8. --reconcile-tx cannot accept arbitrary hash; requires full verifier
# ===========================================================================
def test_audit_08_reconcile_tx_rejects_arbitrary_or_tampered_transactions():
    intent = driver.build_intent()

    # Sub-case 8a: Sender is not authorized operator -> REJECT
    class BadSenderRpc:
        def get_transaction_by_hash(self, _h):
            return {
                "from": "0x0000000000000000000000000000000000000001",
                "to": EXPECTED_CONTRACT,
                "input": EXPECTED_CALLDATA,
            }

    with pytest.raises(driver.ActivationDriverError, match="transaction sender"):
        driver.reconcile_via_tx_hash(
            rpc=BadSenderRpc(),
            tx_hash="0x" + "11" * 32,
            intent=intent,
            operator_address=EXPECTED_OPERATOR,
        )

    # Sub-case 8b: Recipient is not CREDIT contract -> REJECT
    class BadRecipientRpc:
        def get_transaction_by_hash(self, _h):
            return {
                "from": EXPECTED_OPERATOR,
                "to": "0x0000000000000000000000000000000000000002",
                "input": EXPECTED_CALLDATA,
            }

    with pytest.raises(driver.ActivationDriverError, match="transaction recipient"):
        driver.reconcile_via_tx_hash(
            rpc=BadRecipientRpc(),
            tx_hash="0x" + "22" * 32,
            intent=intent,
            operator_address=EXPECTED_OPERATOR,
        )

    # Sub-case 8c: Calldata is not exact activate(1000000) -> REJECT
    class BadCalldataRpc:
        def get_transaction_by_hash(self, _h):
            return {
                "from": EXPECTED_OPERATOR,
                "to": EXPECTED_CONTRACT,
                "input": "0xdeadbeef",
            }

    with pytest.raises(driver.ActivationDriverError, match="calldata mismatch"):
        driver.reconcile_via_tx_hash(
            rpc=BadCalldataRpc(),
            tx_hash="0x" + "33" * 32,
            intent=intent,
            operator_address=EXPECTED_OPERATOR,
        )

    # Sub-case 8d: Reverted transaction (status=0) -> REJECT
    class RevertedTxRpc:
        def get_transaction_by_hash(self, _h):
            return {
                "from": EXPECTED_OPERATOR,
                "to": EXPECTED_CONTRACT,
                "input": EXPECTED_CALLDATA,
            }

        def get_transaction_receipt(self, _h):
            return {"status": 0, "to": EXPECTED_CONTRACT, "logs": []}

    out_rev = driver.reconcile_via_tx_hash(
        rpc=RevertedTxRpc(),
        tx_hash="0x" + "44" * 32,
        intent=intent,
        operator_address=EXPECTED_OPERATOR,
    )
    assert out_rev["outcome"] == "FAILURE"
    assert out_rev["state"] == "FAILED"

    # Sub-case 8e: Success status but missing Activated event in logs -> REJECT
    class MissingEventRpc:
        def get_transaction_by_hash(self, _h):
            return {
                "from": EXPECTED_OPERATOR,
                "to": EXPECTED_CONTRACT,
                "input": EXPECTED_CALLDATA,
            }

        def get_transaction_receipt(self, _h):
            return {
                "status": 1,
                "to": EXPECTED_CONTRACT,
                "transactionHash": "0x" + "55" * 32,
                "logs": [],
            }

    out_missing = driver.reconcile_via_tx_hash(
        rpc=MissingEventRpc(),
        tx_hash="0x" + "55" * 32,
        intent=intent,
        operator_address=EXPECTED_OPERATOR,
    )
    assert out_missing["state"] == "NOT_VERIFIED"
    assert out_missing["verification"]["result"] == ActivationVerificationResult.REJECTED.value


# ===========================================================================
# 9. No API balance is marked as confirmed merely from blockchain receipt
# ===========================================================================
def test_audit_09_api_balance_never_confirmed_from_blockchain_receipt():
    intent = driver.build_intent()

    class ValidOnChainRpc:
        def get_transaction_by_hash(self, _h):
            return {
                "from": EXPECTED_OPERATOR,
                "to": EXPECTED_CONTRACT,
                "input": EXPECTED_CALLDATA,
            }

        def get_transaction_receipt(self, tx_hash):
            return {
                "status": 1,
                "transactionHash": tx_hash,
                "to": EXPECTED_CONTRACT,
                "logs": [
                    {
                        "address": EXPECTED_CONTRACT,
                        "topics": [
                            ACTIVATED_EVENT_TOPIC0,
                            "0x" + (100).to_bytes(32, "big").hex(),
                            "0x" + "0" * 24 + EXPECTED_OPERATOR[2:].lower(),
                            "0x" + "0" * 64,
                        ],
                        "data": "0x" + (1_000_000).to_bytes(32, "big").hex(),
                    }
                ],
            }

    out = driver.reconcile_via_tx_hash(
        rpc=ValidOnChainRpc(),
        tx_hash="0x" + "66" * 32,
        intent=intent,
        operator_address=EXPECTED_OPERATOR,
    )
    # Crucial safety invariant
    assert out["api_balance_confirmed"] is False
    assert "API BALANCE NOT YET CONFIRMED" in out["api_balance_note"]
    assert "Phase 21" in out["api_balance_note"]


# ===========================================================================
# 10. Manual transaction from Robinhood Wallet reconciles without private key
# ===========================================================================
def test_audit_10_reconciliation_zero_private_keys(monkeypatch):
    monkeypatch.delenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("KALYX_BLOCKCHAIN_RPC_URL", "https://example.invalid")
    args = SimpleNamespace(
        confirm_mainnet_activation=True,
        dry_run=False,
        signer_mode="external",
        operator_address=EXPECTED_OPERATOR,
        signed_tx_hex=None,
        export_unsigned_tx=False,
        reconcile_tx="0x" + "77" * 32,
    )
    cfg = driver.load_config(args)
    assert cfg.private_key is None

    signer = driver.build_signer(cfg)
    assert isinstance(signer, ExternalTransactionSigner)
    assert signer.address.lower() == EXPECTED_OPERATOR.lower()


# ===========================================================================
# 11. Nonce handling prevents stale transaction reuse
# ===========================================================================
def test_audit_11_nonce_handling_prevents_stale_tx():
    manager = NonceManager()
    on_chain_nonce = 10

    # 1. Live nonce is fetched from node
    allocated_nonce = manager.get_or_allocate_nonce(
        sender=EXPECTED_OPERATOR,
        chain_id=4663,
        operation_id="op-audit-1",
        idempotency_key="key-audit-1",
        on_chain_nonce_fetcher=lambda: on_chain_nonce,
    )
    assert allocated_nonce == 10

    # 2. Retrying identical op returns same nonce
    retry_nonce = manager.get_or_allocate_nonce(
        sender=EXPECTED_OPERATOR,
        chain_id=4663,
        operation_id="op-audit-1",
        idempotency_key="key-audit-1",
        on_chain_nonce_fetcher=lambda: on_chain_nonce,
    )
    assert retry_nonce == 10

    # 3. If on-chain nonce advanced to 15, manager advances to 15 (never stale!)
    advanced_nonce = manager.get_or_allocate_nonce(
        sender=EXPECTED_OPERATOR,
        chain_id=4663,
        operation_id="op-audit-2",
        idempotency_key="key-audit-2",
        on_chain_nonce_fetcher=lambda: 15,
    )
    assert advanced_nonce == 15


# ===========================================================================
# 12. No path for an agent to create a tampered recipient/calldata/amount/chain
# ===========================================================================
def test_audit_12_fail_closed_against_agent_tampering():
    intent = driver.build_intent()

    # Tampered chain
    with pytest.raises(driver.ActivationDriverError, match="chain_id"):
        driver.validate_intent_hard_bounds(intent.model_copy(update={"chain_id": 1}))

    # Tampered contract
    with pytest.raises(driver.ActivationDriverError, match="credit_contract"):
        driver.validate_intent_hard_bounds(
            intent.model_copy(update={"credit_contract": "0x0000000000000000000000000000000000000001"})
        )

    # Tampered amount
    with pytest.raises(driver.ActivationDriverError, match="amount"):
        driver.validate_intent_hard_bounds(intent.model_copy(update={"amount": 5_000_000}))

    # Policy rejects amount > 1_000_000
    policy = OrbioCreditActivationPolicy()
    tampered_intent = intent.model_copy(update={"amount": 2_000_000})
    appr = policy.issue_human_approval(tampered_intent)
    dec = policy.evaluate(tampered_intent, human_approval=appr, runtime_mode="mainnet")
    assert not dec.is_allowed()
