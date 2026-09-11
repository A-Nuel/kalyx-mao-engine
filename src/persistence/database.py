import sqlite3
import os
from typing import Optional

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS organisations (
    id TEXT PRIMARY KEY,
    mission TEXT NOT NULL,
    treasury_balance INTEGER NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    role TEXT NOT NULL,
    model_name TEXT NOT NULL,
    credit_balance INTEGER NOT NULL,
    reputation_score REAL NOT NULL,
    authority_ceiling INTEGER NOT NULL,
    allowed_action_types TEXT NOT NULL,
    status TEXT NOT NULL,
    successful_tasks INTEGER NOT NULL,
    failed_tasks INTEGER NOT NULL,
    policy_violations INTEGER NOT NULL,
    FOREIGN KEY (org_id) REFERENCES organisations(id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    assigned_agent_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    allocated_credits INTEGER NOT NULL,
    status TEXT NOT NULL,
    output_evidence TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (org_id) REFERENCES organisations(id)
);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    proposing_agent_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target TEXT NOT NULL,
    parameters TEXT NOT NULL,
    requested_credits INTEGER NOT NULL,
    expected_value_score REAL NOT NULL,
    risk_assessment TEXT NOT NULL,
    rationale TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS policy_decisions (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    result TEXT NOT NULL,
    violated_rule_id TEXT,
    violated_rule_description TEXT,
    evaluated_rules TEXT NOT NULL,
    authorization_token TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES proposals(id)
);

CREATE TABLE IF NOT EXISTS execution_receipts (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    authorization_token TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target TEXT NOT NULL,
    http_status INTEGER,
    raw_response_hash TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    cost_credits INTEGER NOT NULL,
    executed_at TEXT NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES proposals(id)
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    sequence_num INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    timestamp TEXT NOT NULL,
    transaction_id TEXT UNIQUE NOT NULL,
    from_account TEXT NOT NULL,
    to_account TEXT NOT NULL,
    amount INTEGER NOT NULL,
    memo TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    sequence_id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    previous_event_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_receipts (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    verified INTEGER NOT NULL,
    checks TEXT NOT NULL,
    failures TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (execution_id) REFERENCES execution_receipts(id)
);
"""

class Database:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)

    def close(self) -> None:
        if self.conn:
            self.conn.close()
