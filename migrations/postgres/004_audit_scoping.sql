-- Migration 004: Audit events tenant and organisation scoping
BEGIN;

ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'tenant-demo';
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS organisation_id TEXT;

CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_events (tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_events (organisation_id);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_org ON audit_events (tenant_id, organisation_id);

INSERT INTO schema_migrations (version) VALUES ('004_audit_scoping')
ON CONFLICT (version) DO NOTHING;

COMMIT;
