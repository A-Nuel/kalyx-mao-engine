import pytest
from src.persistence.database import Database
from src.persistence.repositories import SqliteLedger, SqliteEventStore, SqliteRepository, TREASURY, EXTERNAL_SINK
from src.domain.entities import Organisation, AgentRecord, Task, ActionProposal, PolicyDecision, ExecutionReceipt
from src.domain.enums import OrgState, AgentRole, ActionType, TaskStatus, PolicyResult
from src.domain.exceptions import InsufficientCreditsError

def test_sqlite_ledger_lifecycle():
    db = Database(":memory:")
    ledger = SqliteLedger(db, initial_treasury=100)
    
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.verify_conservation() is True

    # Valid transfer
    entry = ledger.transfer(TREASURY, "AGENT:RESEARCH", 30, "Task allocation")
    assert entry.amount == 30
    assert ledger.get_balance(TREASURY) == 70
    assert ledger.get_balance("AGENT:RESEARCH") == 30
    assert ledger.verify_conservation() is True

    # Overdraft attempt
    with pytest.raises(InsufficientCreditsError):
        ledger.transfer(TREASURY, "AGENT:CEO", 150, "Exceeds balance")

    # Duplicate transaction ID
    with pytest.raises(ValueError) as exc:
        ledger.transfer(TREASURY, "AGENT:RESEARCH", 10, "First", transaction_id="tx-fixed-01")
        ledger.transfer(TREASURY, "AGENT:RESEARCH", 10, "Second", transaction_id="tx-fixed-01")
    assert "Duplicate transaction ID" in str(exc.value)

def test_sqlite_repository_crud():
    db = Database(":memory:")
    repo = SqliteRepository(db)

    org = Organisation(id="org-sqlite-01", mission="Test SQLite", treasury_balance=100)
    agent = AgentRecord(id="agent-ceo", role=AgentRole.CEO, authority_ceiling=25)
    org.agents["agent-ceo"] = agent

    repo.save_organisation(org)
    repo.save_agent(agent, org.id)

    loaded_org = repo.load_organisation("org-sqlite-01")
    assert loaded_org is not None
    assert loaded_org.mission == "Test SQLite"
    assert loaded_org.treasury_balance == 100
    assert "agent-ceo" in loaded_org.agents
    assert loaded_org.agents["agent-ceo"].role == AgentRole.CEO

def test_sqlite_event_store_lifecycle():
    db = Database(":memory:")
    store = SqliteEventStore(db, verify_on_startup=True)
    
    e1 = store.append_event("OPERATOR", "ORG_CREATED", "org-01", {"budget": 100})
    e2 = store.append_event("CEO", "PLAN_CREATED", "org-01", {"tasks": 3})

    assert e1.sequence_id == 1
    assert e2.sequence_id == 2
    assert e2.previous_event_hash == e1.event_hash

    valid, err = store.verify_integrity()
    assert valid is True
    assert err is None
