-- Consequential operations durable state tracking
BEGIN;

CREATE TABLE IF NOT EXISTS consequential_operations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    action_type TEXT NOT NULL,
    target TEXT NOT NULL,
    parameters TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    provider_name TEXT NOT NULL,
    provider_reference TEXT,
    state TEXT NOT NULL CHECK (state IN ('created', 'authorized', 'escrowed', 'submitted', 'succeeded', 'failed', 'unknown', 'reconciling', 'reconciled')),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_consequential_ops_tenant ON consequential_operations (tenant_id);
CREATE INDEX IF NOT EXISTS idx_consequential_ops_org ON consequential_operations (organisation_id);
CREATE INDEX IF NOT EXISTS idx_consequential_ops_state ON consequential_operations (state);

INSERT INTO schema_migrations (version) VALUES ('003_consequential_operations')
ON CONFLICT (version) DO NOTHING;

COMMIT;
