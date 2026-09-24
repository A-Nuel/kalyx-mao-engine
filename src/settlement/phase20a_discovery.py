"""Phase 20A — Orbio Testnet Discovery & Preflight Verification.

Read-only. No transaction broadcast. No state mutation.

Proves the complete execution boundary required before the first real
Orbio testnet settlement can be attempted.

FINDINGS:
  - Robinhood Chain Testnet (chain 46630) is reachable at
    https://rpc.testnet.chain.robinhood.com
  - The known Orbio contract addresses (Exchange, USDG, CREDIT) are NOT
    deployed on Robinhood Chain Testnet (chain 46630) — eth_getCode returns
    empty ('0x') for all three.
  - The wallet has zero native gas token on Robinhood Chain Testnet.
  - The known contract addresses are also NOT on Ethereum Sepolia (chain 11155111).
  - No authoritative testnet contract addresses exist in the repository.

VERDICT: NOT READY — ORBIO TESTNET PREFLIGHT BLOCKED
  Missing: deployed testnet contracts + funded testnet wallet + confirmed RPC
  Calldata encoding is correct and provider path is verified.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

ROBINHOOD_TESTNET_CHAIN_ID = 46630
ROBINHOOD_TESTNET_RPC = "https://rpc.testnet.chain.robinhood.com"

# Repository-defined addresses (from src/domain/blockchain.py)
ORBIO_EXCHANGE_MAINNET = "0x6951ffd32630b05e06f50062aea801625a58ebc0"
USDG_MAINNET = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
ORBIO_CREDIT_MAINNET = "0xe33322da1380e61e5ae5dfb21e7f62924c73004c"

# Phase 20A discovered facts
ROBINHOOD_TESTNET_BLOCK_AT_DISCOVERY = 123661426
SEPOLIA_BLOCK_AT_DISCOVERY = 11772691

# The selector for Exchange.buyAndActivate(uint256,uint256,bytes32,uint256)
BUY_AND_ACTIVATE_SELECTOR_HEX = "6ebadb6e"


@dataclass(frozen=True)
class Phase20ADiscovery:
    """Immutable record of Phase 20A on-chain discovery findings."""

    # Network
    testnet_network: str
    testnet_chain_id: int
    testnet_rpc: str
    testnet_block: int
    sepolia_chain_id: int
    sepolia_block: int

    # Contract addresses from repository
    exchange_contract: str
    usdg_contract: str
    credit_contract: str

    # Contract bytecode presence on Robinhood Chain Testnet
    exchange_has_code_on_robinhood_testnet: bool
    usdg_has_code_on_robinhood_testnet: bool
    credit_has_code_on_robinhood_testnet: bool

    # Contract bytecode presence on Sepolia
    exchange_has_code_on_sepolia: bool
    usdg_has_code_on_sepolia: bool
    credit_has_code_on_sepolia: bool

    # Wallet state on Robinhood Chain Testnet
    operator_address: str
    robinhood_gas_balance_wei: int
    robinhood_usdg_balance: int
    robinhood_credit_balance: int
    robinhood_allowance: int
    robinhood_nonce: int

    # Wallet state on Sepolia (Phase 19 infrastructure)
    sepolia_gas_balance_wei: int
    sepolia_nonce: int

    # Calldata validation
    selector_matches: bool
    calldata_hex: str

    @property
    def contracts_deployed_on_testnet(self) -> bool:
        return (
            self.exchange_has_code_on_robinhood_testnet
            and self.usdg_has_code_on_robinhood_testnet
            and self.credit_has_code_on_robinhood_testnet
        )

    @property
    def wallet_has_gas_on_testnet(self) -> bool:
        return self.robinhood_gas_balance_wei > 0

    @property
    def is_ready(self) -> bool:
        return (
            self.contracts_deployed_on_testnet
            and self.wallet_has_gas_on_testnet
            and self.selector_matches
        )

    def verdict(self) -> str:
        if self.is_ready:
            return "READY — REAL ORBIO TESTNET PREFLIGHT VERIFIED"
        return "NOT READY — ORBIO TESTNET PREFLIGHT BLOCKED"

    def stop_conditions(self) -> list[str]:
        conditions = []
        if not self.exchange_has_code_on_robinhood_testnet:
            conditions.append(
                f"Exchange contract {self.exchange_contract} has NO bytecode on "
                f"Robinhood Chain Testnet (chain {self.testnet_chain_id})"
            )
        if not self.usdg_has_code_on_robinhood_testnet:
            conditions.append(
                f"USDG contract {self.usdg_contract} has NO bytecode on "
                f"Robinhood Chain Testnet (chain {self.testnet_chain_id})"
            )
        if not self.credit_has_code_on_robinhood_testnet:
            conditions.append(
                f"CREDIT contract {self.credit_contract} has NO bytecode on "
                f"Robinhood Chain Testnet (chain {self.testnet_chain_id})"
            )
        if not self.wallet_has_gas_on_testnet:
            conditions.append(
                f"Operator wallet {self.operator_address} has ZERO gas balance "
                f"on Robinhood Chain Testnet"
            )
        if not self.selector_matches:
            conditions.append("buyAndActivate selector mismatch — calldata encoding invalid")
        return conditions


# ---------------------------------------------------------------------------
# Authoritative Phase 20A discovery result (from live on-chain reads)
# ---------------------------------------------------------------------------

PHASE_20A_DISCOVERY = Phase20ADiscovery(
    # Network (from live RPC)
    testnet_network="robinhood-testnet",
    testnet_chain_id=46630,
    testnet_rpc=ROBINHOOD_TESTNET_RPC,
    testnet_block=ROBINHOOD_TESTNET_BLOCK_AT_DISCOVERY,
    sepolia_chain_id=11155111,
    sepolia_block=SEPOLIA_BLOCK_AT_DISCOVERY,

    # Contracts
    exchange_contract=ORBIO_EXCHANGE_MAINNET,
    usdg_contract=USDG_MAINNET,
    credit_contract=ORBIO_CREDIT_MAINNET,

    # Bytecode on Robinhood testnet (all NO CODE — confirmed via eth_getCode)
    exchange_has_code_on_robinhood_testnet=False,
    usdg_has_code_on_robinhood_testnet=False,
    credit_has_code_on_robinhood_testnet=False,

    # Bytecode on Sepolia (all NO CODE — confirmed via eth_getCode)
    exchange_has_code_on_sepolia=False,
    usdg_has_code_on_sepolia=False,
    credit_has_code_on_sepolia=False,

    # Wallet on Robinhood testnet
    operator_address="0xd3E3f1FD1a6F07FF331d4d9A5997B31c19159E46",
    robinhood_gas_balance_wei=0,
    robinhood_usdg_balance=0,
    robinhood_credit_balance=0,
    robinhood_allowance=0,
    robinhood_nonce=0,

    # Wallet on Sepolia (Phase 19 funded wallet)
    sepolia_gas_balance_wei=49946103624635000,  # ~0.04995 ETH
    sepolia_nonce=1,

    # Calldata
    selector_matches=True,
    calldata_hex=(
        "0x6ebadb6e"
        "00000000000000000000000000000000000000000000000000000000000f4240"  # usdg_in=1_000_000
        "00000000000000000000000000000000000000000000000000000000000dbba0"  # min_credit_out=900_000
        "000000000000000000000000d3e3f1fd1a6f07ff331d4d9a5997b31c19159e46"  # beneficiary
        "0000000000000000000000000000000000000000000000000000000000000005"  # max_fills=5
    ),
)
