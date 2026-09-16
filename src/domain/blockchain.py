"""Domain models for blockchain transaction intents and on-chain receipt evidence.

Enforces strict typed representation of blockchain settlement intents.
The exact execution payload is deterministically bound to the approved intent,
ensuring an LLM or client cannot execute a different transaction than what was
authorized by policy.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator

from src.domain.events import canonical_json

ETH_ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
ETH_TX_HASH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")


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
