-- Orbio OAuth 2.1 + PKCE connection state
-- Stores only encrypted PKCE verifiers and OAuth connection state.

CREATE TABLE IF NOT EXISTS orbio_oauth_states (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES product_users(id),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    organisation_id TEXT NOT NULL REFERENCES organisations(id),
    state_hash TEXT NOT NULL UNIQUE,
    code_verifier_ciphertext TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    consumed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orbio_oauth_states_expiry
    ON orbio_oauth_states(expires_at, consumed);
