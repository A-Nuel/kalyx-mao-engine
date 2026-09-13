-- Kalyx PostgreSQL initial schema
-- Mirrors the SQLite domain model with production constraints and indexes.
-- Application authorization remains the primary tenant boundary;
-- tenant_id columns and indexes make future RLS adoption straightforward.

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS principals (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_memberships (
    principal_id TEXT NOT NULL REFERENCES principals(id),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    role TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (principal_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_memberships_tenant ON tenant_memberships (tenant_id) WHERE active = TRUE;

CREATE TABLE IF NOT EXISTS organisations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    mission TEXT NOT NULL,
    treasury_balance INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_organisations_tenant ON organisations (tenant_id);
CREATE INDEX IF NOT EXISTS idx_organisations_tenant_created ON organisations (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organisations(id),
    role TEXT NOT NULL,
    model_name TEXT NOT NULL,
    credit_balance INTEGER NOT NULL DEFAULT 0,
    reputation_score DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    authority_ceiling INTEGER NOT NULL DEFAULT 25,
    allowed_action_types TEXT NOT NULL,
    status TEXT NOT NULL,
    successful_tasks INTEGER NOT NULL DEFAULT 0,
    failed_tasks INTEGER NOT NULL DEFAULT 0,
    policy_violations INTEGER NOT NULL DEFAULT 0,
    performance_score DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    risk_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    resource_efficiency DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    reliability_score DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    task_history TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_agents_org ON agents (org_id);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organisations(id),
    assigned_agent_id TEXT,
    objective TEXT NOT NULL,
    allocated_credits INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    output_evidence TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tasks_org ON tasks (org_id);
CREATE INDEX IF NOT EXISTS idx_tasks_org_created ON tasks (org_id, created_at DESC);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    proposing_agent_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target TEXT NOT NULL,
    parameters TEXT NOT NULL,
    requested_credits INTEGER NOT NULL DEFAULT 0,
    expected_value_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    risk_assessment TEXT NOT NULL,
    rationale TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_proposals_task ON proposals (task_id);

CREATE TABLE IF NOT EXISTS policy_decisions (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    result TEXT NOT NULL,
    violated_rule_id TEXT,
    violated_rule_description TEXT,
    evaluated_rules TEXT NOT NULL,
    authorization_token TEXT,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_policy_decisions_proposal ON policy_decisions (proposal_id);

CREATE TABLE IF NOT EXISTS execution_receipts (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    authorization_token TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target TEXT NOT NULL,
    http_status INTEGER,
    raw_response_hash TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    cost_credits INTEGER NOT NULL DEFAULT 0,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_execution_receipts_proposal ON execution_receipts (proposal_id);

CREATE TABLE IF NOT EXISTS ledger_entries (
    sequence_num BIGSERIAL PRIMARY KEY,
    id TEXT UNIQUE NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    transaction_id TEXT UNIQUE NOT NULL,
    from_account TEXT NOT NULL,
    to_account TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    memo TEXT NOT NULL,
    tenant_id TEXT NOT NULL REFERENCES tenants(id)
);

CREATE INDEX IF NOT EXISTS idx_ledger_from ON ledger_entries (from_account);
CREATE INDEX IF NOT EXISTS idx_ledger_to ON ledger_entries (to_account);
CREATE INDEX IF NOT EXISTS idx_ledger_tenant ON ledger_entries (tenant_id);
CREATE INDEX IF NOT EXISTS idx_ledger_tenant_accounts ON ledger_entries (tenant_id, from_account, to_account);

CREATE TABLE IF NOT EXISTS audit_events (
    sequence_id BIGINT PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    actor_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    previous_event_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events (entity_id);

CREATE TABLE IF NOT EXISTS verification_receipts (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES execution_receipts(id),
    verified BOOLEAN NOT NULL,
    checks TEXT NOT NULL,
    failures TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_verification_execution ON verification_receipts (execution_id);

CREATE TABLE IF NOT EXISTS idempotency_operations (
    operation_key TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('started', 'succeeded', 'failed')),
    receipt_id TEXT,
    tenant_id TEXT,
    organisation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_idempotency_tenant ON idempotency_operations (tenant_id);

-- Seed demo tenant only if absent (safe on repeated migrate).
INSERT INTO tenants (id, name, status)
VALUES ('tenant-demo', 'Demo Workspace', 'active')
ON CONFLICT (id) DO NOTHING;

INSERT INTO principals (id, name, active)
VALUES ('principal-demo', 'Demo Operator', TRUE)
ON CONFLICT (id) DO NOTHING;

INSERT INTO tenant_memberships (principal_id, tenant_id, role, active)
VALUES ('principal-demo', 'tenant-demo', 'owner', TRUE)
ON CONFLICT (principal_id, tenant_id) DO NOTHING;

INSERT INTO schema_migrations (version) VALUES ('001_initial')
ON CONFLICT (version) DO NOTHING;

COMMIT;
