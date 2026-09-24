"""Phase 20B — Read-only preflight for CREDIT.activate (no broadcast).

Performs safe RPC views and records audit evidence. Never sends a transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol

from src.domain.orbio_activation import (
    ORBIO_ACTIVATION_CHAIN_ID,
    ORBIO_CREDIT_ACTIVATION_CONTRACT,
    OrbioCreditActivationIntent,
    encode_activate_calldata,
)


class ReadOnlyRpc(Protocol):
    def get_chain_id(self) -> int: ...
    def get_code(self, address: str) -> str: ...
    def eth_call(self, to: str, data: str, from_address: Optional[str] = None) -> str: ...
    def get_transaction_count(self, address: str, block: str = "pending") -> int: ...
    def estimate_gas(self, tx: Dict[str, Any]) -> int: ...
    def get_balance(self, address: str) -> int: ...


# ERC-20 balanceOf(address) selector
_BALANCE_OF_SELECTOR = "0x70a08231"
# previewActivation(uint256) — from official ABI name; selector derived at runtime if needed
_PREVIEW_SELECTOR = None  # computed lazily
_FEE_BPS_SELECTOR = None


def _sel(sig: str) -> str:
    from eth_hash.auto import keccak
    return "0x" + keccak(sig.encode()).hex()[:8]


def _encode_balance_of(owner: str) -> str:
    from eth_abi import encode
    owner_clean = owner.lower().replace("0x", "").zfill(64)
    return _BALANCE_OF_SELECTOR + owner_clean


def _encode_preview(amount: int) -> str:
    from eth_abi import encode
    from eth_hash.auto import keccak
    sel = keccak(b"previewActivation(uint256)")[:4]
    return "0x" + (sel + encode(["uint256"], [amount])).hex()


def _encode_fee_bps() -> str:
    from eth_hash.auto import keccak
    return "0x" + keccak(b"activationFeeBps()")[:4].hex()


@dataclass
class ActivationPreflightResult:
    ok: bool
    chain_id: Optional[int] = None
    contract_has_code: bool = False
    operator_credit_balance: Optional[int] = None
    preview_credited: Optional[int] = None
    preview_fee_atoms: Optional[int] = None
    activation_fee_bps: Optional[int] = None
    nonce: Optional[int] = None
    gas_estimate: Optional[int] = None
    operator_eth_balance: Optional[int] = None
    calldata: str = ""
    errors: list = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "chain_id": self.chain_id,
            "contract_has_code": self.contract_has_code,
            "operator_credit_balance": self.operator_credit_balance,
            "preview_credited": self.preview_credited,
            "preview_fee_atoms": self.preview_fee_atoms,
            "activation_fee_bps": self.activation_fee_bps,
            "nonce": self.nonce,
            "gas_estimate": self.gas_estimate,
            "operator_eth_balance": self.operator_eth_balance,
            "calldata": self.calldata,
            "errors": list(self.errors),
            "broadcast": False,
        }


class OrbioActivationPreflight:
    """Read-only checks before any authorization/execution of activation."""

    def __init__(self, rpc: ReadOnlyRpc, operator_address: str):
        self.rpc = rpc
        self.operator_address = operator_address.lower()

    def run(self, intent: OrbioCreditActivationIntent) -> ActivationPreflightResult:
        errors: list = []
        result = ActivationPreflightResult(ok=False, calldata=intent.encode_calldata())

        try:
            chain_id = int(self.rpc.get_chain_id())
            result.chain_id = chain_id
            if chain_id != ORBIO_ACTIVATION_CHAIN_ID:
                errors.append(
                    f"chain_id mismatch: rpc={chain_id}, required={ORBIO_ACTIVATION_CHAIN_ID}"
                )
        except Exception as exc:
            errors.append(f"eth_chainId failed: {exc}")

        contract = intent.credit_contract.lower()
        if contract != ORBIO_CREDIT_ACTIVATION_CONTRACT:
            errors.append(f"credit_contract not allowlisted: {contract}")

        try:
            code = self.rpc.get_code(contract) or "0x"
            result.contract_has_code = code not in ("0x", "0x0", "")
            if not result.contract_has_code:
                errors.append("CREDIT contract has no bytecode on this RPC")
        except Exception as exc:
            errors.append(f"eth_getCode failed: {exc}")

        try:
            bal_hex = self.rpc.eth_call(contract, _encode_balance_of(self.operator_address))
            bal = int(bal_hex, 16) if bal_hex else 0
            result.operator_credit_balance = bal
            if bal < intent.amount:
                errors.append(
                    f"insufficient CREDIT: balance={bal}, required={intent.amount}"
                )
        except Exception as exc:
            errors.append(f"balanceOf failed: {exc}")

        try:
            preview_hex = self.rpc.eth_call(contract, _encode_preview(intent.amount))
            if preview_hex and preview_hex != "0x":
                # returns (uint256 credited, uint256 feeAtoms)
                raw = bytes.fromhex(preview_hex[2:].zfill(128))
                result.preview_credited = int.from_bytes(raw[0:32], "big")
                result.preview_fee_atoms = int.from_bytes(raw[32:64], "big")
        except Exception as exc:
            # preview is optional if node/call fails; record but do not hard-fail alone
            result.raw["preview_error"] = str(exc)

        try:
            fee_hex = self.rpc.eth_call(contract, _encode_fee_bps())
            if fee_hex and fee_hex != "0x":
                result.activation_fee_bps = int(fee_hex, 16)
        except Exception as exc:
            result.raw["fee_bps_error"] = str(exc)

        try:
            result.nonce = self.rpc.get_transaction_count(self.operator_address, "pending")
        except Exception as exc:
            errors.append(f"nonce failed: {exc}")

        try:
            result.operator_eth_balance = self.rpc.get_balance(self.operator_address)
            if result.operator_eth_balance is not None and result.operator_eth_balance == 0:
                errors.append("operator ETH balance is zero; cannot pay gas")
        except Exception as exc:
            errors.append(f"get_balance failed: {exc}")

        try:
            gas = self.rpc.estimate_gas(
                {
                    "from": self.operator_address,
                    "to": contract,
                    "data": result.calldata,
                    "value": "0x0",
                }
            )
            result.gas_estimate = int(gas)
            if result.gas_estimate > intent.gas_limit:
                errors.append(
                    f"gas estimate {result.gas_estimate} exceeds intent gas_limit {intent.gas_limit}"
                )
        except Exception as exc:
            errors.append(f"estimate_gas failed: {exc}")

        result.errors = errors
        result.ok = len(errors) == 0
        result.raw["broadcast"] = False
        return result
