import pytest

from src.external.models import ExternalProviderMode
from src.external.orbio.collateral_adapter import CollateralVaultAdapter, build_collateral_vault_adapter
from src.external.orbio.collateral_config import CollateralVaultConfig


def _simulated_adapter() -> CollateralVaultAdapter:
    return CollateralVaultAdapter(config=CollateralVaultConfig(
        mode=ExternalProviderMode.SIMULATED,
        rpc_url="",
        vault_address=None,
        credit_token_address=None,
        owner_private_key=None,
        pledger_private_key=None,
    ))


def _disabled_adapter() -> CollateralVaultAdapter:
    return CollateralVaultAdapter(config=CollateralVaultConfig(
        mode=ExternalProviderMode.DISABLED,
        rpc_url="",
        vault_address=None,
        credit_token_address=None,
        owner_private_key=None,
        pledger_private_key=None,
    ))


def test_disabled_mode_rejects_all_operations():
    adapter = _disabled_adapter()
    with pytest.raises(RuntimeError, match="disabled"):
        adapter.lock(position_id="p1", pledger_address="0xabc", beneficiary_address="0xdef", amount_atoms=1_000_000)


def test_lock_then_release_round_trip():
    adapter = _simulated_adapter()
    receipt = adapter.lock(position_id="p1", pledger_address="0xabc", beneficiary_address="0xdef", amount_atoms=20_000_000)
    assert receipt.is_simulated is True
    pos = adapter.get_position(position_id="p1")
    assert pos["state"] == "LOCKED"
    assert pos["beneficiary"] == "0xdef"
    assert pos["amount"] == 20_000_000

    release_receipt = adapter.release(position_id="p1")
    assert release_receipt.is_simulated is True
    assert adapter.get_position(position_id="p1")["state"] == "RELEASED"


def test_lock_then_forfeit_round_trip_uses_immutable_beneficiary():
    adapter = _simulated_adapter()
    adapter.lock(position_id="p2", pledger_address="0xabc", beneficiary_address="0xbeneficiary", amount_atoms=10_000_000)
    receipt = adapter.forfeit(position_id="p2")
    assert receipt.is_simulated is True
    pos = adapter.get_position(position_id="p2")
    assert pos["state"] == "FORFEITED"
    assert pos["beneficiary"] == "0xbeneficiary"


def test_duplicate_lock_rejected():
    adapter = _simulated_adapter()
    adapter.lock(position_id="p3", pledger_address="0xabc", beneficiary_address="0xdef", amount_atoms=5_000_000)
    with pytest.raises(ValueError, match="already exists"):
        adapter.lock(position_id="p3", pledger_address="0xabc", beneficiary_address="0xdef", amount_atoms=5_000_000)


def test_replay_release_and_forfeit_after_settlement_rejected():
    adapter = _simulated_adapter()
    adapter.lock(position_id="p4", pledger_address="0xabc", beneficiary_address="0xdef", amount_atoms=5_000_000)
    adapter.release(position_id="p4")
    with pytest.raises(ValueError, match="not LOCKED"):
        adapter.release(position_id="p4")
    with pytest.raises(ValueError, match="not LOCKED"):
        adapter.forfeit(position_id="p4")


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
    assert build_collateral_vault_adapter() is None


def test_build_collateral_vault_adapter_returns_simulated_instance(monkeypatch):
    monkeypatch.setenv("KALYX_COLLATERAL_MODE", "simulated")
    adapter = build_collateral_vault_adapter()
    assert adapter is not None
    assert adapter.config.mode == ExternalProviderMode.SIMULATED
    monkeypatch.delenv("KALYX_COLLATERAL_MODE", raising=False)


def test_live_mode_without_config_fails_closed():
    cfg = CollateralVaultConfig(
        mode=ExternalProviderMode.LIVE,
        rpc_url="",
        vault_address=None,
        credit_token_address=None,
        owner_private_key=None,
        pledger_private_key=None,
    )
    with pytest.raises(RuntimeError, match="live configuration error"):
        CollateralVaultAdapter(config=cfg)
