"""Governed SIMULATED/LIVE/DISABLED adapter for the Phase 18 CREDIT vault."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional

from src.external.models import ExternalProviderMode
from src.external.orbio.collateral_config import CollateralVaultConfig, validate_collateral_config


@dataclass
class VaultOperationReceipt:
    tx_hash: str
    is_simulated: bool


class _SimulatedVaultState:
    def __init__(self) -> None:
        self._positions: Dict[str, Dict[str, object]] = {}

    def lock(self, *, position_id: str, pledger: str, beneficiary: str, amount: int) -> str:
        if position_id in self._positions:
            raise ValueError(f"simulated position {position_id} already exists")
        if amount <= 0:
            raise ValueError("collateral amount must be positive")
        if not beneficiary:
            raise ValueError("beneficiary is required")
        self._positions[position_id] = {
            "pledger": pledger,
            "beneficiary": beneficiary,
            "amount": amount,
            "state": "LOCKED",
        }
        return self._sim_tx_hash("lock", position_id)

    def release(self, *, position_id: str) -> str:
        pos = self._require_locked(position_id)
        pos["state"] = "RELEASED"
        return self._sim_tx_hash("release", position_id)

    def forfeit(self, *, position_id: str) -> str:
        pos = self._require_locked(position_id)
        pos["state"] = "FORFEITED"
        return self._sim_tx_hash("forfeit", position_id)

    def get_position(self, *, position_id: str) -> Dict[str, object]:
        if position_id not in self._positions:
            return {"pledger": None, "beneficiary": None, "amount": 0, "state": "NONE"}
        return dict(self._positions[position_id])

    def _require_locked(self, position_id: str) -> Dict[str, object]:
        pos = self._positions.get(position_id)
        if pos is None:
            raise ValueError(f"simulated position {position_id} not found")
        if pos["state"] != "LOCKED":
            raise ValueError(
                f"simulated position {position_id} is {pos['state']}, not LOCKED; "
                "rejected replayed or out-of-sequence settlement call"
            )
        return pos

    @staticmethod
    def _sim_tx_hash(op: str, position_id: str) -> str:
        digest = hashlib.sha256(f"sim|{op}|{position_id}".encode()).hexdigest()
        return f"sim-{digest[:16]}"


class CollateralVaultAdapter:
    name = "collateral_vault"

    def __init__(self, config: Optional[CollateralVaultConfig] = None):
        self.config = config or CollateralVaultConfig.from_env()
        self._simulated_state: Optional[_SimulatedVaultState] = None
        self._chain_client = None
        if self.config.mode == ExternalProviderMode.LIVE:
            validate_collateral_config(self.config)
            from src.external.orbio.collateral_chain_client import CollateralVaultClient
            self._chain_client = CollateralVaultClient(
                rpc_url=self.config.rpc_url,
                vault_address=self.config.vault_address,  # type: ignore[arg-type]
                credit_token_address=self.config.credit_token_address,  # type: ignore[arg-type]
                owner_private_key=self.config.owner_private_key,  # type: ignore[arg-type]
                pledger_private_key=self.config.pledger_private_key,  # type: ignore[arg-type]
            )
        elif self.config.mode == ExternalProviderMode.SIMULATED:
            self._simulated_state = _SimulatedVaultState()

    def _require_enabled(self) -> None:
        if self.config.mode == ExternalProviderMode.DISABLED:
            raise RuntimeError("Collateral vault is disabled (KALYX_COLLATERAL_MODE=disabled)")

    def lock(
        self,
        *,
        position_id: str,
        pledger_address: str,
        beneficiary_address: str,
        amount_atoms: int,
    ) -> VaultOperationReceipt:
        self._require_enabled()
        if self.config.mode == ExternalProviderMode.SIMULATED:
            assert self._simulated_state is not None
            tx = self._simulated_state.lock(
                position_id=position_id,
                pledger=pledger_address,
                beneficiary=beneficiary_address,
                amount=amount_atoms,
            )
            return VaultOperationReceipt(tx_hash=tx, is_simulated=True)

        assert self._chain_client is not None
        from src.external.orbio.collateral_chain_client import position_id_to_bytes32
        tx = self._chain_client.approve_and_lock(
            position_id_bytes32=position_id_to_bytes32(position_id),
            amount_atoms=amount_atoms,
            beneficiary_address=beneficiary_address,
            expected_pledger_address=pledger_address,
        )
        return VaultOperationReceipt(tx_hash=tx, is_simulated=False)

    def release(self, *, position_id: str) -> VaultOperationReceipt:
        self._require_enabled()
        if self.config.mode == ExternalProviderMode.SIMULATED:
            assert self._simulated_state is not None
            tx = self._simulated_state.release(position_id=position_id)
            return VaultOperationReceipt(tx_hash=tx, is_simulated=True)
        assert self._chain_client is not None
        from src.external.orbio.collateral_chain_client import position_id_to_bytes32
        tx = self._chain_client.release(position_id_bytes32=position_id_to_bytes32(position_id))
        return VaultOperationReceipt(tx_hash=tx, is_simulated=False)

    def forfeit(self, *, position_id: str) -> VaultOperationReceipt:
        self._require_enabled()
        if self.config.mode == ExternalProviderMode.SIMULATED:
            assert self._simulated_state is not None
            tx = self._simulated_state.forfeit(position_id=position_id)
            return VaultOperationReceipt(tx_hash=tx, is_simulated=True)
        assert self._chain_client is not None
        from src.external.orbio.collateral_chain_client import position_id_to_bytes32
        tx = self._chain_client.forfeit(position_id_bytes32=position_id_to_bytes32(position_id))
        return VaultOperationReceipt(tx_hash=tx, is_simulated=False)

    def get_position(self, *, position_id: str) -> Dict[str, object]:
        self._require_enabled()
        if self.config.mode == ExternalProviderMode.SIMULATED:
            assert self._simulated_state is not None
            return self._simulated_state.get_position(position_id=position_id)
        assert self._chain_client is not None
        from src.external.orbio.collateral_chain_client import position_id_to_bytes32
        return self._chain_client.get_position(position_id_bytes32=position_id_to_bytes32(position_id))


def build_collateral_vault_adapter(
    config: Optional[CollateralVaultConfig] = None,
) -> Optional[CollateralVaultAdapter]:
    cfg = config or CollateralVaultConfig.from_env()
    if cfg.mode == ExternalProviderMode.DISABLED:
        return None
    return CollateralVaultAdapter(config=cfg)
