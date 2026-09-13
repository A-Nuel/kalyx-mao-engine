# Phase 9.5 FINAL — Security & Persistence Gate

## Status

Phase 9 (identity/tenant/org isolation) and Phase 9.5 (dual-backend persistence) are closed with the following production fail-closed hardening.

## What was fixed in this gate

1. **Single production persistence entry** — API and missions resolve DB via `create_database()` / `create_sqlite()` for explicit test paths.
2. **Production fail-closed** — `src/api/bootstrap.py` requires PostgreSQL URL, non-demo policy secret, operator key, and identity auth when `KALYX_ENV=production`.
3. **Durable authorization consumption** — `authorization_token_consumptions` journal keyed by token nonce; replay after restart fails.
4. **No ghost agents** — `NoEligibleAgentException` fails the task; synthetic `agent-{role}` IDs removed.
5. **ESCALATE_TO_HUMAN** — pauses organisation, audits `ESCALATION_REQUIRED`, spends nothing, does not raise `NotImplementedError`.
6. **Canonical account namespace** — `AccountNamespace` derives physical accounts from tenant + org + logical name.

## What production still is NOT

Kalyx is **not** production-ready for real-money custody.

Still deferred to Phase 10+:

- Real execution / settlement adapters
- Provider reconciliation against live external systems
- Full dual-backend port of every repository path
- PostgreSQL RLS enablement
- Enterprise identity (OIDC/OAuth)
- Rate limiting / body size enforcement at gateway scale
- Non-root Docker (optional P2)

## Production configuration checklist

```bash
KALYX_ENV=production
KALYX_DATABASE_URL=postgresql://...
KALYX_POLICY_SECRET=<unique non-demo secret>
KALYX_OPERATOR_KEY=<strong random>
KALYX_IDENTITY_AUTH=true
```

## Core invariants (unchanged)

> Agents propose. Policies authorize. Executors execute. Auditors verify.

> No tenant can read, spend, authorize, or audit another tenant's resources.
