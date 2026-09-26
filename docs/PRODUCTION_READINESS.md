# Kalyx Production Readiness Pass

**Review date:** 2026-09-26  
**Scope:** standalone deployment readiness after Phase 21D–21H

This document separates **deployable software infrastructure** from **live consequential economic execution**. A green CI run is required before merging the Phase 21D–21H branch.

## Gate status

| Gate | Status | Notes |
|---|---|---|
| Automated CI | IN PROGRESS | Must be green on the final branch head before merge. |
| Python 3.11 / 3.12 | REQUIRED | Matrix is enforced by GitHub Actions. |
| Production container build | REQUIRED | CI builds the production image on Python 3.12. |
| PostgreSQL integration | REQUIRED | Production persistence path must pass its integration suite. |
| Production startup fail-closed | IMPLEMENTED | Production requires PostgreSQL, non-demo policy secret, operator key, and production identity authentication. |
| Health endpoint | IMPLEMENTED | `/api/health` is available and used by the container healthcheck. |
| Rate/body limits | IMPLEMENTED | Security middleware enforces configured request limits. |
| CORS | CONFIGURED | Production should set explicit browser origins; do not use wildcard CORS. |
| Tenant/organisation isolation | IMPLEMENTED + TESTED | Phase 21B/21C and Phase 21D–21H tests bind execution context and approvals to scope. |
| Execution authority | IMPLEMENTED | `ExecutionAuthority` is organisation-bound and delegates cryptographic signing to the existing signer boundary. |
| Wallet identity boundary | IMPLEMENTED | Public metadata only; no seed phrase/private key field. |
| Durable execution approval | IMPLEMENTED | HMAC-bound, expiring, durable, and atomically single-use. |
| Secrets in agent/org data | PROHIBITED | Credentials remain deployment/provider concerns, not agent authority. |
| Real EVM signing boundary | IMPLEMENTED | External signer path avoids moving a private key into Kalyx. |
| Orbio CREDIT activation | CODE-PATH READY / NOT BROADCAST | Mainnet preflight passed; no activation transaction has been broadcast through Kalyx. |
| Real Orbio economic settlement | NOT YET VERIFIED | Do not describe the project as having completed live Orbio economic settlement. |
| Disaster recovery | DOCUMENTED | PostgreSQL backup/recovery procedures exist; deployment operator must configure actual backups. |
| Observability | IMPLEMENTED | Structured logging and correlation/operational controls exist; production operator must connect logs/alerts to an actual service. |

## Required Render configuration

Phase 21D–21H introduces **no new mandatory environment variable**.

A production Render deployment should provide the existing production configuration, including:

- `KALYX_ENV=production`
- `KALYX_DATABASE_URL=<PostgreSQL URL>`
- `KALYX_POLICY_SECRET=<unique high-entropy secret>`
- `KALYX_OPERATOR_KEY=<strong operator secret>`
- `KALYX_IDENTITY_AUTH=production`
- `KALYX_REQUIRE_OPERATOR_AUTH=true`
- `KALYX_CORS_ORIGINS=<actual frontend origins>`
- `KALYX_RECEIPT_SECRET_KEY=<unique high-entropy secret>`

Only enable provider-specific credentials when that provider is intentionally being used.

### Wallet security

Do **not** place a Robinhood Wallet seed phrase or private key in Render merely to support Phase 21D–21H. The current external signer boundary exists specifically so wallet custody can remain outside the Kalyx application process.

## Live execution boundary

The current evidence supports these statements:

1. Kalyx has real EVM transaction infrastructure and receipt verification.
2. A Sepolia transaction was broadcast and independently reconciled as a successful blockchain infrastructure smoke test.
3. The Orbio CREDIT activation path has a hardened intent, policy, preflight, verifier, runtime, and external-signer boundary.
4. Orbio mainnet preflight confirmed the live CREDIT contract, wallet balance, activation preview, fee, nonce, gas estimate, and calldata.
5. No Orbio CREDIT activation transaction has been broadcast through Kalyx.

Therefore the correct release description is:

> **Kalyx is a deployable governed autonomous-organisation control plane with hardened real-execution boundaries. Live consequential execution is opt-in and provider-specific; Orbio economic settlement remains an explicitly unverified next execution step.**

## Merge gate

Do not merge Phase 21D–21H until the **final branch head** has a green CI run covering:

- Python 3.11
- Python 3.12
- SQLite suite
- PostgreSQL integration suite
- production container build
- dependency consistency
- JavaScript syntax
- Python compilation

After merge, perform a deployment smoke test against the actual Render service:

1. application starts without fail-open configuration;
2. `/api/health` reports healthy;
3. unauthenticated protected mutation is rejected;
4. valid production identity can access its permitted scope;
5. cross-organisation access is rejected;
6. database migrations complete cleanly;
7. logs contain no secrets;
8. demo/simulation endpoints are disabled unless deliberately enabled.

A successful deployment smoke test should be recorded separately from CI; CI cannot prove the correctness of a particular Render environment.
