-- Durable single-use authorization token consumption
BEGIN;

CREATE TABLE IF NOT EXISTS authorization_token_consumptions (
    nonce TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    proposal_content_hash TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    policy_version_hash TEXT NOT NULL,
    token_fingerprint TEXT NOT NULL,
    consumed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_token_consumptions_org ON authorization_token_consumptions (org_id);
CREATE INDEX IF NOT EXISTS idx_token_consumptions_proposal ON authorization_token_consumptions (proposal_id);

INSERT INTO schema_migrations (version) VALUES ('002_authorization_tokens')
ON CONFLICT (version) DO NOTHING;

COMMIT;
