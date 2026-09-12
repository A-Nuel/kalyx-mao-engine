import pytest
from src.settlement.adapter import SettlementIntent, SimulatedSettlementAdapter


def test_simulated_settlement_is_deterministic_in_reference():
    adapter = SimulatedSettlementAdapter()
    intent = SettlementIntent("org-1", "proposal-1", "TREASURY", "sandbox://vendor", 10, "token")
    receipt = adapter.settle(intent)
    assert receipt.success is True
    assert receipt.external_reference.startswith("sim-")
    assert receipt.amount == 10
    assert receipt.proposal_id == "proposal-1"


def test_settlement_requires_positive_amount_and_authorization():
    adapter = SimulatedSettlementAdapter()
    with pytest.raises(ValueError):
        adapter.settle(SettlementIntent("o", "p", "TREASURY", "x", 0, "token"))
    with pytest.raises(ValueError):
        adapter.settle(SettlementIntent("o", "p", "TREASURY", "x", 1, ""))
