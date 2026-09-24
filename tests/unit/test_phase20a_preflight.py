"""Phase 20A — Orbio Testnet Preflight Tests.

Verifies the discovery findings without broadcasting any transaction:
- Robinhood Chain Testnet RPC is reachable and returns correct chain ID
- Orbio contracts (Exchange, USDG, CREDIT) have NO code on either chain
- Calldata encoding is deterministic and selector is correct
- Simulation separation: SimulatedOrbioExchangeProvider ≠ OrbioExchangeProvider
- No automatic fallback from real provider → simulation
- Discovery constants are internally consistent

These are all read-only, no-broadcast assertions.
"""

from __future__ import annotations

import pytest

from src.domain.blockchain import (
    BUY_AND_ACTIVATE_SELECTOR,
    BUY_AND_ACTIVATE_SIGNATURE,
    ORBIO_CREDIT_MAINNET,
    ORBIO_EXCHANGE_MAINNET,
    USDG_MAINNET,
    encode_buy_and_activate_calldata,
)
from src.governance.orbio_purchase_rules import (
    DEFAULT_DEPLOYMENTS,
    DEPLOYMENT_ROBINHOOD_MAINNET,
    DEPLOYMENT_ROBINHOOD_TESTNET,
    DEPLOYMENT_ROBINHOOD_TESTNET_ALIAS,
)
from src.settlement.orbio_exchange_provider import OrbioExchangeProvider
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider
from src.settlement.phase20a_discovery import PHASE_20A_DISCOVERY


# ---------------------------------------------------------------------------
# Step 2: Testnet configuration is authoritative
# ---------------------------------------------------------------------------


class TestTestnetConfiguration:
    def test_robinhood_testnet_chain_id(self):
        assert DEPLOYMENT_ROBINHOOD_TESTNET.chain_id == 46630

    def test_robinhood_testnet_network_name(self):
        assert DEPLOYMENT_ROBINHOOD_TESTNET.network == "robinhood-testnet"

    def test_mainnet_chain_id(self):
        assert DEPLOYMENT_ROBINHOOD_MAINNET.chain_id == 4663

    def test_exchange_contract_address_format(self):
        import re
        assert re.match(r"^0x[0-9a-fA-F]{40}$", ORBIO_EXCHANGE_MAINNET)

    def test_usdg_contract_address_format(self):
        import re
        assert re.match(r"^0x[0-9a-fA-F]{40}$", USDG_MAINNET)

    def test_credit_contract_address_format(self):
        import re
        assert re.match(r"^0x[0-9a-fA-F]{40}$", ORBIO_CREDIT_MAINNET)

    def test_testnet_uses_same_contracts_as_mainnet(self):
        """Repository explicitly uses same addresses for testnet and mainnet."""
        assert DEPLOYMENT_ROBINHOOD_TESTNET.exchange_contract == ORBIO_EXCHANGE_MAINNET
        assert DEPLOYMENT_ROBINHOOD_TESTNET.payment_token == USDG_MAINNET
        assert DEPLOYMENT_ROBINHOOD_TESTNET.credit_token == ORBIO_CREDIT_MAINNET

    def test_default_deployments_includes_testnet(self):
        chain_ids = {d.chain_id for d in DEFAULT_DEPLOYMENTS}
        assert 46630 in chain_ids

    def test_default_deployments_includes_mainnet(self):
        chain_ids = {d.chain_id for d in DEFAULT_DEPLOYMENTS}
        assert 4663 in chain_ids


# ---------------------------------------------------------------------------
# Step 4: Calldata encoding
# ---------------------------------------------------------------------------


class TestCalldataEncoding:
    OPERATOR_ADDR = "0xd3E3f1FD1a6F07FF331d4d9A5997B31c19159E46"

    def test_selector_is_correct(self):
        """keccak256('buyAndActivate(uint256,uint256,bytes32,uint256)')[:4]"""
        expected = bytes.fromhex("6ebadb6e")
        assert BUY_AND_ACTIVATE_SELECTOR == expected

    def test_signature_string(self):
        assert BUY_AND_ACTIVATE_SIGNATURE == "buyAndActivate(uint256,uint256,bytes32,uint256)"

    def test_calldata_starts_with_selector(self):
        cd = encode_buy_and_activate_calldata(
            usdg_in=1_000_000,
            min_credit_out=900_000,
            beneficiary=self.OPERATOR_ADDR,
            max_fills=5,
        )
        assert cd.startswith("0x6ebadb6e"), f"Selector mismatch: {cd[:10]}"

    def test_calldata_length(self):
        """4-byte selector + 4 × 32-byte ABI-encoded params = 132 bytes."""
        cd = encode_buy_and_activate_calldata(
            usdg_in=1_000_000,
            min_credit_out=900_000,
            beneficiary=self.OPERATOR_ADDR,
            max_fills=5,
        )
        raw = bytes.fromhex(cd[2:])
        assert len(raw) == 132, f"Expected 132 bytes, got {len(raw)}"

    def test_calldata_deterministic(self):
        cd1 = encode_buy_and_activate_calldata(1_000_000, 900_000, self.OPERATOR_ADDR, 5)
        cd2 = encode_buy_and_activate_calldata(1_000_000, 900_000, self.OPERATOR_ADDR, 5)
        assert cd1 == cd2

    def test_calldata_differs_on_different_params(self):
        cd1 = encode_buy_and_activate_calldata(1_000_000, 900_000, self.OPERATOR_ADDR, 5)
        cd2 = encode_buy_and_activate_calldata(2_000_000, 900_000, self.OPERATOR_ADDR, 5)
        assert cd1 != cd2

    def test_beneficiary_address_encoded_as_bytes32(self):
        """Address must be left-padded with zeros in bytes32 slot."""
        cd = encode_buy_and_activate_calldata(
            usdg_in=1_000_000,
            min_credit_out=900_000,
            beneficiary=self.OPERATOR_ADDR,
            max_fills=5,
        )
        # bytes32 slot (offset 68..132 in hex string after '0x')
        # selector=8 chars, then 3 params × 64 chars each
        b32_slot = cd[2 + 8 + 64 + 64: 2 + 8 + 64 + 64 + 64]
        # lower-case address without 0x, zero-padded to 64 hex chars
        expected = self.OPERATOR_ADDR[2:].lower().zfill(64)
        assert b32_slot.lower() == expected, f"Beneficiary slot: {b32_slot} != {expected}"

    def test_matches_discovery_calldata(self):
        """Calldata must match the Phase 20A authoritative record."""
        cd = encode_buy_and_activate_calldata(
            usdg_in=1_000_000,
            min_credit_out=900_000,
            beneficiary=self.OPERATOR_ADDR,
            max_fills=5,
        )
        # Remove spaces from discovery record
        expected = PHASE_20A_DISCOVERY.calldata_hex.replace(" ", "")
        assert cd.lower() == expected.lower()


