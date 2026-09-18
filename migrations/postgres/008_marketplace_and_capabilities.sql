-- Migration 008: Marketplace Orders, Escrow Agreements, and Dynamic Agent Capability Grants
CREATE TABLE IF NOT EXISTS marketplace_orders (
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    specification_hash TEXT NOT NULL,
    required_capability TEXT NOT NULL,
    bounty_amount INTEGER NOT NULL,
    bounty_asset TEXT NOT NULL DEFAULT 'USDG',
    sla_timeout_seconds INTEGER NOT NULL DEFAULT 3600,
    status TEXT NOT NULL DEFAULT 'OPEN',
    claimed_by_tenant_id TEXT,
    claimed_by_org_id TEXT,
    claimed_by_agent_id TEXT,
    work_order_id TEXT,
    deliverable_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, organisation_id, order_id)
);
CREATE INDEX IF NOT EXISTS idx_mkt_orders_status ON marketplace_orders (status);
CREATE INDEX IF NOT EXISTS idx_mkt_orders_claimed ON marketplace_orders (claimed_by_tenant_id, claimed_by_org_id);

CREATE TABLE IF NOT EXISTS marketplace_escrows (
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    escrow_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    client_tenant_id TEXT NOT NULL,
    client_org_id TEXT NOT NULL,
    provider_tenant_id TEXT,
    provider_org_id TEXT,
    bounty_amount INTEGER NOT NULL,
    bounty_asset TEXT NOT NULL DEFAULT 'USDG',
    status TEXT NOT NULL DEFAULT 'HELD',
    client_ledger_tx_id TEXT,
    provider_ledger_tx_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, organisation_id, escrow_id)
);
CREATE INDEX IF NOT EXISTS idx_mkt_escrow_order ON marketplace_escrows (order_id);
CREATE INDEX IF NOT EXISTS idx_mkt_escrow_status ON marketplace_escrows (status);

CREATE TABLE IF NOT EXISTS agent_capability_grants (
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    capability_name TEXT NOT NULL,
    permission_level TEXT NOT NULL,
    trigger_performance_score NUMERIC(5, 4) NOT NULL,
    granted_by_policy_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, organisation_id, grant_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_cap_grants ON agent_capability_grants (tenant_id, organisation_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_cap_status ON agent_capability_grants (status);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('008_marketplace_and_capabilities', NOW())
ON CONFLICT (version) DO NOTHING;
