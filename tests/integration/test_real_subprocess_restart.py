import sys
import os
import subprocess
import pytest
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)

def test_real_subprocess_restart_survival(tmp_path):
    db_file = str(tmp_path / "restart_survival.db")

    # Environment with project root on PYTHONPATH
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO_ROOT
    env["PYTHONIOENCODING"] = "utf-8"

    # Process 1: Setup DB, initialize ledger, record events, transfer credits, pause org, exit
    proc1_code = f"""
import sys
from src.persistence.database import Database
from src.persistence.repositories import SqliteLedger, SqliteEventStore, SqliteRepository, TREASURY, EXTERNAL_SINK
from src.domain.entities import Organisation, AgentRecord
from src.domain.enums import OrgState, AgentRole, ActionType

db = Database(r"{db_file}")
ledger = SqliteLedger(db, initial_treasury=100)
events = SqliteEventStore(db, verify_on_startup=True)
repo = SqliteRepository(db)

org = Organisation(id="org-reboot", mission="Reboot Survival Mission", treasury_balance=100, state=OrgState.EXECUTING)
repo.save_organisation(org)

agent = AgentRecord(id="agent-ceo", role=AgentRole.CEO, authority_ceiling=50, allowed_action_types=[ActionType.SIMULATED_ALLOCATION])
repo.save_agent(agent, org.id)

# Execute transfer on ledger
tx = ledger.transfer(TREASURY, EXTERNAL_SINK, 35, "Transfer before restart")

# Record audit event
events.append_event(
    actor_id="ORCHESTRATOR",
    event_type="TRANSFER_EXECUTED",
    entity_id=tx.id,
    payload={{"from": TREASURY, "to": EXTERNAL_SINK, "amount": 35}}
)

# Emergency pause organisation
org.state = OrgState.PAUSED
repo.save_organisation(org)
events.append_event(
    actor_id="HUMAN_GATE",
    event_type="ORG_PAUSED",
    entity_id=org.id,
    payload={{"reason": "Pre-restart security lock"}}
)

assert ledger.get_balance(TREASURY) == 65
sys.exit(0)
"""

    res1 = subprocess.run([sys.executable, "-c", proc1_code], capture_output=True, text=True, env=env)
    assert res1.returncode == 0, f"Process 1 failed with: {res1.stderr}"

    # Process 2: Clean OS process start. Must verify audit chain on startup, reload ledger and org state.
    proc2_code = f"""
import sys
from src.persistence.database import Database
from src.persistence.repositories import SqliteLedger, SqliteEventStore, SqliteRepository, TREASURY
from src.domain.enums import OrgState

db = Database(r"{db_file}")
# Startup integrity verification runs on init
events = SqliteEventStore(db, verify_on_startup=True)
assert len(events.get_events()) >= 2

ledger = SqliteLedger(db)
assert ledger.get_balance(TREASURY) == 65
assert ledger.verify_conservation() is True

repo = SqliteRepository(db)
org = repo.load_organisation("org-reboot", ledger=ledger)
assert org is not None
assert org.state == OrgState.PAUSED, f"Expected PAUSED state, got {{org.state}}"
assert org.treasury_balance == 65, f"Expected treasury 65, got {{org.treasury_balance}}"

sys.exit(0)
"""

    res2 = subprocess.run([sys.executable, "-c", proc2_code], capture_output=True, text=True, env=env)
    assert res2.returncode == 0, f"Process 2 restart failed with: {res2.stderr}"

def test_corrupted_audit_state_halts_startup_in_subprocess(tmp_path):
    db_file = str(tmp_path / "tamper_test.db")
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO_ROOT
    env["PYTHONIOENCODING"] = "utf-8"

    # Step 1: Create valid DB and audit events in a process
    setup_code = f"""
import sys
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore

db = Database(r"{db_file}")
events = SqliteEventStore(db, verify_on_startup=True)
events.append_event("SYSTEM", "INIT", "org-1", {{"msg": "Genesis block"}})
events.append_event("SYSTEM", "STEP", "org-1", {{"msg": "Second block"}})
sys.exit(0)
"""
    res_setup = subprocess.run([sys.executable, "-c", setup_code], capture_output=True, text=True, env=env)
    assert res_setup.returncode == 0

    # Step 2: Adversarially tamper with event payload in the SQLite file
    import sqlite3
    import json
    conn = sqlite3.connect(db_file)
    with conn:
        conn.execute("UPDATE audit_events SET payload = ? WHERE sequence_id = 1", (json.dumps({"tampered": True}),))
    conn.close()

    # Step 3: Spawn separate OS process. verify_on_startup=True must raise TamperedAuditLogError and halt startup
    startup_code = f"""
import sys
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore

db = Database(r"{db_file}")
# This must raise TamperedAuditLogError and exit with error
events = SqliteEventStore(db, verify_on_startup=True)
sys.exit(0)
"""
    res_tamper = subprocess.run([sys.executable, "-c", startup_code], capture_output=True, text=True, env=env)
    assert res_tamper.returncode != 0, "Subprocess should have crashed on corrupted audit chain startup"
    assert "TamperedAuditLogError" in res_tamper.stderr
    assert "Audit log corruption detected on startup" in res_tamper.stderr
