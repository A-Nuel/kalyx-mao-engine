import os
import pytest
from src.persistence.database import Database
from src.persistence.repositories import SqliteLedger, SqliteEventStore, SqliteRepository, TREASURY, EXTERNAL_SINK
from src.domain.entities import Organisation, AgentRecord, Task
from src.domain.enums import OrgState, AgentRole, TaskStatus

def test_system_survives_process_restart(tmp_path):
    db_file = str(tmp_path / "restart_test.db")

    # --- PROCESS 1: Initialise & Execute ---
    db1 = Database(db_file)
    ledger1 = SqliteLedger(db1, initial_treasury=100)
    event_store1 = SqliteEventStore(db1, verify_on_startup=True)
    repo1 = SqliteRepository(db1)

    org1 = Organisation(id="org-restart", mission="Survive restart", treasury_balance=100, state=OrgState.EXECUTING)
    ceo1 = AgentRecord(id="agent-ceo", role=AgentRole.CEO, reputation_score=93.0)
    org1.agents["agent-ceo"] = ceo1

    task1 = Task(id="t-01", mission_id="org-restart", assigned_agent_id="agent-ceo", objective="Do work", allocated_credits=20, status=TaskStatus.COMPLETED)

    repo1.save_organisation(org1)
    repo1.save_agent(ceo1, org1.id)
    repo1.save_task(task1, org1.id)

    # Spend 20 credits
    ledger1.transfer(TREASURY, EXTERNAL_SINK, 20, "Simulated execution", transaction_id="tx-fixed-99")
    event_store1.append_event("ORCHESTRATOR", "MISSION_STARTED", org1.id, {"budget": 100})
    event_store1.append_event("EXECUTOR", "ACTION_EXECUTED", "exec-01", {"cost": 20})

    # Assert state before kill
    assert ledger1.get_balance(TREASURY) == 80
    assert ledger1.verify_conservation() is True

    # SIMULATE PROCESS SHUTDOWN
    db1.close()
    del db1, ledger1, event_store1, repo1

    # --- PROCESS 2: Reboot & Recover ---
    db2 = Database(db_file)
    # 1. Startup verification MUST pass
    event_store2 = SqliteEventStore(db2, verify_on_startup=True)
    valid, err = event_store2.verify_integrity()
    assert valid is True
    assert err is None
    assert len(event_store2.get_events()) == 2

    # 2. Ledger authoritative recovery: Zero balance drift
    ledger2 = SqliteLedger(db2, initial_treasury=0)
    assert ledger2.get_balance(TREASURY) == 80
    assert ledger2.get_balance(EXTERNAL_SINK) == 20
    assert ledger2.verify_conservation() is True

    # 3. Organisation state recovery
    repo2 = SqliteRepository(db2)
    recovered_org = repo2.load_organisation("org-restart")
    assert recovered_org is not None
    assert recovered_org.state == OrgState.EXECUTING
    assert recovered_org.agents["agent-ceo"].reputation_score == 93.0

    db2.close()
