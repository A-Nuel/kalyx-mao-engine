"""Deterministic policy rules for blockchain actions (RULE-BC-01 to RULE-BC-05).

Ensures blockchain proposals are strictly bounded by policy before any
authorization token is issued or wallet signing is permitted.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Set

from src.domain.blockchain import ETH_ADDRESS_PATTERN
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType
from src.governance.rules import PolicyRule


def is_blockchain_action(proposal: ActionProposal) -> bool:
    """Detect whether a proposal represents an on-chain blockchain action."""
    if proposal.action_type == ActionType.BLOCKCHAIN_TRANSACTION:
        return True
    if proposal.target and (
        proposal.target.startswith("blockchain://")
        or proposal.target.startswith("evm://")
        or proposal.target.startswith("sepolia://")
    ):
        return True
    if isinstance(proposal.parameters, dict) and "chain_id" in proposal.parameters:
        return True
    return False


class AllowedChainRule(PolicyRule):
    """RULE-BC-01: Only explicitly configured networks/chains may be used."""

    rule_id = "RULE-BC-01"
    description = "Only configured EVM networks and chain IDs may be targeted"

    def __init__(self, allowed_chain_ids: Optional[Set[int]] = None):
        # Default: Ethereum Sepolia testnet only (11155111)
        self.allowed_chain_ids = allowed_chain_ids or {11155111}

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if not is_blockchain_action(proposal):
            return None

        chain_id = proposal.parameters.get("chain_id") if isinstance(proposal.parameters, dict) else None
        if chain_id is None:
            return "Blockchain proposal must specify a target 'chain_id'"

        try:
            cid = int(chain_id)
        except (ValueError, TypeError):
            return f"Invalid chain_id '{chain_id}'; must be an integer"

        if cid not in self.allowed_chain_ids:
            return (
                f"Chain ID {cid} is not in the allowed network list ({sorted(self.allowed_chain_ids)}); "
                "mainnet and unapproved chains are strictly prohibited"
            )
        return None


class RecipientAllowlistRule(PolicyRule):
    """RULE-BC-02: Recipient address must be valid and on the approved allowlist."""

    rule_id = "RULE-BC-02"
    description = "Recipient address must be a valid EVM address on the approved allowlist"

    def __init__(self, approved_recipients: Optional[Set[str]] = None, allow_any_valid_testnet: bool = False):
        self.approved_recipients = {r.lower() for r in approved_recipients} if approved_recipients else set()
        self.allow_any_valid_testnet = allow_any_valid_testnet

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if not is_blockchain_action(proposal):
            return None

        params = proposal.parameters if isinstance(proposal.parameters, dict) else {}
        recipient = params.get("recipient") or proposal.target.replace("evm://", "").replace("blockchain://", "").replace("sepolia://", "")

        if not recipient:
            return "Blockchain proposal must specify a 'recipient' address"

        recipient = str(recipient).strip().lower()
        if not ETH_ADDRESS_PATTERN.match(recipient):
            return f"Invalid EVM recipient address '{recipient}'; must be a 42-char 0x-prefixed hex address"

        if self.approved_recipients and recipient not in self.approved_recipients:
            return f"Recipient address '{recipient}' is not on the approved destination allowlist"

        if not self.approved_recipients and not self.allow_any_valid_testnet:
            return "No approved recipient allowlist configured for blockchain actions"

        return None


class TransactionAmountCeilingRule(PolicyRule):
    """RULE-BC-03: Transaction amount must not exceed policy ceiling."""

    rule_id = "RULE-BC-03"
    description = "Transaction credit and value amount must not exceed the policy ceiling"

    def __init__(self, max_credits: int = 50, max_wei: int = 50_000_000_000_000_000):  # 0.05 ETH default max
        self.max_credits = max_credits
        self.max_wei = max_wei

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if not is_blockchain_action(proposal):
            return None

        if proposal.requested_credits > self.max_credits:
            return f"Requested {proposal.requested_credits} credits exceeds blockchain action ceiling of {self.max_credits}"

        params = proposal.parameters if isinstance(proposal.parameters, dict) else {}
        amount_wei = params.get("amount_wei")
        if amount_wei is not None:
            try:
                wei_val = int(amount_wei)
                if wei_val > self.max_wei:
                    return f"Transaction value of {wei_val} wei exceeds maximum allowable ceiling of {self.max_wei} wei"
                if wei_val < 0:
                    return "Transaction value cannot be negative"
            except (ValueError, TypeError):
                return f"Invalid amount_wei '{amount_wei}'"

        return None


class GasExposureRule(PolicyRule):
    """RULE-BC-04: Gas fee parameters must not exceed maximum exposure limit."""

    rule_id = "RULE-BC-04"
    description = "Gas price and gas limit must not exceed safe exposure thresholds"

    def __init__(self, max_fee_per_gas: int = 100_000_000_000, max_gas_limit: int = 200_000):
        self.max_fee_per_gas = max_fee_per_gas  # 100 gwei
        self.max_gas_limit = max_gas_limit

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if not is_blockchain_action(proposal):
            return None

        params = proposal.parameters if isinstance(proposal.parameters, dict) else {}
        max_fee = params.get("max_fee_per_gas")
        if max_fee is not None:
            try:
                if int(max_fee) > self.max_fee_per_gas:
                    return f"Max fee per gas ({max_fee}) exceeds maximum allowable ceiling of {self.max_fee_per_gas}"
            except (ValueError, TypeError):
                return f"Invalid max_fee_per_gas '{max_fee}'"

        gas_limit = params.get("gas_limit")
        if gas_limit is not None:
            try:
                if int(gas_limit) > self.max_gas_limit:
                    return f"Gas limit ({gas_limit}) exceeds maximum allowable ceiling of {self.max_gas_limit}"
            except (ValueError, TypeError):
                return f"Invalid gas_limit '{gas_limit}'"

        return None


class IntentMatchRule(PolicyRule):
    """RULE-BC-05: Transaction parameters must match authorized intent specifications."""

    rule_id = "RULE-BC-05"
    description = "Transaction intent parameters must match proposal specifications"

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs: Any) -> Optional[str]:
        if not is_blockchain_action(proposal):
            return None

        params = proposal.parameters if isinstance(proposal.parameters, dict) else {}
        # If amount_credits is provided in parameters, it must equal requested_credits
        if "amount_credits" in params and params["amount_credits"] != proposal.requested_credits:
            return (
                f"Parameter amount_credits ({params['amount_credits']}) does not match "
                f"proposal requested_credits ({proposal.requested_credits})"
            )

        return None
