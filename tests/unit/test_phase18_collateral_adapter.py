import pytest

from src.external.models import ExternalProviderMode
from src.external.orbio.collateral_adapter import CollateralVaultAdapter, build_collateral_vault_adapter
from src.external.orbio.collateral_config import CollateralVaultConfig


def _simulated_adapter() -> CollateralVaultAdapter:
    cfg = CollateralVaultConfig(
        mode=ExternalProviderMode.SIMULATED,
        rpc_url="",
        vault_address=None,
        credit_token_address=None,
        signer_private_key=None,
    )
    return CollateralVaultAdapter(config=cfg)


def _disabled_adapter() -> CollateralVaultAdapter:
    cfg = CollateralVaultConfig(
        mode=ExternalProviderMode.DISABLED,
        rpc_url="",
        vault_address=None,
        credit_token_address=None,
        signer_private_key=None,
    )
    return CollateralVaultAdapter(config=cfg)


def test_disabled_mode_rejects_all_operations():
    adapter = _disabled_adapter()
    with pytest.raises(RuntimeError, match="disabled"):
        adapter.lock(position_id="p1", pledger_address="0xabc", amount_atoms=1_000_000)


def test_lock_then_release_round_trip():
    adapter = _simulated_adapter()
    receipt = adapter.lock(position_id="p1", pledger_address="0xabc", amount_atoms=20_000_000)
    assert receipt.is_simulated is True
    assert receipt.tx_hash.startswith("sim-")

    pos = adapter.get_position(position_id="p1")
    assert pos["state"] == "LOCKED"
    assert pos["amount"] == 20_000_000

    release_receipt = adapter.release(position_id="p1")
    assert release_receipt.is_simulated is True

    pos_after = adapter.get_position(position_id="p1")
    assert pos_after["state"] == "RELEASED"


def test_lock_then_forfeit_round_trip():
    adapter = _simulated_adapter()
    adapter.lock(position_id="p2", pledger_address="0xabc", amount_atoms=10_000_000)
    receipt = adapter.forfeit(position_id="p2", beneficiary_address="0xdead")
    assert receipt.is_simulated is True

    pos = adapter.get_position(position_id="p2")
    assert pos["state"] == "FORFEITED"
    assert pos["beneficiary"] == "0xdead"


def test_duplicate_lock_rejected():
    adapter = _simulated_adapter()
    adapter.lock(position_id="p3", pledger_address="0xabc", amount_atoms=5_000_000)
    with pytest.raises(ValueError, match="already exists"):
        adapter.lock(position_id="p3", pledger_address="0xabc", amount_atoms=5_000_000)


def test_replay_release_after_settlement_rejected():
    """Mirrors CollateralVault.sol's PositionNotLocked guard and
    src/domain/collateral.py's invariant 6 — the simulated path must enforce
    the identical replay protection the real contract would."""
    adapter = _simulated_adapter()
    adapter.lock(position_id="p4", pledger_address="0xabc", amount_atoms=5_000_000)
    adapter.release(position_id="p4")
    with pytest.raises(ValueError, match="not LOCKED"):
        adapter.release(position_id="p4")
    with pytest.raises(ValueError, match="not LOCKED"):
        adapter.forfeit(position_id="p4", beneficiary_address="0xdead")


def test_release_of_unknown_position_rejected():
    adapter = _simulated_adapter()
    with pytest.raises(ValueError, match="not found"):
        adapter.release(position_id="does-not-exist")


def test_get_position_for_unknown_id_reports_none_state():
    adapter = _simulated_adapter()
    pos = adapter.get_position(position_id="never-locked")
    assert pos["state"] == "NONE"
    assert pos["amount"] == 0


def test_build_collateral_vault_adapter_returns_none_when_disabled(monkeypatch):
    monkeypatch.delenv("KALYX_COLLATERAL_MODE", raising=False)
    adapter = build_collateral_vault_adapter()
    assert adapter is None


def test_build_collateral_vault_adapter_returns_simulated_instance(monkeypatch):
    monkeypatch.setenv("KALYX_COLLATERAL_MODE", "simulated")
    adapter = build_collateral_vault_adapter()
    assert adapter is not None
    assert adapter.config.mode == ExternalProviderMode.SIMULATED
    monkeypatch.delenv("KALYX_COLLATERAL_MODE", raising=False)


def test_live_mode_without_config_fails_closed(monkeypatch):
    """Fail-closed check mirrors validate_orbio_config's own tests — a LIVE
    mode with missing rpc_url/vault_address/etc must raise at construction
    time, never silently behave as if it were SIMULATED."""
    cfg = CollateralVaultConfig(
        mode=ExternalProviderMode.LIVE,
        rpc_url="",
        vault_address=None,
        credit_token_address=None,
        signer_private_key=None,
    )
    with pytest.raises(RuntimeError, match="live configuration error"):
        CollateralVaultAdapter(config=cfg)
