"""Minimal web3.py client for CollateralVault — LIVE mode only.

Deliberately thin: no retry logic, no gas estimation heuristics beyond
web3.py defaults. This is a hackathon-timeline client for a small number of
demo transactions, not production infrastructure. See contracts/README.md
"Known limitations" for what production hardening would add.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from web3 import Web3
from web3.middleware import geth_poa_middleware  # Arbitrum-Orbit chains need this


class CollateralVaultChainError(Exception):
    def __init__(self, message: str, *, tx_hash: Optional[str] = None):
        super().__init__(message)
        self.tx_hash = tx_hash


# Minimal ABI subset — just the functions/events this client calls.
# Matches contracts/CollateralVault.sol exactly; keep in sync if the
# contract changes.
_VAULT_ABI = json.loads("""
[
  {"inputs":[{"internalType":"bytes32","name":"positionId","type":"bytes32"},
             {"internalType":"uint256","name":"amount","type":"uint256"}],
   "name":"lock","outputs":[],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"internalType":"bytes32","name":"positionId","type":"bytes32"}],
   "name":"release","outputs":[],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"internalType":"bytes32","name":"positionId","type":"bytes32"},
             {"internalType":"address","name":"beneficiary","type":"address"}],
   "name":"forfeit","outputs":[],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"internalType":"bytes32","name":"positionId","type":"bytes32"}],
   "name":"getPosition",
   "outputs":[{"internalType":"address","name":"pledger","type":"address"},
              {"internalType":"uint256","name":"amount","type":"uint256"},
              {"internalType":"uint8","name":"state","type":"uint8"}],
   "stateMutability":"view","type":"function"}
]
""")

_ERC20_ABI = json.loads("""
[
  {"inputs":[{"internalType":"address","name":"spender","type":"address"},
             {"internalType":"uint256","name":"amount","type":"uint256"}],
   "name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],
   "stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"internalType":"address","name":"account","type":"address"}],
   "name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],
   "stateMutability":"view","type":"function"}
]
""")

_POSITION_STATE_NAMES = ("NONE", "LOCKED", "RELEASED", "FORFEITED")


class CollateralVaultClient:
    """Talks to a real deployed CollateralVault contract. Every call in this
    class sends or reads an actual on-chain transaction — there is no
    simulated branch here (that lives in the adapter, one layer up, exactly
    like OrbioAdapter separates SimulatedOrbioProvider from the real
    OrbioGatewayClient)."""

    def __init__(self, *, rpc_url: str, vault_address: str, credit_token_address: str, private_key: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        # Robinhood Chain is an Arbitrum Orbit chain; PoA middleware handles
        # the extraData field shape some Orbit/L2 clients still emit.
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        if not self.w3.is_connected():
            raise CollateralVaultChainError(f"could not connect to RPC {rpc_url}")

        self.account = self.w3.eth.account.from_key(private_key)
        self.vault = self.w3.eth.contract(address=Web3.to_checksum_address(vault_address), abi=_VAULT_ABI)
        self.credit = self.w3.eth.contract(
            address=Web3.to_checksum_address(credit_token_address), abi=_ERC20_ABI
        )

    def _send(self, fn) -> str:
        tx = fn.build_transaction(
            {
                "from": self.account.address,
                "nonce": self.w3.eth.get_transaction_count(self.account.address),
            }
        )
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.status != 1:
            raise CollateralVaultChainError(
                f"transaction reverted: {tx_hash.hex()}", tx_hash=tx_hash.hex()
            )
        return tx_hash.hex()

    def approve(self, *, amount_atoms: int) -> str:
        """Must be called before lock(); approves the vault to pull `amount_atoms`."""
        return self._send(self.credit.functions.approve(self.vault.address, amount_atoms))

    def lock(self, *, position_id_bytes32: bytes, amount_atoms: int) -> str:
        return self._send(self.vault.functions.lock(position_id_bytes32, amount_atoms))

    def release(self, *, position_id_bytes32: bytes) -> str:
        return self._send(self.vault.functions.release(position_id_bytes32))

    def forfeit(self, *, position_id_bytes32: bytes, beneficiary_address: str) -> str:
        return self._send(
            self.vault.functions.forfeit(position_id_bytes32, Web3.to_checksum_address(beneficiary_address))
        )

    def get_position(self, *, position_id_bytes32: bytes) -> Dict[str, Any]:
        pledger, amount, state = self.vault.functions.getPosition(position_id_bytes32).call()
        return {
            "pledger": pledger,
            "amount": amount,
            "state": _POSITION_STATE_NAMES[state] if state < len(_POSITION_STATE_NAMES) else "UNKNOWN",
        }


def position_id_to_bytes32(position_id: str) -> bytes:
    """Deterministic keccak256 of the position_id string, matching
    `cast keccak "position-id-string"` used in the manual runbook, so a
    position created by Kalyx and one poked at manually via `cast` resolve
    to the identical on-chain key."""
    return Web3.keccak(text=position_id)
