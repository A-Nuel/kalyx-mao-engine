import sqlite3
import os

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS principals (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tenant_memberships (
    principal_id TEXT NOT NULL, tenant_id TEXT NOT NULL, role TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
    PRIMARY KEY (principal_id, tenant_id),
    FOREIGN KEY (principal_id) REFERENCES principals(id), FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);
CREATE TABLE IF NOT EXISTS organisations (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'tenant-demo', mission TEXT NOT NULL,
    treasury_balance INTEGER NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, role TEXT NOT NULL, model_name TEXT NOT NULL,
    credit_balance INTEGER NOT NULL, reputation_score REAL NOT NULL, authority_ceiling INTEGER NOT NULL,
    allowed_action_types TEXT NOT NULL, status TEXT NOT NULL, successful_tasks INTEGER NOT NULL,
    failed_tasks INTEGER NOT NULL, policy_violations INTEGER NOT NULL,
    performance_score REAL NOT NULL DEFAULT 100.0, risk_score REAL NOT NULL DEFAULT 0.0,
    resource_efficiency REAL NOT NULL DEFAULT 1.0, reliability_score REAL NOT NULL DEFAULT 100.0,
    task_history TEXT NOT NULL DEFAULT '[]', FOREIGN KEY (org_id) REFERENCES organisations(id)
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, assigned_agent_id TEXT, objective TEXT NOT NULL,
    allocated_credits INTEGER NOT NULL, status TEXT NOT NULL, output_evidence TEXT,
    created_at TEXT NOT NULL, FOREIGN KEY (org_id) REFERENCES organisations(id)
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, proposing_agent_id TEXT NOT NULL, action_type TEXT NOT NULL,
    target TEXT NOT NULL, parameters TEXT NOT NULL, requested_credits INTEGER NOT NULL,
    expected_value_score REAL NOT NULL, risk_assessment TEXT NOT NULL, rationale TEXT NOT NULL,
    content_hash TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY (task_id) REFERENCES tasks(id)
);
CREATE TABLE IF NOT EXISTS policy_decisions (
    id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, result TEXT NOT NULL, violated_rule_id TEXT,
    violated_rule_description TEXT, evaluated_rules TEXT NOT NULL, authorization_token TEXT,
    timestamp TEXT NOT NULL, FOREIGN KEY (proposal_id) REFERENCES proposals(id)
);
CREATE TABLE IF NOT EXISTS execution_receipts (
    id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, authorization_token TEXT NOT NULL,
    action_type TEXT NOT NULL, target TEXT NOT NULL, http_status INTEGER, raw_response_hash TEXT NOT NULL,
    raw_output TEXT NOT NULL, cost_credits INTEGER NOT NULL, executed_at TEXT NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES proposals(id)
);
CREATE TABLE IF NOT EXISTS ledger_entries (
    sequence_num INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, timestamp TEXT NOT NULL,
    transaction_id TEXT UNIQUE NOT NULL, from_account TEXT NOT NULL, to_account TEXT NOT NULL,
    amount INTEGER NOT NULL, memo TEXT NOT NULL, tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);
CREATE TABLE IF NOT EXISTS audit_events (
    sequence_id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, actor_id TEXT NOT NULL, event_type TEXT NOT NULL,
    entity_id TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
    previous_event_hash TEXT NOT NULL, event_hash TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo', organisation_id TEXT
);
CREATE TABLE IF NOT EXISTS verification_receipts (
    id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, verified INTEGER NOT NULL, checks TEXT NOT NULL,
    failures TEXT NOT NULL, evidence_hash TEXT NOT NULL, timestamp TEXT NOT NULL,
    FOREIGN KEY (execution_id) REFERENCES execution_receipts(id)
);
CREATE TABLE IF NOT EXISTS consequential_operations (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'tenant-demo', organisation_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL, decision_id TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
    action_type TEXT NOT NULL, target TEXT NOT NULL, parameters TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0, provider_name TEXT NOT NULL, provider_reference TEXT,
    state TEXT NOT NULL CHECK(state IN ('created','authorized','escrowed','submitted','succeeded','failed','unknown','reconciling','reconciled')),
    error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_consequential_ops_tenant ON consequential_operations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_consequential_ops_org ON consequential_operations(organisation_id);
CREATE INDEX IF NOT EXISTS idx_consequential_ops_state ON consequential_operations(state);

CREATE TABLE IF NOT EXISTS agent_performance_records (
    agent_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    tasks_failed INTEGER NOT NULL DEFAULT 0,
    missions_contributed INTEGER NOT NULL DEFAULT 0,
    successful_proposals INTEGER NOT NULL DEFAULT 0,
    rejected_proposals INTEGER NOT NULL DEFAULT 0,
    policy_violations INTEGER NOT NULL DEFAULT 0,
    resources_allocated INTEGER NOT NULL DEFAULT 0,
    resources_consumed INTEGER NOT NULL DEFAULT 0,
    value_produced REAL NOT NULL DEFAULT 0.0,
    unnecessary_actions INTEGER NOT NULL DEFAULT 0,
    execution_successes INTEGER NOT NULL DEFAULT 0,
    execution_failures INTEGER NOT NULL DEFAULT 0,
    recovery_successes INTEGER NOT NULL DEFAULT 0,
    recovery_failures INTEGER NOT NULL DEFAULT 0,
    performance_score REAL NOT NULL DEFAULT 100.0,
    reliability_score REAL NOT NULL DEFAULT 100.0,
    resource_efficiency_score REAL NOT NULL DEFAULT 1.0,
    policy_compliance_score REAL NOT NULL DEFAULT 100.0,
    composite_score REAL NOT NULL DEFAULT 100.0,
    reputation_score REAL NOT NULL DEFAULT 100.0,
    authority_level INTEGER NOT NULL DEFAULT 1,
    evaluation_count INTEGER NOT NULL DEFAULT 0,
    last_evaluated_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, organisation_id, agent_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (organisation_id) REFERENCES organisations(id)
);
CREATE INDEX IF NOT EXISTS idx_agent_perf_tenant_org ON agent_performance_records(tenant_id, organisation_id);

CREATE TABLE IF NOT EXISTS resource_allocations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    organisation_id TEXT NOT NULL,
    mission_id TEXT,
    strategy TEXT NOT NULL,
    treasury_available INTEGER NOT NULL,
    total_allocated INTEGER NOT NULL,
    allocations TEXT NOT NULL,
    authority_limits TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (organisation_id) REFERENCES organisations(id)
);
CREATE INDEX IF NOT EXISTS idx_allocations_tenant_org ON resource_allocations(tenant_id, organisation_id);

CREATE TABLE IF NOT EXISTS reputation_history (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    previous_score REAL NOT NULL,
    new_score REAL NOT NULL,
    score_delta REAL NOT NULL,
    trigger_event TEXT NOT NULL,
    evidence_hash TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (organisation_id) REFERENCES organisations(id)
);
CREATE INDEX IF NOT EXISTS idx_rep_hist_agent ON reputation_history(tenant_id, organisation_id, agent_id);

CREATE TABLE IF NOT EXISTS experiment_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    organisation_id TEXT NOT NULL,
    scenario TEXT NOT NULL,
    strategy TEXT NOT NULL,
    random_seed INTEGER NOT NULL,
    results_json TEXT NOT NULL,
    summary_analysis TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (organisation_id) REFERENCES organisations(id)
);
CREATE INDEX IF NOT EXISTS idx_experiments_tenant_org ON experiment_runs(tenant_id, organisation_id);