# ---------------------------------------------------------------------------
# Step 7: Simulation separation
# ---------------------------------------------------------------------------


class TestSimulationSeparation:
    def test_simulated_provider_name(self):
        prov = SimulatedOrbioExchangeProvider()
        assert prov.name == "orbio-exchange-simulated"

    def test_real_provider_name(self):
        from src.settlement.blockchain.rpc_client import SimulatedEvmRpcClient
        from src.settlement.blockchain.signer import LocalKeySigner
        sim_key = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
        rpc = SimulatedEvmRpcClient(chain_id=46630)
        signer = LocalKeySigner(sim_key)
        prov = OrbioExchangeProvider(
            rpc_client=rpc,
            signer=signer,
            default_chain_id=46630,
            network_name="robinhood-testnet",
        )
        assert prov.name == "orbio-exchange"

    def test_real_provider_is_not_simulated_by_default(self):
        from src.settlement.blockchain.rpc_client import SimulatedEvmRpcClient
        from src.settlement.blockchain.signer import LocalKeySigner
        sim_key = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
        rpc = SimulatedEvmRpcClient(chain_id=46630)
        signer = LocalKeySigner(sim_key)
        prov = OrbioExchangeProvider(rpc_client=rpc, signer=signer)
        assert prov.is_simulated is False

    def test_real_and_simulated_providers_are_different_types(self):
        assert OrbioExchangeProvider is not SimulatedOrbioExchangeProvider

    def test_simulated_provider_flags_simulated_in_name(self):
        prov = SimulatedOrbioExchangeProvider()
        assert "simulated" in prov.name


# ---------------------------------------------------------------------------
# Phase 20A discovery record assertions
# ---------------------------------------------------------------------------


class TestPhase20ADiscovery:
    def test_testnet_chain_id_is_46630(self):
        assert PHASE_20A_DISCOVERY.testnet_chain_id == 46630

    def test_testnet_network_name(self):
        assert PHASE_20A_DISCOVERY.testnet_network == "robinhood-testnet"

    def test_testnet_rpc_is_robinhood(self):
        assert "robinhood" in PHASE_20A_DISCOVERY.testnet_rpc

    def test_exchange_not_deployed_on_testnet(self):
        """STOP CONDITION: Exchange has no bytecode on Robinhood Chain Testnet."""
        assert not PHASE_20A_DISCOVERY.exchange_has_code_on_robinhood_testnet

    def test_usdg_not_deployed_on_testnet(self):
        """STOP CONDITION: USDG has no bytecode on Robinhood Chain Testnet."""
        assert not PHASE_20A_DISCOVERY.usdg_has_code_on_robinhood_testnet

    def test_credit_not_deployed_on_testnet(self):
        """STOP CONDITION: CREDIT has no bytecode on Robinhood Chain Testnet."""
        assert not PHASE_20A_DISCOVERY.credit_has_code_on_robinhood_testnet

    def test_no_contracts_on_sepolia(self):
        """Orbio contracts must NOT be used on Ethereum Sepolia (Hard Rule 6)."""
        assert not PHASE_20A_DISCOVERY.exchange_has_code_on_sepolia
        assert not PHASE_20A_DISCOVERY.usdg_has_code_on_sepolia
        assert not PHASE_20A_DISCOVERY.credit_has_code_on_sepolia

    def test_zero_gas_balance_on_testnet(self):
        """Wallet has zero native gas token on Robinhood Chain Testnet."""
        assert PHASE_20A_DISCOVERY.robinhood_gas_balance_wei == 0

    def test_calldata_selector_matches(self):
        assert PHASE_20A_DISCOVERY.selector_matches is True

    def test_verdict_is_not_ready(self):
        assert PHASE_20A_DISCOVERY.verdict() == "NOT READY — ORBIO TESTNET PREFLIGHT BLOCKED"

    def test_stop_conditions_non_empty(self):
        stops = PHASE_20A_DISCOVERY.stop_conditions()
        assert len(stops) >= 2  # at minimum: no contracts + no gas

    def test_is_not_ready(self):
        assert not PHASE_20A_DISCOVERY.is_ready

    def test_no_transaction_broadcast_safety(self):
        """Confirm the discovery module contains no broadcast paths."""
        import ast
        import pathlib
        # Resolve from project root: tests/unit/ -> tests/ -> project root
        src = pathlib.Path(__file__).parent.parent.parent / "src" / "settlement" / "phase20a_discovery.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"send_raw_transaction", "eth_sendRawTransaction"}
        ]
        assert calls == [], "Discovery module must not contain any broadcast calls"
