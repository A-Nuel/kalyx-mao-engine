"""Isolated cryptographic signing boundary for blockchain execution.

Architectural Guarantees:
- Private keys NEVER leave this module.
- Neither LLM agents, nor FastAPI routes, nor browser clients can access signing keys.
- Key material is strictly excluded from logs, error messages, and object representations.
"""

from __future__ import annotations

import re
from typing import Protocol, Tuple
from eth_account import Account
from web3 import Web3

from src.domain.blockchain import BlockchainTransactionIntent


class IBlockchainSigner(Protocol):
    """Protocol for blockchain transaction signers."""

    @property
    def address(self) -> str:
        """The public Ethereum address of the signer."""
        ...

    def sign_transaction(self, intent: BlockchainTransactionIntent, nonce: int) -> Tuple[bytes, str]:
        """Sign a transaction intent, returning raw signed bytes and the transaction hash."""
        ...


class LocalKeySigner:
    """Isolated local EVM signer using eth-account.
    
    Operates strictly at the consequential execution boundary.
    """

    def __init__(self, private_key: str):
        cleaned_key = private_key.strip()
        if not cleaned_key.startswith("0x"):
            cleaned_key = "0x" + cleaned_key
        if len(cleaned_key) != 66 or not re.match(r"^0x[0-9a-fA-F]{64}$", cleaned_key):
            raise ValueError("Invalid private key: must be a 32-byte hex string (64 hex characters)")

        self._private_key = cleaned_key
        account = Account.from_key(self._private_key)
        self._address = Web3.to_checksum_address(account.address)

    @property
    def address(self) -> str:
        return self._address

    def sign_transaction(self, intent: BlockchainTransactionIntent, nonce: int) -> Tuple[bytes, str]:
        """Constructs an EIP-1559 transaction from the authorized intent and signs it."""
        to_addr = Web3.to_checksum_address(intent.recipient)
        calldata = (
            bytes.fromhex(intent.data_payload[2:])
            if intent.data_payload.startswith("0x")
            else bytes.fromhex(intent.data_payload)
        )

        tx_dict = {
            "type": 2,
            "chainId": intent.chain_id,
            "nonce": nonce,
            "maxFeePerGas": intent.max_fee_per_gas,
            "maxPriorityFeePerGas": intent.max_priority_fee_per_gas,
            "gas": intent.gas_limit,
            "to": to_addr,
            "value": intent.amount_wei,
            "data": calldata,
        }

        signed_tx = Account.sign_transaction(tx_dict, self._private_key)
        tx_hash = signed_tx.hash.hex()
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        return bytes(signed_tx.raw_transaction), tx_hash.lower()

    def __repr__(self) -> str:
        return f"<LocalKeySigner address={self._address} key=***REDACTED***>"

    def __str__(self) -> str:
        return self.__repr__()
