"""EVM JSON-RPC client interface, HTTP provider, and simulated test RPC."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, Optional, Protocol
from eth_account import Account
import httpx
from web3 import Web3


class IEvmRpcClient(Protocol):
    """Interface for EVM JSON-RPC interactions."""

    def get_chain_id(self) -> int:
        ...

    def get_block_number(self) -> int:
        ...

    def get_transaction_count(self, address: str, block: str = "pending") -> int:
        ...

    def get_balance(self, address: str, block: str = "latest") -> int:
        ...

    def get_code(self, address: str, block: str = "latest") -> str:
        ...

    def send_raw_transaction(self, raw_tx_bytes: bytes) -> str:
        ...

    def get_transaction_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        ...

    def get_transaction_by_hash(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        ...


class HttpEvmRpcClient:
    """Standard HTTP JSON-RPC client for live EVM networks (e.g. Sepolia)."""

    def __init__(self, rpc_url: str, timeout: float = 15.0):
        self.rpc_url = rpc_url.strip()
        self.timeout = timeout

    def _call(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"RPC error: {data['error']}")
            return data.get("result")

    def get_chain_id(self) -> int:
        res = self._call("eth_chainId", [])
        return int(res, 16) if isinstance(res, str) else int(res)

    def get_block_number(self) -> int:
        res = self._call("eth_blockNumber", [])
        return int(res, 16) if isinstance(res, str) else int(res)

    def get_transaction_count(self, address: str, block: str = "pending") -> int:
        res = self._call("eth_getTransactionCount", [Web3.to_checksum_address(address), block])
        return int(res, 16) if isinstance(res, str) else int(res)

    def get_balance(self, address: str, block: str = "latest") -> int:
        res = self._call("eth_getBalance", [Web3.to_checksum_address(address), block])
        return int(res, 16) if isinstance(res, str) else int(res)

    def get_code(self, address: str, block: str = "latest") -> str:
        res = self._call("eth_getCode", [Web3.to_checksum_address(address), block])
        return str(res) if res is not None else "0x"

    def send_raw_transaction(self, raw_tx_bytes: bytes) -> str:
        hex_data = "0x" + raw_tx_bytes.hex()
        res = self._call("eth_sendRawTransaction", [hex_data])
        return str(res).lower()

    def get_transaction_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        return self._call("eth_getTransactionReceipt", [tx_hash])

    def get_transaction_by_hash(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        return self._call("eth_getTransactionByHash", [tx_hash])


class SimulatedEvmRpcClient:
    """Deterministic in-memory simulated EVM RPC client for reliable automated testing.
    
    Models realistic blocks, receipts, pending pools, reverts, and transport drops.
    """

    def __init__(self, chain_id: int = 11155111, initial_block: int = 5_000_000):
        self.chain_id = chain_id
        self.block_number = initial_block
        self._balances: Dict[str, int] = {}
        self._nonces: Dict[str, int] = {}
        self._deployed_codes: Dict[str, str] = {}
        self._transactions: Dict[str, Dict[str, Any]] = {}
        self._receipts: Dict[str, Dict[str, Any]] = {}

        # Failure rules for testing
        self._timeout_hashes: set[str] = set()
        self._revert_hashes: set[str] = set()
        self._pending_hashes: set[str] = set()
        self._reject_submission_hashes: set[str] = set()
        self.timeout_all: bool = False
        self.revert_all: bool = False
        self.pending_all: bool = False

    def set_balance(self, address: str, balance_wei: int) -> None:
        self._balances[address.lower()] = balance_wei

    def set_code(self, address: str, code: str) -> None:
        self._deployed_codes[address.lower()] = code

    def set_timeout_rule(self, tx_hash: str) -> None:
        self._timeout_hashes.add(tx_hash.lower())

    def set_revert_rule(self, tx_hash: str) -> None:
        self._revert_hashes.add(tx_hash.lower())

    def set_pending_rule(self, tx_hash: str) -> None:
        self._pending_hashes.add(tx_hash.lower())

    def set_reject_submission_rule(self, tx_hash: str) -> None:
        self._reject_submission_hashes.add(tx_hash.lower())

    def set_revert_all_rules(self, revert: bool = True) -> None:
        self.revert_all = revert

    def set_timeout_all_rules(self, timeout: bool = True) -> None:
        self.timeout_all = timeout

    def set_pending_all_rules(self, pending: bool = True) -> None:
        self.pending_all = pending

    def get_chain_id(self) -> int:
        return self.chain_id

    def get_block_number(self) -> int:
        return self.block_number

    def get_transaction_count(self, address: str, block: str = "pending") -> int:
        return self._nonces.get(address.lower(), 0)

    def get_balance(self, address: str, block: str = "latest") -> int:
        return self._balances.get(address.lower(), 10**18)  # Default 1 ETH

    def get_code(self, address: str, block: str = "latest") -> str:
        clean = address.lower()
        if clean in self._deployed_codes:
            return self._deployed_codes[clean]
        if clean == "0x0000000000000000000000000000000000000000":
            return "0x"
        # Standard mock contract bytecode
        return "0x608060405234801561001057600080fd5b50"

    def send_raw_transaction(self, raw_tx_bytes: bytes) -> str:
        # Compute tx hash deterministically via EVM Keccak-256
        try:
            raw_h = Web3.keccak(raw_tx_bytes).hex()
        except Exception:
            raw_h = hashlib.sha256(raw_tx_bytes).hexdigest()
        tx_hash = "0x" + raw_h if not raw_h.startswith("0x") else raw_h

        if self.timeout_all or (tx_hash.lower() in self._timeout_hashes):
            raise httpx.ReadTimeout("Simulated upstream transport timeout during eth_sendRawTransaction")

        if tx_hash.lower() in self._reject_submission_hashes:
            raise RuntimeError(f"Simulated RPC rejection: execution reverted")

        self.block_number += 1
        block_hash = "0x" + hashlib.sha256(f"block-{self.block_number}".encode()).hexdigest()

        # Record transaction
        self._transactions[tx_hash.lower()] = {
            "hash": tx_hash,
            "blockNumber": hex(self.block_number),
            "blockHash": block_hash,
            "raw": "0x" + raw_tx_bytes.hex(),
        }

        # Determine receipt outcome
        is_revert = self.revert_all or (tx_hash.lower() in self._revert_hashes)
        is_pending = self.pending_all or (tx_hash.lower() in self._pending_hashes)

        if not is_pending:
            self._receipts[tx_hash.lower()] = {
                "transactionHash": tx_hash,
                "blockNumber": hex(self.block_number),
                "blockHash": block_hash,
                "status": "0x0" if is_revert else "0x1",
                "gasUsed": hex(21000),
                "effectiveGasPrice": hex(25_000_000_000),
            }

        return tx_hash.lower()

    def get_transaction_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        clean = tx_hash.lower()
        if self.timeout_all or (clean in self._timeout_hashes):
            raise httpx.ReadTimeout("Simulated upstream transport timeout during eth_getTransactionReceipt")
        if self.pending_all or (clean in self._pending_hashes):
            return None
        if clean in self._receipts:
            return self._receipts[clean]
        if self.revert_all or (clean in self._revert_hashes):
            return {
                "transactionHash": tx_hash,
                "blockNumber": hex(self.block_number),
                "blockHash": "0x" + hashlib.sha256(f"block-{self.block_number}".encode()).hexdigest(),
                "status": "0x0",
                "gasUsed": hex(21000),
                "effectiveGasPrice": hex(25_000_000_000),
            }
        return None

    def get_transaction_by_hash(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        return self._transactions.get(tx_hash.lower())

    def confirm_pending_transaction(self, tx_hash: str, status: int = 1) -> None:
        clean = tx_hash.lower()
        self._pending_hashes.discard(clean)
        self.block_number += 1
        block_hash = "0x" + hashlib.sha256(f"block-{self.block_number}".encode()).hexdigest()
        self._receipts[clean] = {
            "transactionHash": clean,
            "blockNumber": hex(self.block_number),
            "blockHash": block_hash,
            "status": "0x1" if status == 1 else "0x0",
            "gasUsed": hex(21000),
            "effectiveGasPrice": hex(25_000_000_000),
        }
