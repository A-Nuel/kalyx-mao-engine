-- Phase 14A: external provider key metadata (no plaintext secrets)
CREATE TABLE IF NOT EXISTS external_keys (
    key_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    status TEXT NOT NULL,
    account_id TEXT,
    evidence_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    rate_limit_json TEXT,
    PRIMARY KEY (tenant_id, organisation_id, key_id)
);

CREATE INDEX IF NOT EXISTS idx_external_keys_tenant_org ON external_keys (tenant_id, organisation_id);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('006_external_keys', NOW())
ON CONFLICT (version) DO NOTHING;
