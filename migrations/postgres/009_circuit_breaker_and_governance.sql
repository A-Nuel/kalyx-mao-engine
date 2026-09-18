-- Migration 009: Emergency Circuit Breaker and Multi-Signature Admin Governance
CREATE TABLE IF NOT EXISTS system_circuit_breaker (
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'NORMAL',
    paused_by TEXT,
    paused_reason TEXT,
    paused_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, organisation_id)
);
CREATE INDEX IF NOT EXISTS idx_circuit_breaker_state ON system_circuit_breaker (state);

CREATE TABLE IF NOT EXISTS circuit_breaker_audit (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reason TEXT,
    state_before TEXT NOT NULL,
    state_after TEXT NOT NULL,
    approvals_json TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_circuit_breaker_audit_org ON circuit_breaker_audit (tenant_id, organisation_id);

CREATE TABLE IF NOT EXISTS admin_approvals (
    approval_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    approver_id TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    signature TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_admin_approvals_target ON admin_approvals (tenant_id, target_id, action_type);
CREATE INDEX IF NOT EXISTS idx_admin_approvals_hash ON admin_approvals (payload_hash);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('009_circuit_breaker_and_governance', NOW())
ON CONFLICT (version) DO NOTHING;
