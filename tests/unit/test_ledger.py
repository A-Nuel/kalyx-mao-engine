import pytest
from src.economy.ledger import DoubleEntryLedger, TREASURY
from src.domain.exceptions import InsufficientCreditsError

def test_initial_treasury_and_conservation():
    ledger = DoubleEntryLedger(initial_treasury=100)
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.verify_conservation() is True

def test_successful_transfer():
    ledger = DoubleEntryLedger(initial_treasury=100)
    entry = ledger.transfer(TREASURY, "AGENT:RESEARCH", 25, "Task allocation")
    
    assert entry.amount == 25
    assert ledger.get_balance(TREASURY) == 75
    assert ledger.get_balance("AGENT:RESEARCH") == 25
    assert ledger.verify_conservation() is True

def test_overdraft_prevention():
    ledger = DoubleEntryLedger(initial_treasury=100)
    with pytest.raises(InsufficientCreditsError):
        ledger.transfer(TREASURY, "AGENT:CEO", 150, "Exceeds treasury")
    
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.get_balance("AGENT:CEO") == 0
    assert ledger.verify_conservation() is True

def test_zero_or_negative_transfer_rejected():
    ledger = DoubleEntryLedger(initial_treasury=100)
    with pytest.raises(ValueError):
        ledger.transfer(TREASURY, "AGENT:CEO", 0, "Zero amount")
    with pytest.raises(ValueError):
        ledger.transfer(TREASURY, "AGENT:CEO", -10, "Negative amount")

def test_multiple_transfers_and_entries_log():
    ledger = DoubleEntryLedger(initial_treasury=100)
    ledger.transfer(TREASURY, "AGENT:RESEARCH", 30, "Budget for research")
    ledger.transfer("AGENT:RESEARCH", "TASK:01", 10, "Action execution")
    ledger.transfer("TASK:01", "EXTERNAL_SINK", 10, "API Fee payment")

    assert ledger.get_balance(TREASURY) == 70
    assert ledger.get_balance("AGENT:RESEARCH") == 20
    assert ledger.get_balance("TASK:01") == 0
    assert ledger.get_balance("EXTERNAL_SINK") == 10
    assert ledger.verify_conservation() is True
    assert len(ledger.get_entries()) == 4
