"""Domain models for blockchain transaction intents and on-chain receipt evidence.

Enforces strict typed representation of blockchain settlement intents.
The exact execution payload is deterministically bound to the approved intent,
ensuring an LLM or client cannot execute a different transaction than what was
authorized by policy.

Phase 14B adds OrbioPurchaseIntent: a narrow, typed purchase specialization
that reconstructs Exchange.buyAndActivate calldata from authorized fields only.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Dict, Optional

from eth_abi import encode as eth_abi_encode
from eth_utils import keccak
from pydantic import BaseModel, Field, field_validator, model_validator

from src.domain.events import canonical_json

ETH_ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
ETH_TX_HASH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")
BYTES32_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")

# Official Orbio addresses on Robinhood Chain mainnet (chain 4663).
# Testnet addresses must be verified independently before use; do not assume parity.
ORBIO_EXCHANGE_MAINNET = "0x6951ffd32630b05e06f50062aea801625a58ebc0"
ORBIO_CREDIT_MAINNET = "0xe33322da1380e61e5ae5dfb21e7f62924c73004c"
USDG_MAINNET = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"

BUY_AND_ACTIVATE_SIGNATURE = "buyAndActivate(uint256,uint256,bytes32,uint256)"
BUY_AND_ACTIVATE_SELECTOR = keccak(text=BUY_AND_ACTIVATE_SIGNATURE)[:4]


def address_to_beneficiary_bytes32(address: str) -> str:
    """Encode an EVM address as the bytes32 beneficiary expected by buyAndActivate."""
    clean = address.strip().lower()
    if not ETH_ADDRESS_PATTERN.match(clean):
        raise ValueError(f"Invalid beneficiary address: '{address}'")
    # bytes32(uint256(uint160(recipient)))
    return "0x" + clean[2:].zfill(64)


def encode_buy_and_activate_calldata(
    usdg_in: int,
    min_credit_out: int,
    beneficiary: str,
    max_fills: int,
) -> str:
    """Deterministically encode Exchange.buyAndActivate calldata.

    Agents never supply raw calldata. Encoding is performed only from
    policy-authorized typed parameters.
    """
    if usdg_in < 0 or min_credit_out < 0 or max_fills < 0:
        raise ValueError("usdg_in, min_credit_out, and max_fills must be non-negative")

    beneficiary_clean = beneficiary.strip().lower()
    if ETH_ADDRESS_PATTERN.match(beneficiary_clean):
        beneficiary_b32 = bytes.fromhex(address_to_beneficiary_bytes32(beneficiary_clean)[2:])
    elif BYTES32_PATTERN.match(beneficiary_clean):
        beneficiary_b32 = bytes.fromhex(beneficiary_clean[2:])
    else:
        raise ValueError(
            f"beneficiary must be a 0x address or 0x bytes32; got '{beneficiary}'"
        )

    encoded_args = eth_abi_encode(
        ["uint256", "uint256", "bytes32", "uint256"],
        [usdg_in, min_credit_out, beneficiary_b32, max_fills],
    )
    return "0x" + (BUY_AND_ACTIVATE_SELECTOR + encoded_args).hex()


class BlockchainTransactionIntent(BaseModel):
    """Typed, immutable specification of an intended on-chain transaction.

    Cryptographically bound to the organisation, mission, proposal, and authorization token.
    """

    tenant_id: str
    organisation_id: str
    mission_id: Optional[str] = None
    operation_id: str
    chain_id: int = Field(default=11155111, description="EVM Chain ID (default Sepolia: 11155111)")
    network: str = Field(default="sepolia", description="Human-readable network name")
    recipient: str = Field(description="Target 0x hex EVM address")
    amount_wei: int = Field(ge=0, default=0, description="Value in wei to transfer")
    amount_credits: int = Field(ge=0, default=0, description="Equivalent credits locked in Kalyx escrow")
    asset: str = Field(default="ETH", description="Native asset or token symbol")
    token_contract: Optional[str] = Field(default=None, description="ERC-20 contract address if applicable")
    transaction_type: int = Field(default=2, description="EIP-2718 transaction type (2 = EIP-1559)")
    max_fee_per_gas: int = Field(default=25_000_000_000, description="Max fee per gas in wei (default 25 gwei)")
    max_priority_fee_per_gas: int = Field(default=1_500_000_000, description="Priority fee in wei (default 1.5 gwei)")
    gas_limit: int = Field(default=21_000, ge=21_000, description="Gas limit for transfer")
    data_payload: str = Field(default="0x", description="Hex calldata")
    idempotency_key: str
    policy_decision_id: str
    authorization_token_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, v: str) -> str:
        v_clean = v.strip()
        if not ETH_ADDRESS_PATTERN.match(v_clean):
            raise ValueError(f"Invalid EVM recipient address: '{v}'")
        return v_clean.lower()

    @field_validator("token_contract")
    @classmethod
    def validate_token_contract(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_clean = v.strip()
            if not ETH_ADDRESS_PATTERN.match(v_clean):
                raise ValueError(f"Invalid token contract address: '{v}'")
            return v_clean.lower()
        return None

    def compute_intent_hash(self) -> str:
        """Compute the deterministic SHA-256 fingerprint of the authorized intent.

        Any alteration of recipient, amount, gas, or scope produces a completely
        different hash, preventing parameter substitution.
        """
        data = {
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "mission_id": self.mission_id,
            "operation_id": self.operation_id,
            "chain_id": self.chain_id,
            "network": self.network,
            "recipient": self.recipient,
            "amount_wei": self.amount_wei,
            "amount_credits": self.amount_credits,
            "asset": self.asset,
            "token_contract": self.token_contract,
            "transaction_type": self.transaction_type,
            "max_fee_per_gas": self.max_fee_per_gas,
            "max_priority_fee_per_gas": self.max_priority_fee_per_gas,
            "gas_limit": self.gas_limit,
            "data_payload": self.data_payload,
            "idempotency_key": self.idempotency_key,
            "policy_decision_id": self.policy_decision_id,
            "authorization_token_hash": self.authorization_token_hash,
        }
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


class OrbioPurchaseIntent(BaseModel):
    """Typed authorization surface for Orbio CREDIT purchase + activation.

    This is a specialization of the Phase 12 settlement boundary, not a parallel
    system. Agents never supply calldata, contract addresses for free choice, or
    beneficiaries outside policy. The signed transaction is reconstructed only
    from these fields via to_blockchain_intent().

    Economic units:
    - usdg_in / min_credit_out use the token native decimals (USDG & CREDIT = 6).
    - amount_credits is the Kalyx internal ORG-credit escrow reservation (separate).
    """

    tenant_id: str
    organisation_id: str
    mission_id: Optional[str] = None
    operation_id: str

    # Network
    chain_id: int = Field(description="Must be Robinhood Chain (4663 mainnet or 46630 testnet)")
    network: str = Field(description="Human-readable network name, e.g. robinhood or robinhood-testnet")

    # Contracts (must be policy-allowlisted; defaults are mainnet official addresses)
    exchange_contract: str = Field(default=ORBIO_EXCHANGE_MAINNET)
    payment_token: str = Field(default=USDG_MAINNET, description="USDG contract")
    credit_token: str = Field(default=ORBIO_CREDIT_MAINNET, description="CREDIT contract (observation)")

    # Purchase parameters (native token units, 6 decimals)
    usdg_in: int = Field(ge=0, description="Maximum USDG to spend (6 decimals)")
    min_credit_out: int = Field(ge=0, description="Minimum CREDIT output (slippage bound, 6 decimals)")
    beneficiary: str = Field(
        description="Activation beneficiary: 0x address or pre-encoded 0x bytes32"
    )
    max_fills: int = Field(ge=1, default=10, description="Max order-book fills")

    # Gas / EIP-1559
    max_fee_per_gas: int = Field(default=25_000_000_000, ge=0)
    max_priority_fee_per_gas: int = Field(default=1_500_000_000, ge=0)
    gas_limit: int = Field(default=350_000, ge=21_000)

    # Governance binding
    amount_credits: int = Field(
        ge=0,
        default=0,
        description="Kalyx ORG Credits reserved in escrow (independent of Orbio CREDIT)",
    )
    idempotency_key: str
    policy_decision_id: str
    authorization_token_hash: str
    deadline: Optional[int] = Field(
        default=None,
        description="Optional unix-second deadline for the authorization window",
    )

    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("exchange_contract", "payment_token", "credit_token")
    @classmethod
    def validate_contract_address(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if not ETH_ADDRESS_PATTERN.match(v_clean):
            raise ValueError(f"Invalid contract address: '{v}'")
        return v_clean

    @field_validator("beneficiary")
    @classmethod
    def validate_beneficiary(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if ETH_ADDRESS_PATTERN.match(v_clean):
            return v_clean
        if BYTES32_PATTERN.match(v_clean):
            return v_clean
        raise ValueError(
            f"beneficiary must be a 0x-prefixed EVM address or bytes32; got '{v}'"
        )

    @model_validator(mode="after")
    def validate_economic_bounds(self) -> "OrbioPurchaseIntent":
        if self.usdg_in == 0 and self.min_credit_out == 0:
            raise ValueError("usdg_in and min_credit_out cannot both be zero")
        if self.min_credit_out > self.usdg_in * 2:
            # Soft sanity: min out should not wildly exceed max spend at face value;
            # real pricing is market-determined. Keep as soft guard only.
            pass
        return self

    def beneficiary_as_bytes32(self) -> str:
        """Return the bytes32 form used in calldata."""
        if BYTES32_PATTERN.match(self.beneficiary):
            return self.beneficiary
        return address_to_beneficiary_bytes32(self.beneficiary)

    def encode_calldata(self) -> str:
        """Deterministic buyAndActivate calldata from authorized fields only."""
        return encode_buy_and_activate_calldata(
            usdg_in=self.usdg_in,
            min_credit_out=self.min_credit_out,
            beneficiary=self.beneficiary,
            max_fills=self.max_fills,
        )

    def compute_purchase_intent_hash(self) -> str:
        """Hash over purchase-specific authorized parameters.

        Distinct from BlockchainTransactionIntent.compute_intent_hash so that
        purchase semantics remain auditable even if the outer intent wrapper changes.
        """
        data = {
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "mission_id": self.mission_id,
            "operation_id": self.operation_id,
            "chain_id": self.chain_id,
            "network": self.network,
            "exchange_contract": self.exchange_contract,
            "payment_token": self.payment_token,
            "credit_token": self.credit_token,
            "usdg_in": self.usdg_in,
            "min_credit_out": self.min_credit_out,
            "beneficiary": self.beneficiary_as_bytes32(),
            "max_fills": self.max_fills,
            "max_fee_per_gas": self.max_fee_per_gas,
            "max_priority_fee_per_gas": self.max_priority_fee_per_gas,
            "gas_limit": self.gas_limit,
            "amount_credits": self.amount_credits,
            "idempotency_key": self.idempotency_key,
            "policy_decision_id": self.policy_decision_id,
            "authorization_token_hash": self.authorization_token_hash,
            "deadline": self.deadline,
            "function": BUY_AND_ACTIVATE_SIGNATURE,
        }
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()

    def to_blockchain_intent(self) -> BlockchainTransactionIntent:
        """Project onto the existing Phase 12 intent used by signer/provider.

        - recipient = Exchange
        - amount_wei = 0 (USDG is ERC-20; approval is a separate authorized step)
        - data_payload = deterministic buyAndActivate calldata
        - token_contract = USDG (payment asset metadata)
        """
        return BlockchainTransactionIntent(
            tenant_id=self.tenant_id,
            organisation_id=self.organisation_id,
            mission_id=self.mission_id,
            operation_id=self.operation_id,
            chain_id=self.chain_id,
            network=self.network,
            recipient=self.exchange_contract,
            amount_wei=0,
            amount_credits=self.amount_credits,
            asset="USDG",
            token_contract=self.payment_token,
            transaction_type=2,
            max_fee_per_gas=self.max_fee_per_gas,
            max_priority_fee_per_gas=self.max_priority_fee_per_gas,
            gas_limit=self.gas_limit,
            data_payload=self.encode_calldata(),
            idempotency_key=self.idempotency_key,
            policy_decision_id=self.policy_decision_id,
            authorization_token_hash=self.authorization_token_hash,
            created_at=self.created_at,
        )


class BlockchainReceiptEvidence(BaseModel):
    """Normalized, provider-neutral proof of on-chain execution and settlement."""

    provider: str = "evm"
    network: str
    chain_id: int
    transaction_hash: str
    status: str = Field(description="submitted | confirmed | reverted | pending | unknown")
    block_number: Optional[int] = None
    block_hash: Optional[str] = None
    gas_used: Optional[int] = None
    effective_gas_price: Optional[int] = None
    network_fee_wei: Optional[int] = None
    sender: str
    recipient: str
    amount_wei: int
    intent_hash: str
    raw_receipt: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("transaction_hash")
    @classmethod
    def validate_tx_hash(cls, v: str) -> str:
        v_clean = v.strip()
        if not ETH_TX_HASH_PATTERN.match(v_clean):
            raise ValueError(f"Invalid EVM transaction hash: '{v}'")
        return v_clean.lower()

    def compute_evidence_hash(self) -> str:
        """Compute cryptographic hash of the receipt evidence."""
        data = {
            "provider": self.provider,
            "network": self.network,
            "chain_id": self.chain_id,
            "transaction_hash": self.transaction_hash,
            "status": self.status,
            "block_number": self.block_number,
            "gas_used": self.gas_used,
            "effective_gas_price": self.effective_gas_price,
            "network_fee_wei": self.network_fee_wei,
            "sender": self.sender.lower(),
            "recipient": self.recipient.lower(),
            "amount_wei": self.amount_wei,
            "intent_hash": self.intent_hash,
        }
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
