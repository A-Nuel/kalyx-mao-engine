# Kalyx MAO Engine — Architecture Reference

This document provides the authoritative architectural specification for the Kalyx Machine Autonomous Organisation (MAO) Engine. It details the system invariants, execution pipelines, data boundaries, and persistence models.

---

## 1. Core System Invariant

The fundamental architectural invariant of Kalyx is strict separation of duties:

```text
AGENTS PROPOSE ──► POLICIES AUTHORIZE ──► EXECUTORS EXECUTE ──► AUDITORS VERIFY ──► LEDGER SETTLES
```

AI agents have **zero autonomous execution authority**. No agent or model can:
- Self-grant capability tokens or permissions
- Release escrows or mutate treasury balances directly
- Execute consequential external actions without policy authorization
- Self-verify deliverables or outcomes

---

## 2. Governed Lifecycle & Pipeline

```text
  ┌─────────────┐
  │  AI Agent   │
  └──────┬──────┘
         │ 1. Submits Proposal (ActionProposal)
         ▼
  ┌──────────────────────────────────────────────────┐
  │ Policy Engine                                    │
  │ • Validates spend limits, allowlists, risk tiers │
  │ • Evaluates human approval requirements          │
  └──────┬───────────────────────────────────────────┘
         │ 2. Issues HMAC Capability Token (time-bound, single-use)
         ▼
  ┌──────────────────────────────────────────────────┐
  │ Double-Entry Ledger                              │
  │ • Reserves funds in ESCROW                       │
  │ • Enforces conservation invariants               │
  └──────┬───────────────────────────────────────────┘
         │ 3. Dispatches authorized operation
         ▼
  ┌──────────────────────────────────────────────────┐
  │ Controlled Executor                              │
  │ • Consumes capability token atomically          │
  │ • Network egress gate (SSRF & target allowlist)  │
  │ • External API / Blockchain transaction dispatch │
  └──────┬───────────────────────────────────────────┘
         │ 4. Produces ExecutionReceipt & deliverable
         ▼
  ┌──────────────────────────────────────────────────┐
  │ Independent Auditor / Verifier                   │
  │ • Evaluates proof, deliverable, or remote state  │
  │ • Validates cryptographic receipts               │
  └──────┬───────────────────────────────────────────┘
         │ 5. Reports verification verdict
         ▼
  ┌──────────────────────────────────────────────────┐
  │ Settlement & Audit                               │
  │ • Ledger commits ESCROW -> SINK (or refunds)     │
  │ • Appends SHA-256 event to tamper-evident chain   │
  │ • Updates agent reputation & performance scores  │
  └──────────────────────────────────────────────────┘
```

### 2.1 Propose
An agent or coordinator submits an `ActionProposal` specifying the target action, arguments, estimated budget, and rationale.

### 2.2 Authorize
The `PolicyEngine` deterministically validates the proposal against organisation policies:
- Maximum spend per action, per mission, and per rolling window.
- Permitted target domains and API allowlists.
- Human-in-the-loop approval thresholds for high-risk operations.
- Outputs an HMAC-SHA256 capability token bound to the exact action fingerprint, organisation ID, and expiry window.

### 2.3 Escrow
Prior to external dispatch, the `DoubleEntryLedger` locks required funds in an escrow account, ensuring no overdraft or double-spending can occur.

### 2.4 Execute
A specialized `Executor` consumes the capability token. The `TokenConsumptionStore` atomically invalidates the token to prevent replay attacks. External requests pass through egress firewalls enforcing strict SSRF protections.

### 2.5 Verify
The outcome is independently evaluated by the `Auditor` / `VerificationEngine`. Verification checks receipts, response signatures, external blockchain receipts, or deliverable artifacts against acceptance criteria.

### 2.6 Settle & Audit
Upon successful verification, escrowed funds settle to external sinks or vendor accounts. If verification fails or times out, escrow is refunded to the treasury. All state transitions append an immutable event to the SHA-256 cryptographic audit hash chain.

---

## 3. Security Boundaries & Execution Authority

### 3.1 Tenant and Organisation Isolation
Kalyx enforces multi-tenant boundaries at the application layer:
- Every entity (organisations, agents, tasks, proposals, ledger entries) is scoped by `tenant_id` and `organisation_id`.
- Memberships map authenticated principals to tenant roles (`owner`, `admin`, `operator`, `viewer`).
- Client-supplied `X-Tenant-ID` headers are validated against authenticated principal memberships and are never trusted as identity proof.

### 3.2 Wallet Identity & Custody Boundary
- `WalletIdentity` models store strictly **public metadata** (wallet address, network, chain ID, verification timestamp).
- **Zero custody**: Private keys, seed phrases, and mnemonics are never accepted, stored, or processed by Kalyx.
- Transaction signing is delegated to external signers (e.g., browser-injected EIP-1193 providers or external signing services).
- Kalyx constructs, preflights, and verifies transactions; the user or external authority signs them.

