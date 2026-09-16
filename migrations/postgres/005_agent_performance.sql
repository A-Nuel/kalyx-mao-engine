-- Migration 005: Agent Performance Records, Resource Allocations, and Experiments
BEGIN;

CREATE TABLE IF NOT EXISTS agent_performance_records (
    agent_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    tasks_failed INTEGER NOT NULL DEFAULT 0,
    missions_contributed INTEGER NOT NULL DEFAULT 0,
    successful_proposals INTEGER NOT NULL DEFAULT 0,
    rejected_proposals INTEGER NOT NULL DEFAULT 0,
    policy_violations INTEGER NOT NULL DEFAULT 0,
    resources_allocated INTEGER NOT NULL DEFAULT 0,
    resources_consumed INTEGER NOT NULL DEFAULT 0,
    value_produced DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    unnecessary_actions INTEGER NOT NULL DEFAULT 0,
    execution_successes INTEGER NOT NULL DEFAULT 0,
    execution_failures INTEGER NOT NULL DEFAULT 0,
    recovery_successes INTEGER NOT NULL DEFAULT 0,
    recovery_failures INTEGER NOT NULL DEFAULT 0,
    performance_score DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    reliability_score DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    resource_efficiency_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    policy_compliance_score DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    composite_score DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    reputation_score DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    authority_level INTEGER NOT NULL DEFAULT 1,
    evaluation_count INTEGER NOT NULL DEFAULT 0,
    last_evaluated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, organisation_id, agent_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (organisation_id) REFERENCES organisations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_perf_tenant_org ON agent_performance_records (tenant_id, organisation_id);

CREATE TABLE IF NOT EXISTS resource_allocations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    organisation_id TEXT NOT NULL,
    mission_id TEXT,
    strategy TEXT NOT NULL,
    treasury_available INTEGER NOT NULL,
    total_allocated INTEGER NOT NULL,
    allocations JSONB NOT NULL,
    authority_limits JSONB NOT NULL,
    rationale TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (organisation_id) REFERENCES organisations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_allocations_tenant_org ON resource_allocations (tenant_id, organisation_id);

CREATE TABLE IF NOT EXISTS reputation_history (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    previous_score DOUBLE PRECISION NOT NULL,
    new_score DOUBLE PRECISION NOT NULL,
    score_delta DOUBLE PRECISION NOT NULL,
    trigger_event TEXT NOT NULL,
    evidence_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (organisation_id) REFERENCES organisations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_rep_hist_agent ON reputation_history (tenant_id, organisation_id, agent_id);

CREATE TABLE IF NOT EXISTS experiment_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'tenant-demo',
    organisation_id TEXT NOT NULL,
    scenario TEXT NOT NULL,
    strategy TEXT NOT NULL,
    random_seed INTEGER NOT NULL,
    results_json JSONB NOT NULL,
    summary_analysis TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (organisation_id) REFERENCES organisations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_experiments_tenant_org ON experiment_runs (tenant_id, organisation_id);

INSERT INTO schema_migrations (version) VALUES ('005_agent_performance')
ON CONFLICT (version) DO NOTHING;

COMMIT;
