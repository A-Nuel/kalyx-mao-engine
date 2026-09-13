# Kalyx Persistence — SQLite + PostgreSQL

## Decision

| Environment | Backend |
|---|---|
| Local development | SQLite |
| Unit / deterministic tests | SQLite (`:memory:` or temp file) |
| Judge / demo | SQLite |
| Production | PostgreSQL |

SQLite is **not** removed. It remains the fast, deterministic path.

## Configuration

```bash
# Local / demo (default)
KALYX_DB=data/kalyx.db

# Production
KALYX_ENV=production
KALYX_DATABASE_URL=postgresql://user:pass@host:5432/kalyx
KALYX_DB_POOL_MIN=1
KALYX_DB_POOL_MAX=10
```

`KALYX_ENV=production` **fails closed** if `KALYX_DATABASE_URL` is missing or not a postgres URL. There is no silent SQLite fallback in production.

## Boundary

```text
Domain / Application
        |
  create_database()   (src/persistence/factory.py)
        |
   +----+----+
   |         |
 SQLite   PostgreSQL
```

Existing repositories still use a connection-centric API. The Postgres adapter provides a transitional `.conn` proxy that accepts `?` placeholders and returns dict-like rows so migration can be incremental.

## Schema & migrations

- SQLite: `Database._init_schema()` (CREATE IF NOT EXISTS + ad-hoc ALTER) — unchanged for local/demo.
- PostgreSQL: ordered SQL files under `migrations/postgres/` applied by `src/persistence/migrate.py`.

First migration: `001_initial.sql` covers tenants, principals, memberships, organisations, agents, tasks, proposals, policy decisions, execution receipts, ledger entries, audit events, verification receipts, and idempotency operations.

## Tenant / organisation isolation

**Primary boundary remains application authorization** (authenticated principal → membership → tenant → organisation). Client `X-Tenant-ID` is never proof of identity.

Database defence-in-depth:

- Explicit `tenant_id` on organisations and ledger_entries.
- Indexes on `tenant_id` / `org_id` for scoped queries.
- Schema is RLS-ready (columns present). Full Row-Level Security is **deferred** because enabling RLS without every connection setting a trusted `app.current_tenant` would break legitimate access. When RLS is introduced, set the session variable from the authenticated membership context only.

## Ledger concurrency (PostgreSQL)

Balances are derived from append-only `ledger_entries` (same model as SQLite).

Spend path:

1. `BEGIN`
2. `pg_advisory_xact_lock(hashtext(from_account))` — serialize concurrent spends on the same account
3. Reject duplicate `transaction_id`
4. Compute balance; reject if insufficient
5. `INSERT` ledger entry
6. `COMMIT`

This preserves: no overdraft, atomic transfer, unique transaction IDs, conservation.

## Idempotency

`idempotency_operations` exists in the Postgres schema with unique `operation_key`. Concurrent claim of the same key is rejected by the primary key. Application logic still binds fingerprint + state transitions.

## Audit

The cryptographic hash chain is application-level. PostgreSQL stores the same fields (`sequence_id`, hashes, payload). Ordering is preserved via primary key on `sequence_id`. Postgres does **not** make the audit cryptographically trustworthy by itself.

## What PostgreSQL does NOT solve

- Authorization correctness (still application + membership)
- Agent action safety
- External execution idempotency without provider cooperation
- Cryptographic audit integrity (still hash-chain verification)
- Secret management
- Real-money settlement readiness

## Indexes (why)

| Index | Reason |
|---|---|
| `idx_organisations_tenant` | List orgs by tenant |
| `idx_agents_org` / `idx_tasks_org` | Org-scoped reads |
| `idx_ledger_from` / `idx_ledger_to` | Balance derivation |
| `idx_ledger_tenant` | Tenant-scoped ledger scans |
| `idx_memberships_tenant` | Active membership lookups |
| `idx_audit_entity` | Org-filtered audit |

## Threat model notes

- Connection pool exhaustion: bound `KALYX_DB_POOL_MAX`.
- Migration risk: migrations are ordered and recorded in `schema_migrations`; test on a disposable database before production apply.
- Backup/recovery: operator responsibility; ledger and audit are append-oriented and should be backed up together.
- SQL injection: parameterized queries only; placeholder translation in the proxy must remain strict.
