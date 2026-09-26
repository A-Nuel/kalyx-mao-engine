-- Phase 22 Product Plane
-- User/workspace onboarding, agent credentials, provider connections and configurable policy profiles.

CREATE TABLE IF NOT EXISTS product_users (
    id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL UNIQUE,
    email TEXT,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS product_identities (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES product_users(id),
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(kind, subject)
);

CREATE TABLE IF NOT EXISTS wallet_challenges (
    id TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    message TEXT NOT NULL,
    nonce TEXT NOT NULL UNIQUE,
    expires_at DOUBLE PRECISION NOT NULL,
    consumed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS product_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES product_users(id),
    principal_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at DOUBLE PRECISION NOT NULL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_credentials (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    organisation_id TEXT NOT NULL REFERENCES organisations(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    scopes_json TEXT NOT NULL,
    expires_at DOUBLE PRECISION,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_credentials_scope
    ON agent_credentials(tenant_id, organisation_id, agent_id);

CREATE TABLE IF NOT EXISTS provider_connections (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    organisation_id TEXT NOT NULL REFERENCES organisations(id),
    provider TEXT NOT NULL,
    connection_type TEXT NOT NULL,
    secret_ciphertext TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_connections_scope
    ON provider_connections(tenant_id, organisation_id, provider);

CREATE TABLE IF NOT EXISTS organisation_policies (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    organisation_id TEXT NOT NULL REFERENCES organisations(id),
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    config_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_policies_scope
    ON organisation_policies(tenant_id, organisation_id, enabled);
