"""Unit tests for mainnet activation driver configuration (no broadcast)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domain.orbio_activation import (
    MAX_ACTIVATION_AMOUNT,
    ORBIO_ACTIVATION_CHAIN_ID,
    ORBIO_CREDIT_ACTIVATION_CONTRACT,
    encode_activate_calldata,
)
from src.governance.orbio_activation_rules import OrbioCreditActivationPolicy

# Load driver module from scripts/ without requiring package install
_DRIVER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "execute_orbio_activation_mainnet.py"
_spec = importlib.util.spec_from_file_location("execute_orbio_activation_mainnet", _DRIVER_PATH)
driver = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["execute_orbio_activation_mainnet"] = driver
_spec.loader.exec_module(driver)


def test_expected_calldata_matches_one_credit():
    assert encode_activate_calldata(1_000_000) == driver.EXPECTED_CALLDATA
    assert driver.EXPECTED_CALLDATA.startswith("0xb260c42a")


def test_load_config_missing_private_key(monkeypatch):
    monkeypatch.delenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("KALYX_BLOCKCHAIN_RPC_URL", "https://example.invalid")
    args = SimpleNamespace(confirm_mainnet_activation=True, dry_run=False)
    with pytest.raises(driver.ActivationDriverError, match="PRIVATE_KEY"):
        driver.load_config(args)


def test_load_config_missing_rpc(monkeypatch):
    monkeypatch.setenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.delenv("KALYX_BLOCKCHAIN_RPC_URL", raising=False)
    args = SimpleNamespace(confirm_mainnet_activation=True, dry_run=False)
    with pytest.raises(driver.ActivationDriverError, match="RPC_URL"):
        driver.load_config(args)


def test_load_config_missing_confirm_flag(monkeypatch):
    monkeypatch.setenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("KALYX_BLOCKCHAIN_RPC_URL", "https://example.invalid")
    args = SimpleNamespace(confirm_mainnet_activation=False, dry_run=False)
    with pytest.raises(driver.ActivationDriverError, match="confirm-mainnet-activation"):
        driver.load_config(args)


def test_load_config_ok(monkeypatch):
    monkeypatch.setenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("KALYX_BLOCKCHAIN_RPC_URL", "https://example.invalid")
    args = SimpleNamespace(confirm_mainnet_activation=True, dry_run=True)
    cfg = driver.load_config(args)
    assert cfg.confirm is True
    assert cfg.dry_run is True


def test_build_signer_rejects_wrong_address():
    wrong_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    with pytest.raises(driver.ActivationDriverError, match="signer address"):
        driver.build_signer(wrong_key)


def test_validate_intent_rejects_wrong_chain():
    intent = driver.build_intent()
    bad = intent.model_copy(update={"chain_id": 46630})
    with pytest.raises(driver.ActivationDriverError):
        driver.validate_intent_hard_bounds(bad)


def test_validate_intent_rejects_wrong_contract():
    intent = driver.build_intent()
    bad = intent.model_copy(
        update={"credit_contract": "0x0000000000000000000000000000000000000001"}
    )
    with pytest.raises(driver.ActivationDriverError, match="credit_contract"):
        driver.validate_intent_hard_bounds(bad)


def test_validate_intent_rejects_wrong_amount():
    intent = driver.build_intent()
    bad = intent.model_copy(update={"amount": 2_000_000})
    with pytest.raises(driver.ActivationDriverError, match="amount"):
        driver.validate_intent_hard_bounds(bad)


def test_validate_intent_ok_for_canonical():
    intent = driver.build_intent()
    assert intent.chain_id == ORBIO_ACTIVATION_CHAIN_ID
    assert intent.credit_contract == ORBIO_CREDIT_ACTIVATION_CONTRACT
    assert intent.amount == MAX_ACTIVATION_AMOUNT
    driver.validate_intent_hard_bounds(intent)


def test_authorize_dry_path_succeeds_without_rpc():
    intent = driver.build_intent()
    intent2, approval, bundle = driver.authorize(intent)
    preparation, operation, org, decision = bundle
    assert preparation.is_authorized
    assert decision.is_allowed()
    assert approval.activation_amount == MAX_ACTIVATION_AMOUNT
    assert operation.parameters["chain_id"] == 4663
    assert operation.parameters["data_payload"] == driver.EXPECTED_CALLDATA


def test_main_dry_run_fails_without_env(monkeypatch):
    monkeypatch.delenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("KALYX_BLOCKCHAIN_RPC_URL", raising=False)
    rc = driver.main(["--confirm-mainnet-activation", "--dry-run"])
    assert rc == 1


def test_simulation_runtime_rejected_by_policy():
    intent = driver.build_intent()
    policy = OrbioCreditActivationPolicy()
    approval = policy.issue_human_approval(intent)
    d = policy.evaluate(intent, human_approval=approval, runtime_mode="simulation")
    assert not d.is_allowed()


def test_load_config_external_mode_without_private_key(monkeypatch):
    monkeypatch.delenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("KALYX_BLOCKCHAIN_RPC_URL", "https://example.invalid")
    args = SimpleNamespace(
        confirm_mainnet_activation=True,
        dry_run=True,
        signer_mode="external",
        operator_address=driver.EXPECTED_OPERATOR,
        signed_tx_hex=None,
        export_unsigned_tx=False,
        reconcile_tx=None,
    )
    cfg = driver.load_config(args)
    assert cfg.signer_mode == "external"
    assert cfg.private_key is None
    assert cfg.operator_address == driver.EXPECTED_OPERATOR


def test_load_config_external_mode_rejects_wrong_operator(monkeypatch):
    monkeypatch.delenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("KALYX_BLOCKCHAIN_RPC_URL", "https://example.invalid")
    args = SimpleNamespace(
        confirm_mainnet_activation=True,
        dry_run=True,
        signer_mode="external",
        operator_address="0x0000000000000000000000000000000000000001",
        signed_tx_hex=None,
        export_unsigned_tx=False,
        reconcile_tx=None,
    )
    with pytest.raises(driver.ActivationDriverError, match="operator address"):
        driver.load_config(args)


def test_build_signer_external_mode():
    from src.settlement.blockchain.signer import ExternalTransactionSigner

    cfg = driver.DriverConfig(
        signer_mode="external",
        rpc_url="https://example.invalid",
        confirm=True,
        dry_run=True,
        operator_address=driver.EXPECTED_OPERATOR,
    )
    signer = driver.build_signer(cfg)
    assert isinstance(signer, ExternalTransactionSigner)
    assert signer.address.lower() == driver.EXPECTED_OPERATOR.lower()


def test_reconcile_via_tx_hash_valid():
    class MockRpc:
        def get_transaction_by_hash(self, tx_hash):
            return {
                "from": driver.EXPECTED_OPERATOR,
                "to": ORBIO_CREDIT_ACTIVATION_CONTRACT,
                "input": driver.EXPECTED_CALLDATA,
            }

        def get_transaction_receipt(self, tx_hash):
            from src.domain.orbio_activation import ACTIVATED_EVENT_TOPIC0
            return {
                "status": 1,
                "transactionHash": tx_hash,
                "to": ORBIO_CREDIT_ACTIVATION_CONTRACT,
                "logs": [
                    {
                        "address": ORBIO_CREDIT_ACTIVATION_CONTRACT,
                        "topics": [
                            ACTIVATED_EVENT_TOPIC0,
                            "0x" + (42).to_bytes(32, "big").hex(),
                            "0x" + "0" * 24 + driver.EXPECTED_OPERATOR[2:].lower(),
                            "0x" + "0" * 64,
                        ],
                        "data": "0x" + (1_000_000).to_bytes(32, "big").hex(),
                    }
                ],
            }

    intent = driver.build_intent()
    out = driver.reconcile_via_tx_hash(
        rpc=MockRpc(),
        tx_hash="0x" + "aa" * 32,
        intent=intent,
        operator_address=driver.EXPECTED_OPERATOR,
    )
    assert out["outcome"] == "SUCCESS"
    assert out["state"] == "VERIFIED_ON_CHAIN"


def test_reconcile_via_tx_hash_calldata_mismatch_fails_closed():
    class MockRpc:
        def get_transaction_by_hash(self, tx_hash):
            return {
                "from": driver.EXPECTED_OPERATOR,
                "to": ORBIO_CREDIT_ACTIVATION_CONTRACT,
                "input": "0xinvalid_calldata",
            }

    intent = driver.build_intent()
    with pytest.raises(driver.ActivationDriverError, match="calldata mismatch"):
        driver.reconcile_via_tx_hash(
            rpc=MockRpc(),
            tx_hash="0x" + "bb" * 32,
            intent=intent,
            operator_address=driver.EXPECTED_OPERATOR,
        )


def test_reconcile_via_tx_hash_sender_mismatch_fails_closed():
    class MockRpc:
        def get_transaction_by_hash(self, tx_hash):
            return {
                "from": "0x0000000000000000000000000000000000000002",
                "to": ORBIO_CREDIT_ACTIVATION_CONTRACT,
                "input": driver.EXPECTED_CALLDATA,
            }

    intent = driver.build_intent()
    with pytest.raises(driver.ActivationDriverError, match="transaction sender"):
        driver.reconcile_via_tx_hash(
            rpc=MockRpc(),
            tx_hash="0x" + "cc" * 32,
            intent=intent,
            operator_address=driver.EXPECTED_OPERATOR,
        )

