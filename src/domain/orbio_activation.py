"""Phase 20B — Typed Orbio CREDIT activation intent (already-held CREDIT).

Path: existing CREDIT → credit.activate(amount) → API balance.
Not buyAndActivate. Not USDG purchase.

Agents never supply calldata. Encoding is deterministic from typed fields.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Dict, Optional

from eth_abi import encode as eth_abi_encode
from eth_hash.auto import keccak
from pydantic import BaseModel, Field, field_validator, model_validator

from src.domain.blockchain import BlockchainTransactionIntent, ORBIO_CREDIT_MAINNET
from src.domain.events import canonical_json

ETH_ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Robinhood Chain mainnet only for production activation.
ORBIO_ACTIVATION_CHAIN_ID = 4663
ORBIO_ACTIVATION_NETWORK = "robinhood-mainnet"
ORBIO_CREDIT_ACTIVATION_CONTRACT = ORBIO_CREDIT_MAINNET.lower()

# Hard first-production ceiling: exactly 1.000000 CREDIT (6 decimals).
MAX_ACTIVATION_AMOUNT = 1_000_000

ACTIVATE_SIGNATURE = "activate(uint256)"
# keccak256("activate(uint256)")[:4] — verified in tests against eth_hash.
ACTIVATE_SELECTOR = keccak(b"activate(uint256)")[:4]

ACTIVATED_EVENT_SIGNATURE = "Activated(uint256,address,bytes32,uint256)"
ACTIVATED_EVENT_TOPIC0 = "0x" + keccak(b"Activated(uint256,address,bytes32,uint256)").hex()

ACTIVATION_FEE_EVENT_SIGNATURE = "ActivationFeeCharged(uint256,uint256)"
ACTIVATION_FEE_EVENT_TOPIC0 = "0x" + keccak(b"ActivationFeeCharged(uint256,uint256)").hex()


def encode_activate_calldata(amount: int) -> str:
    """Deterministic credit.activate(uint256) calldata. Agents cannot supply raw data."""
    if amount < 0:
        raise ValueError("activation amount must be non-negative")
    encoded_args = eth_abi_encode(["uint256"], [amount])
    return "0x" + (ACTIVATE_SELECTOR + encoded_args).hex()


class OrbioCreditActivationIntent(BaseModel):
    """Typed authorization surface for activating already-held CREDIT.

    Projects onto BlockchainTransactionIntent for Phase 12 settlement.
    """

    tenant_id: str
    organisation_id: str
    mission_id: Optional[str] = None
    operation_id: str

    chain_id: int = Field(default=ORBIO_ACTIVATION_CHAIN_ID)
    network: str = Field(default=ORBIO_ACTIVATION_NETWORK)
    credit_contract: str = Field(default=ORBIO_CREDIT_ACTIVATION_CONTRACT)

    # Native CREDIT units (6 decimals). First production path max = 1_000_000.
    amount: int = Field(ge=1, description="CREDIT base units to activate")

    # Optional beneficiary for activate(uint256,bytes32) — first path uses single-arg activate.
    beneficiary: Optional[str] = Field(
        default=None,
        description="Unused for activate(uint256); reserved for future overload",
    )

    max_fee_per_gas: int = Field(default=25_000_000_000, ge=0)
    max_priority_fee_per_gas: int = Field(default=1_500_000_000, ge=0)
    gas_limit: int = Field(default=150_000, ge=21_000)

    # Kalyx internal escrow reservation (org credits), independent of Orbio units.
    amount_credits: int = Field(ge=0, default=0)

    idempotency_key: str
    policy_decision_id: str
    authorization_token_hash: str
    deadline: Optional[int] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("credit_contract")
    @classmethod
    def validate_contract(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if not ETH_ADDRESS_PATTERN.match(v_clean):
            raise ValueError(f"Invalid CREDIT contract address: '{v}'")
        return v_clean

    @model_validator(mode="after")
    def validate_bounds(self) -> "OrbioCreditActivationIntent":
        if self.amount <= 0:
            raise ValueError("activation amount must be positive")
        return self

    def encode_calldata(self) -> str:
        return encode_activate_calldata(self.amount)

    def compute_activation_intent_hash(self) -> str:
        data = {
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "mission_id": self.mission_id,
            "operation_id": self.operation_id,
            "chain_id": self.chain_id,
            "network": self.network,
            "credit_contract": self.credit_contract,
            "amount": self.amount,
            "beneficiary": self.beneficiary,
            "max_fee_per_gas": self.max_fee_per_gas,
            "max_priority_fee_per_gas": self.max_priority_fee_per_gas,
            "gas_limit": self.gas_limit,
            "amount_credits": self.amount_credits,
            "idempotency_key": self.idempotency_key,
            "policy_decision_id": self.policy_decision_id,
            "authorization_token_hash": self.authorization_token_hash,
            "deadline": self.deadline,
            "function": ACTIVATE_SIGNATURE,
        }
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()

    def to_blockchain_intent(self) -> BlockchainTransactionIntent:
        """Project onto Phase 12 settlement intent.

        recipient = CREDIT contract, amount_wei = 0, data = activate(amount).
        """
        return BlockchainTransactionIntent(
            tenant_id=self.tenant_id,
            organisation_id=self.organisation_id,
            mission_id=self.mission_id,
            operation_id=self.operation_id,
            chain_id=self.chain_id,
            network=self.network,
            recipient=self.credit_contract,
            amount_wei=0,
            amount_credits=self.amount_credits,
            asset="ORBIO_CREDIT",
            token_contract=self.credit_contract,
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

    def to_operation_parameters(self) -> Dict[str, Any]:
        """Parameters shape for ConsequentialOperation / BlockchainSettlementProvider."""
        return {
            "recipient": self.credit_contract,
            "amount_wei": 0,
            "chain_id": self.chain_id,
            "network": self.network,
            "data_payload": self.encode_calldata(),
            "token_contract": self.credit_contract,
            "asset": "ORBIO_CREDIT",
            "max_fee_per_gas": self.max_fee_per_gas,
            "max_priority_fee_per_gas": self.max_priority_fee_per_gas,
            "gas_limit": self.gas_limit,
            "mission_id": self.mission_id,
            "authorization_token": self.authorization_token_hash,
            "activation_intent_hash": self.compute_activation_intent_hash(),
            "activation_amount": self.amount,
            "credit_contract": self.credit_contract,
            "function": ACTIVATE_SIGNATURE,
            "orbio_action": "credit_activate",
        }
