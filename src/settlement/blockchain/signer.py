"""Isolated cryptographic signing boundary for blockchain execution.

Architectural Guarantees:
- Private keys NEVER leave this module.
- Neither LLM agents, nor FastAPI routes, nor browser clients can access signing keys.
- Key material is strictly excluded from logs, error messages, and object representations.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional, Protocol, Tuple
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


class ExternalTransactionSigner:
    """External/delegated EVM signer adhering to IBlockchainSigner.

    Used when private keys are held in hardware wallets, mobile wallets
    (e.g., Robinhood Wallet), or external KMS where the private key cannot
    and should not be exported to Kalyx.

    Kalyx retains full authority over:
      - Typed transaction intent & calldata validation
      - Governance policy & cryptographic human confirmation tokens
      - Live preflight and on-chain nonce verification

    The ExternalTransactionSigner accepts externally provided signed transaction
    bytes, cryptographically verifies that the recovered signer matches the
    authorized operator address, and returns (raw_tx_bytes, tx_hash) to
    BlockchainSettlementProvider for broadcast.
    """

    def __init__(
        self,
        address: str,
        signed_raw_tx_hex: Optional[str] = None,
        signing_prompt_callback: Optional[Callable[[Dict[str, Any]], str]] = None,
    ):
        clean_addr = address.strip()
        if not re.match(r"^0x[0-9a-fA-F]{40}$", clean_addr):
            raise ValueError(f"Invalid Ethereum address: {address}")
        self._address = Web3.to_checksum_address(clean_addr)
        self._signed_raw_tx_hex = signed_raw_tx_hex.strip() if signed_raw_tx_hex else None
        self._prompt_callback = signing_prompt_callback

    @property
    def address(self) -> str:
        return self._address

    def set_signed_raw_tx_hex(self, signed_raw_tx_hex: str) -> None:
        """Set or update the signed raw transaction hex."""
        self._signed_raw_tx_hex = signed_raw_tx_hex.strip()

    def build_transaction_payload(
        self, intent: Any, nonce: int
    ) -> Dict[str, Any]:
        """Builds the canonical EIP-1559 transaction dict for external signing."""
        recipient = getattr(intent, "recipient", None) or getattr(intent, "credit_contract", None)
        if not recipient:
            raise ValueError("Intent missing required recipient / credit_contract address")
        to_addr = Web3.to_checksum_address(recipient)

        if hasattr(intent, "encode_calldata") and callable(intent.encode_calldata):
            calldata = intent.encode_calldata()
        else:
            raw_data = getattr(intent, "data_payload", "0x")
            calldata = raw_data if raw_data.startswith("0x") else "0x" + raw_data

        amount_wei = getattr(intent, "amount_wei", 0)
        max_fee = getattr(intent, "max_fee_per_gas", 25_000_000_000)
        max_priority_fee = getattr(intent, "max_priority_fee_per_gas", 1_500_000_000)
        gas = getattr(intent, "gas_limit", 150_000)

        return {
            "type": 2,
            "chainId": intent.chain_id,
            "nonce": nonce,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": max_priority_fee,
            "gas": gas,
            "to": to_addr,
            "value": amount_wei,
            "data": calldata,
        }

    def sign_transaction(
        self, intent: BlockchainTransactionIntent, nonce: int
    ) -> Tuple[bytes, str]:
        """Provides the cryptographic signature for the authorized transaction intent.

        Validates that:
        1. A signed raw transaction is provided.
        2. Cryptographically recovered sender matches self.address exactly.
        """
        raw_hex = self._signed_raw_tx_hex
        if not raw_hex and self._prompt_callback:
            tx_payload = self.build_transaction_payload(intent, nonce)
            raw_hex = self._prompt_callback(tx_payload)

        if not raw_hex:
            tx_payload = self.build_transaction_payload(intent, nonce)
            raise ValueError(
                f"External signature required for operator {self.address}. "
                f"Transaction payload: {tx_payload}"
            )

        clean_hex = raw_hex.strip()
        if clean_hex.startswith("0x"):
            raw_bytes = bytes.fromhex(clean_hex[2:])
        else:
            raw_bytes = bytes.fromhex(clean_hex)

        # Cryptographically recover signer address from signed raw transaction
        try:
            recovered_address = Account.recover_transaction(raw_bytes)
        except Exception as exc:
            raise ValueError(
                f"Failed to recover signer from provided raw transaction: {exc}"
            ) from exc

        if recovered_address.lower() != self.address.lower():
            raise ValueError(
                f"Signer address mismatch: transaction was signed by {recovered_address}, "
                f"expected authorized operator {self.address}"
            )

        # Compute transaction hash via Keccak-256
        tx_hash = Web3.keccak(raw_bytes).hex()
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash

        return raw_bytes, tx_hash.lower()

    def __repr__(self) -> str:
        return f"<ExternalTransactionSigner address={self._address}>"

    def __str__(self) -> str:
        return self.__repr__()