CREATE TABLE IF NOT EXISTS work_orders (
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    organisation_id TEXT NOT NULL,
    work_order_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    deliverable_type TEXT NOT NULL,
    required_orbio_credits INTEGER NOT NULL,
    bounty_amount INTEGER NOT NULL,
    bounty_asset TEXT NOT NULL DEFAULT 'USDG',
    deadline_seconds INTEGER NOT NULL DEFAULT 3600,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, organisation_id, work_order_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (organisation_id) REFERENCES organisations(id)
);
CREATE INDEX IF NOT EXISTS idx_work_orders_tenant_org ON work_orders (tenant_id, organisation_id);
CREATE INDEX IF NOT EXISTS idx_work_orders_status ON work_orders (tenant_id, organisation_id, status);

CREATE TABLE IF NOT EXISTS work_deliverables (
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    organisation_id TEXT NOT NULL,
    deliverable_id TEXT NOT NULL,
    work_order_id TEXT NOT NULL,
    producer_agent_id TEXT NOT NULL,
    content_payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    orbio_credits_consumed INTEGER NOT NULL,
    execution_telemetry_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, organisation_id, deliverable_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (organisation_id) REFERENCES organisations(id)
);
CREATE INDEX IF NOT EXISTS idx_work_deliverables_wo ON work_deliverables (tenant_id, organisation_id, work_order_id);

