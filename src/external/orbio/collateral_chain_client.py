"""Minimal web3.py client for the Phase 18 CREDIT collateral vault.

The live path has two explicit signing roles. The pledger key owns CREDIT and
signs approve()+lock(); the vault owner key signs release()/forfeit(). This
prevents the backend settlement signer from pretending to be the pledging
organization.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from web3 import Web3
from web3.middleware import geth_poa_middleware


class CollateralVaultChainError(Exception):
    def __init__(self, message: str, *, tx_hash: Optional[str] = None):
        super().__init__(message)
        self.tx_hash = tx_hash


_VAULT_ABI = json.loads("""
[
  {"inputs":[
    {"internalType":"bytes32","name":"positionId","type":"bytes32"},
    {"internalType":"uint256","name":"amount","type":"uint256"},
    {"internalType":"address","name":"beneficiary","type":"address"}],
   "name":"lock","outputs":[],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"internalType":"bytes32","name":"positionId","type":"bytes32"}],
   "name":"release","outputs":[],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"internalType":"bytes32","name":"positionId","type":"bytes32"}],
   "name":"forfeit","outputs":[],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"internalType":"bytes32","name":"positionId","type":"bytes32"}],
   "name":"getPosition",
   "outputs":[
     {"internalType":"address","name":"pledger","type":"address"},
     {"internalType":"address","name":"beneficiary","type":"address"},
     {"internalType":"uint256","name":"amount","type":"uint256"},
     {"internalType":"uint8","name":"state","type":"uint8"}],
   "stateMutability":"view","type":"function"}
]
""")

_ERC20_ABI = json.loads("""
[
  {"inputs":[
    {"internalType":"address","name":"spender","type":"address"},
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
    def __init__(
        self,
        *,
        rpc_url: str,
        vault_address: str,
        credit_token_address: str,
        owner_private_key: str,
        pledger_private_key: str,
    ):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        if not self.w3.is_connected():
            raise CollateralVaultChainError(f"could not connect to configured collateral RPC")

        self.owner_account = self.w3.eth.account.from_key(owner_private_key)
        self.pledger_account = self.w3.eth.account.from_key(pledger_private_key)
        self.vault = self.w3.eth.contract(
            address=Web3.to_checksum_address(vault_address), abi=_VAULT_ABI
        )
        self.credit = self.w3.eth.contract(
            address=Web3.to_checksum_address(credit_token_address), abi=_ERC20_ABI
        )

    def _send(self, fn, *, private_key: str, from_address: str) -> str:
        tx = fn.build_transaction({
            "from": from_address,
            "nonce": self.w3.eth.get_transaction_count(from_address, "pending"),
            "chainId": self.w3.eth.chain_id,
        })
        signed = self.w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.status != 1:
            raise CollateralVaultChainError(
                f"transaction reverted: {tx_hash.hex()}", tx_hash=tx_hash.hex()
            )
        return tx_hash.hex()

    def approve_and_lock(
        self,
        *,
        position_id_bytes32: bytes,
        amount_atoms: int,
        beneficiary_address: str,
        expected_pledger_address: str,
    ) -> str:
        pledger = self.pledger_account.address
        if Web3.to_checksum_address(expected_pledger_address) != pledger:
            raise CollateralVaultChainError(
                "configured pledger signer does not match the pledger wallet bound to the obligation"
            )
        beneficiary = Web3.to_checksum_address(beneficiary_address)

        self._send(
            self.credit.functions.approve(self.vault.address, amount_atoms),
            private_key=self.pledger_account.key.hex(),
            from_address=pledger,
        )
        return self._send(
            self.vault.functions.lock(position_id_bytes32, amount_atoms, beneficiary),
            private_key=self.pledger_account.key.hex(),
            from_address=pledger,
        )

    def release(self, *, position_id_bytes32: bytes) -> str:
        return self._send(
            self.vault.functions.release(position_id_bytes32),
            private_key=self.owner_account.key.hex(),
            from_address=self.owner_account.address,
        )

    def forfeit(self, *, position_id_bytes32: bytes) -> str:
        return self._send(
            self.vault.functions.forfeit(position_id_bytes32),
            private_key=self.owner_account.key.hex(),
            from_address=self.owner_account.address,
        )

    def get_position(self, *, position_id_bytes32: bytes) -> Dict[str, Any]:
        pledger, beneficiary, amount, state = self.vault.functions.getPosition(position_id_bytes32).call()
        return {
            "pledger": pledger,
            "beneficiary": beneficiary,
            "amount": amount,
            "state": _POSITION_STATE_NAMES[state] if state < len(_POSITION_STATE_NAMES) else "UNKNOWN",
        }


def position_id_to_bytes32(position_id: str) -> bytes:
    return Web3.keccak(text=position_id)
