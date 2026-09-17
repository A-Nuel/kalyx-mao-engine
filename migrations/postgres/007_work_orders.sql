-- Phase 16: Work orders, deliverables, receipts, revenue events, and mission lineage
CREATE TABLE IF NOT EXISTS work_orders (
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    work_order_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    deliverable_type TEXT NOT NULL,
    required_orbio_credits BIGINT NOT NULL,
    bounty_amount INTEGER NOT NULL,
    bounty_asset TEXT NOT NULL DEFAULT 'USDG',
    deadline_seconds INTEGER NOT NULL DEFAULT 3600,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, organisation_id, work_order_id)
);
CREATE INDEX IF NOT EXISTS idx_work_orders_tenant_org ON work_orders (tenant_id, organisation_id);
CREATE INDEX IF NOT EXISTS idx_work_orders_status ON work_orders (tenant_id, organisation_id, status);

CREATE TABLE IF NOT EXISTS work_deliverables (
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    deliverable_id TEXT NOT NULL,
    work_order_id TEXT NOT NULL,
    producer_agent_id TEXT NOT NULL,
    content_payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    orbio_credits_consumed BIGINT NOT NULL,
    execution_telemetry_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, organisation_id, deliverable_id)
);
CREATE INDEX IF NOT EXISTS idx_work_deliverables_wo ON work_deliverables (tenant_id, organisation_id, work_order_id);

CREATE TABLE IF NOT EXISTS work_deliverable_receipts (
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    work_order_id TEXT NOT NULL,
    deliverable_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    verifier_identity TEXT NOT NULL,
    status TEXT NOT NULL,
    verification_notes TEXT NOT NULL DEFAULT '',
    verified_at TIMESTAMPTZ NOT NULL,
    hmac_signature TEXT,
    PRIMARY KEY (tenant_id, organisation_id, receipt_id)
);
CREATE INDEX IF NOT EXISTS idx_work_receipts_wo ON work_deliverable_receipts (tenant_id, organisation_id, work_order_id);

CREATE TABLE IF NOT EXISTS revenue_events (
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    revenue_event_id TEXT NOT NULL,
    work_order_id TEXT NOT NULL,
    gross_revenue_usdg INTEGER NOT NULL,
    direct_expense_usdg INTEGER NOT NULL,
    net_surplus_usdg INTEGER NOT NULL,
    orbio_credits_consumed BIGINT NOT NULL,
    allocated_to_mission_budget INTEGER NOT NULL,
    allocated_to_reserve INTEGER NOT NULL,
    settled_at TIMESTAMPTZ NOT NULL,
    ledger_tx_id TEXT,
    PRIMARY KEY (tenant_id, organisation_id, revenue_event_id)
);
CREATE INDEX IF NOT EXISTS idx_revenue_events_wo ON revenue_events (tenant_id, organisation_id, work_order_id);

CREATE TABLE IF NOT EXISTS mission_lineage (
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    parent_mission_id TEXT,
    funding_source TEXT NOT NULL,
    funding_amount_usdg INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, organisation_id, mission_id)
);
CREATE INDEX IF NOT EXISTS idx_mission_lineage_parent ON mission_lineage (tenant_id, organisation_id, parent_mission_id);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('007_work_orders', NOW())
ON CONFLICT (version) DO NOTHING;
