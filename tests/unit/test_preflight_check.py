"""Unit tests for Phase 18 preflight and RPC get_code validation."""

import os
from unittest.mock import MagicMock, patch
import pytest

from src.settlement.blockchain.rpc_client import HttpEvmRpcClient, SimulatedEvmRpcClient
from scripts.execute_testnet_settlement import run_preflight


def test_simulated_rpc_get_code():
    rpc = SimulatedEvmRpcClient(chain_id=11155111)
    # Default contract code returns bytecode
    code = rpc.get_code("0x6951ffd32630b05e06f50062aea801625a58ebc0")
    assert code.startswith("0x")
    assert len(code) > 2

    # Zero address returns 0x
    assert rpc.get_code("0x0000000000000000000000000000000000000000") == "0x"

    # Custom set_code
    rpc.set_code("0x1234567890123456789012345678901234567890", "0xdeadbeef")
    assert rpc.get_code("0x1234567890123456789012345678901234567890") == "0xdeadbeef"


def test_http_rpc_get_code():
    from web3 import Web3
    rpc = HttpEvmRpcClient(rpc_url="http://localhost:8545")
    raw_addr = "0x6951ffd32630b05e06f50062aea801625a58ebc0"
    checksum_addr = Web3.to_checksum_address(raw_addr)
    with patch.object(rpc, "_call", return_value="0x6080604052") as mock_call:
        res = rpc.get_code(raw_addr)
        assert res == "0x6080604052"
        mock_call.assert_called_once_with(
            "eth_getCode",
            [checksum_addr, "latest"],
        )


def test_run_preflight_simulated_success():
    rc = run_preflight(simulate=True)
    assert rc == 0


def test_run_preflight_testnet_unconfigured_blocked(monkeypatch):
    monkeypatch.delenv("KALYX_BLOCKCHAIN_RPC_URL", raising=False)
    monkeypatch.delenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", raising=False)
    rc = run_preflight(simulate=False)
    assert rc == 1


def test_run_preflight_testnet_with_mocked_http_rpc(monkeypatch):
    monkeypatch.setenv("KALYX_BLOCKCHAIN_RPC_URL", "https://eth-sepolia.example.com/v2/dummy")
    monkeypatch.setenv(
        "KALYX_BLOCKCHAIN_PRIVATE_KEY",
        "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d",
    )
    with patch("scripts.execute_testnet_settlement.HttpEvmRpcClient") as mock_client_cls:
        mock_instance = MagicMock()
        mock_instance.get_chain_id.return_value = 11155111
        mock_instance.get_block_number.return_value = 5432100
        mock_instance.get_balance.return_value = 500000000000000000
        mock_instance.get_transaction_count.return_value = 3
        mock_instance.get_code.return_value = "0x60806040"
        mock_client_cls.return_value = mock_instance

        rc = run_preflight(simulate=False, chain_id=11155111)
        assert rc == 0