### 3.3 Authoritative Network Alignment
To prevent cross-chain replay or network mismatch:
- The UI and API explicitly display and enforce the target chain ID (e.g., Robinhood Mainnet: `4663`, Sepolia: `11155111`).
- Transactions are preflighted against the target RPC; if the connected wallet chain does not match the configured chain, signing is blocked.

### 3.4 Egress Control & SSRF Protection
- Outbound HTTP requests from agent actions or executors pass through URL validation that resolves DNS and blocks internal/private IP ranges (RFC 1918, link-local, loopback, cloud metadata endpoints).
- Only domains registered on the organisation target allowlist are accessible.

---

## 4. Persistence Architecture

Kalyx implements a dual-datastore persistence model managed via `src/persistence/factory.py`:

```text
Domain & Application Logic
           │
     create_database()
           │
    ┌──────┴──────┐
    ▼             ▼
  SQLite      PostgreSQL
 (Dev/Test)   (Production)
```

| Environment | Datastore | Strategy |
|---|---|---|
| Local development | SQLite (`data/kalyx.db`) | Fast setup, file-backed |
| Unit / Deterministic tests | SQLite (`:memory:` or temporary file) | Deterministic, isolated per test run |
| Production (`KALYX_ENV=production`) | PostgreSQL | High-concurrency, connection pooling, ACID |

### 4.1 Production Fail-Closed Policy
When `KALYX_ENV=production`:
- The application **fails closed** at startup if `KALYX_DATABASE_URL` is unset or does not reference PostgreSQL.
- There is no silent fallback to SQLite in production.
- Production requires a non-default `KALYX_POLICY_SECRET`, `KALYX_OPERATOR_KEY`, and production identity authentication.

### 4.2 Schema Migrations
- **SQLite**: Local/test schema initialized via `Database._init_schema()`.
- **PostgreSQL**: Production migrations are versioned SQL scripts located in `migrations/postgres/` applied in sequence by `src/persistence/migrate.py`. Migration progress is tracked in the `schema_migrations` table.

### 4.3 Concurrency & Ledger Integrity
- Account balances are dynamically derived from append-only `ledger_entries`.
- PostgreSQL spend path acquires advisory transaction locks (`pg_advisory_xact_lock(hashtext(from_account))`) to serialize concurrent transfers on the same account.
- Every ledger transfer is atomic, double-entry, and enforces conservation of value:
  $$\sum \text{Debits} = \sum \text{Credits}$$
- Duplicate transactions are rejected via unique constraint on `transaction_id`.

### 4.4 Cryptographic Audit Trail
- Each audit event records `sequence_id`, `timestamp`, `actor_id`, `event_type`, `entity_id`, `payload_hash`, and `previous_event_hash`.
- Application-level SHA-256 chaining ensures that any retroactive tampering or deletion invalidates the chain.
- Datastores maintain ordering and indexed queryability across tenants and organisations.

---

## 5. Resilience, Circuit Breakers & Disaster Recovery

### 5.1 Emergency Circuit Breaker
- Operators can immediately pause an organisation's autonomous loop via `SystemCircuitBreaker`.
- Scoped to `(tenant_id, organisation_id)` in the datastore.
- When `PAUSED`: New order creation, claims, and capital allocations are blocked; verification and reconciliation of existing operations remain active.
- Resumption requires a strict **2-of-2 multi-signature quorum** by authorized administrators using `AdminGovernanceManager`.

### 5.2 Consequential Operation Lifecycle & Idempotency
All external actions are tracked as durable state machines:
```text
NEW ──► ESCROWED ──► SUBMITTED ──► [ UNKNOWN ] ──► RECONCILING ──► RECONCILED
```
- Network partitions or timeouts transition an operation to `UNKNOWN`.
- System restart **never blindly promotes `UNKNOWN` to `SUCCEEDED`**.
- The `ReconciliationService` queries provider adapters with row-level locks, committing escrow on confirmed success or refunding to treasury on failure.
- Idempotency keys (`idempotency_operations`) prevent duplicate executions during network retries.

---

## 6. Operational Documentation Links

For operational, deployment, and recovery procedures, refer to:
- [Runbook P1](RUNBOOK_P1.md): Local startup, Docker Compose, circuit breaker, and crash recovery procedures.
- [Disaster Recovery](disaster_recovery.md): Backup, point-in-time recovery, post-restore verification gates, and reconciliation runbook.
- [Production Readiness](PRODUCTION_READINESS.md): Security gates, configuration requirements, and deployment verification checklist.
