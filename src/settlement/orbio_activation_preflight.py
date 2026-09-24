"""Phase 20B — Read-only preflight for CREDIT.activate (no broadcast).

All critical RPC views are hard-fail. Never sends a transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol

from eth_abi import encode as eth_abi_encode
from eth_hash.auto import keccak

from src.domain.orbio_activation import (
    ORBIO_ACTIVATION_CHAIN_ID,
    ORBIO_CREDIT_ACTIVATION_CONTRACT,
    OrbioCreditActivationIntent,
)


class ReadOnlyRpc(Protocol):
    def get_chain_id(self) -> int: ...
    def get_code(self, address: str) -> str: ...
    def eth_call(self, to: str, data: str, from_address: Optional[str] = None) -> str: ...
    def get_transaction_count(self, address: str, block: str = "pending") -> int: ...
    def estimate_gas(self, tx: Dict[str, Any]) -> int: ...
    def get_balance(self, address: str) -> int: ...


_BALANCE_OF_SELECTOR = "0x70a08231"
_PREVIEW_SELECTOR = keccak(b"previewActivation(uint256)")[:4]
_FEE_BPS_SELECTOR = keccak(b"activationFeeBps()")[:4]


def _encode_balance_of(owner: str) -> str:
    owner_clean = owner.lower().replace("0x", "").zfill(64)
    return _BALANCE_OF_SELECTOR + owner_clean


def _encode_preview(amount: int) -> str:
    return "0x" + (_PREVIEW_SELECTOR + eth_abi_encode(["uint256"], [amount])).hex()


def _encode_fee_bps() -> str:
    return "0x" + _FEE_BPS_SELECTOR.hex()


def _parse_hex_word(data: str, index: int = 0) -> Optional[int]:
    """Parse the index-th 32-byte word from an eth_call hex response."""
    if not data or data in ("0x", "0x0"):
        return None
    hex_body = data[2:] if data.startswith("0x") else data
    if len(hex_body) < (index + 1) * 64:
        return None
    chunk = hex_body[index * 64 : (index + 1) * 64]
    try:
        return int(chunk, 16)
    except ValueError:
        return None


@dataclass
class ActivationPreflightResult:
    ok: bool
    requested_amount: int = 0
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
            "requested_amount": self.requested_amount,
            "preview_credited": self.preview_credited,
            "preview_fee_atoms": self.preview_fee_atoms,
            "activation_fee_bps": self.activation_fee_bps,
            "chain_id": self.chain_id,
            "contract_has_code": self.contract_has_code,
            "operator_credit_balance": self.operator_credit_balance,
            "nonce": self.nonce,
            "gas_estimate": self.gas_estimate,
            "operator_eth_balance": self.operator_eth_balance,
            "calldata": self.calldata,
            "errors": list(self.errors),
            "broadcast": False,
            # Explicit: this is a preview only; API balance is not claimed increased.
            "api_balance_confirmed": False,
        }


class OrbioActivationPreflight:
    """Read-only checks before any authorization/execution of activation.

    Every critical view is hard-fail. previewActivation conservation:
    credited + feeAtoms == requested amount.
    """

    def __init__(self, rpc: ReadOnlyRpc, operator_address: str):
        self.rpc = rpc
        self.operator_address = operator_address.lower()

    def run(self, intent: OrbioCreditActivationIntent) -> ActivationPreflightResult:
        errors: list = []
        result = ActivationPreflightResult(
            ok=False,
            requested_amount=intent.amount,
            calldata=intent.encode_calldata(),
        )

        # 1. chain id
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

        # 2. bytecode
        try:
            code = self.rpc.get_code(contract) or "0x"
            result.contract_has_code = code not in ("0x", "0x0", "")
            if not result.contract_has_code:
                errors.append("CREDIT contract has no bytecode on this RPC")
        except Exception as exc:
            errors.append(f"eth_getCode failed: {exc}")

        # 3. balanceOf
        try:
            bal_hex = self.rpc.eth_call(contract, _encode_balance_of(self.operator_address))
            bal = _parse_hex_word(bal_hex, 0)
            if bal is None:
                errors.append("balanceOf returned empty or malformed response")
            else:
                result.operator_credit_balance = bal
                if bal < intent.amount:
                    errors.append(
                        f"insufficient CREDIT: balance={bal}, required={intent.amount}"
                    )
        except Exception as exc:
            errors.append(f"balanceOf failed: {exc}")

        # 4. previewActivation — hard fail + conservation
        try:
            preview_hex = self.rpc.eth_call(contract, _encode_preview(intent.amount))
            credited = _parse_hex_word(preview_hex, 0)
            fee_atoms = _parse_hex_word(preview_hex, 1)
            if credited is None or fee_atoms is None:
                errors.append("previewActivation returned empty or malformed response")
            else:
                result.preview_credited = credited
                result.preview_fee_atoms = fee_atoms
                if fee_atoms > intent.amount:
                    errors.append(
                        f"preview feeAtoms {fee_atoms} exceeds requested amount {intent.amount}"
                    )
                if credited + fee_atoms != intent.amount:
                    errors.append(
                        f"preview conservation failed: credited({credited}) + feeAtoms({fee_atoms}) "
                        f"!= requested({intent.amount})"
                    )
        except Exception as exc:
            errors.append(f"previewActivation failed: {exc}")

        # 5. activationFeeBps — hard fail
        try:
            fee_hex = self.rpc.eth_call(contract, _encode_fee_bps())
            bps = _parse_hex_word(fee_hex, 0)
            if bps is None:
                errors.append("activationFeeBps returned empty or malformed response")
            else:
                result.activation_fee_bps = bps
        except Exception as exc:
            errors.append(f"activationFeeBps failed: {exc}")

        # 6. nonce
        try:
            result.nonce = self.rpc.get_transaction_count(self.operator_address, "pending")
        except Exception as exc:
            errors.append(f"nonce failed: {exc}")

        # 7. native balance
        try:
            eth_bal = self.rpc.get_balance(self.operator_address)
            result.operator_eth_balance = eth_bal
            if eth_bal is None:
                errors.append("get_balance returned None")
            elif eth_bal == 0:
                errors.append("operator ETH balance is zero; cannot pay gas")
        except Exception as exc:
            errors.append(f"get_balance failed: {exc}")

        # 8. gas estimate
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