CREATE TABLE IF NOT EXISTS work_deliverable_receipts (
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    organisation_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    work_order_id TEXT NOT NULL,
    deliverable_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    verifier_identity TEXT NOT NULL,
    status TEXT NOT NULL,
    verification_notes TEXT NOT NULL DEFAULT '',
    verified_at TEXT NOT NULL,
    hmac_signature TEXT,
    PRIMARY KEY (tenant_id, organisation_id, receipt_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (organisation_id) REFERENCES organisations(id)
);
CREATE INDEX IF NOT EXISTS idx_work_receipts_wo ON work_deliverable_receipts (tenant_id, organisation_id, work_order_id);

CREATE TABLE IF NOT EXISTS revenue_events (
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    organisation_id TEXT NOT NULL,
    revenue_event_id TEXT NOT NULL,
    work_order_id TEXT NOT NULL,
    gross_revenue_usdg INTEGER NOT NULL,
    direct_expense_usdg INTEGER NOT NULL,
    net_surplus_usdg INTEGER NOT NULL,
    orbio_credits_consumed INTEGER NOT NULL,
    allocated_to_mission_budget INTEGER NOT NULL,
    allocated_to_reserve INTEGER NOT NULL,
    settled_at TEXT NOT NULL,
    ledger_tx_id TEXT,
    PRIMARY KEY (tenant_id, organisation_id, revenue_event_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (organisation_id) REFERENCES organisations(id)
);
CREATE INDEX IF NOT EXISTS idx_revenue_events_wo ON revenue_events (tenant_id, organisation_id, work_order_id);

CREATE TABLE IF NOT EXISTS mission_lineage (
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    organisation_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    parent_mission_id TEXT,
    funding_source TEXT NOT NULL,
    funding_amount_usdg INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, organisation_id, mission_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (organisation_id) REFERENCES organisations(id)
);
CREATE INDEX IF NOT EXISTS idx_mission_lineage_parent ON mission_lineage (tenant_id, organisation_id, parent_mission_id);
"""

class Database:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        if db_path != ":memory:": os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)
            self.conn.execute("INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))", ("tenant-demo", "Demo Workspace", "active"))
            self.conn.execute("INSERT OR IGNORE INTO principals (id, name, active, created_at) VALUES (?, ?, ?, datetime('now'))", ("principal-demo", "Demo Operator", 1))
            self.conn.execute("INSERT OR IGNORE INTO tenant_memberships (principal_id, tenant_id, role, active, created_at) VALUES (?, ?, ?, ?, datetime('now'))", ("principal-demo", "tenant-demo", "owner", 1))
            cur = self.conn.cursor()
            cur.execute("PRAGMA table_info(organisations)")
            org_columns = [row["name"] for row in cur.fetchall()]
            if "tenant_id" not in org_columns: self.conn.execute("ALTER TABLE organisations ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'tenant-demo'")
            cur.execute("PRAGMA table_info(ledger_entries)")
            ledger_columns = [row["name"] for row in cur.fetchall()]
            if "tenant_id" not in ledger_columns: self.conn.execute("ALTER TABLE ledger_entries ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'tenant-demo'")
            cur.execute("PRAGMA table_info(agents)")
            columns = [row["name"] for row in cur.fetchall()]
            for col_name, col_type in [("performance_score", "REAL NOT NULL DEFAULT 100.0"), ("risk_score", "REAL NOT NULL DEFAULT 0.0"), ("resource_efficiency", "REAL NOT NULL DEFAULT 1.0"), ("reliability_score", "REAL NOT NULL DEFAULT 100.0"), ("task_history", "TEXT NOT NULL DEFAULT '[]'")]:
                if col_name not in columns: self.conn.execute(f"ALTER TABLE agents ADD COLUMN {col_name} {col_type}")
            cur.execute("PRAGMA table_info(audit_events)")
            audit_columns = [row["name"] for row in cur.fetchall()]
            if "tenant_id" not in audit_columns: self.conn.execute("ALTER TABLE audit_events ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'tenant-demo'")
            if "organisation_id" not in audit_columns: self.conn.execute("ALTER TABLE audit_events ADD COLUMN organisation_id TEXT")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant_org ON audit_events(tenant_id, organisation_id)")
            self.conn.execute("UPDATE organisations SET tenant_id = 'tenant-demo' WHERE tenant_id IS NULL OR tenant_id = ''")
            self.conn.execute("UPDATE ledger_entries SET tenant_id = 'tenant-demo' WHERE tenant_id IS NULL OR tenant_id = ''")
            self.conn.execute("UPDATE audit_events SET tenant_id = 'tenant-demo' WHERE tenant_id IS NULL OR tenant_id = ''")

    def close(self) -> None:
        if self.conn: self.conn.close()
