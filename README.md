<div align="center">

# KALYX
### Machine Autonomous Organisation Engine

**Agents propose · Policies authorize · Executors execute · Auditors verify**

[![CI](https://github.com/A-Nuel/kalyx-mao-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/A-Nuel/kalyx-mao-engine/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://opensource.org/licenses/MIT)

</div>

---

## Overview

**Kalyx** is an operating and governance runtime for **Machine Autonomous Organisations (MAOs)**.

In traditional multi-agent systems, language models are granted direct tool execution permissions, ambient network credentials, or subjective prompt-based guidelines. When an autonomous system attempts real-world economic interactions, prompt-level boundaries inevitably fail: unvetted calls slip through, credits are drained without receipts, state desynchronizes, and accountability is lost.

Kalyx replaces prompt-level trust with **deterministic systems infrastructure**:
- Organisations operate within explicit human missions and finite, conserved budgets.
- Specialized AI agents collaborate to generate structured, typed action proposals.
- An independent policy engine deterministically validates proposals against invariant rules.
- Cryptographically authenticated HMAC-SHA256 tokens bind authorization to exact proposal payloads, preventing modification or replay.
- Controlled executors carry out actions within locked escrow envelopes.
- An independent auditor verifies external receipts, token validity, and double-entry conservation before state transitions commit.
- An immutable double-entry ledger serves as the single source of financial and operational truth.

---

## Core System Invariant

The fundamental architectural invariant of Kalyx is strict separation of duties:

```text
AGENTS PROPOSE ──► POLICIES AUTHORIZE ──► EXECUTORS EXECUTE ──► AUDITORS VERIFY ──► LEDGER SETTLES
```

Treating an AI model as both the planner and the executor violates separation of duties. Giving an agent direct custody of an API key, private key, or treasury balance creates an unbounded attack surface.

In Kalyx, agents have **zero autonomous execution authority**:
- No agent or model can self-grant capabilities, release escrows, or mutate financial ledgers.
- Authority is an ephemeral, cryptographically authenticated capability granted to a verified intent, bounded by deterministic code, executed by constrained adapters, and validated by independent proof.

---

## Governed Economic Lifecycle

```text
┌──────────────┐       ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│  1. PROPOSE  │ ────► │  2. AUTHORIZE   │ ────► │   3. EXECUTE    │ ────► │   4. VERIFY     │
│              │       │                 │       │                 │       │                 │
│ Agent emits  │       │ PolicyEngine    │       │ BaseExecutor    │       │ Auditor checks  │
│ typed intent │       │ checks rules    │       │ locks escrow    │       │ receipts, token │
│ (Action-     │       │ & issues HMAC   │       │ & runs bounded  │       │ binding & ledger│
│ Proposal)    │       │ Capability Token│       │ adapter         │       │ conservation    │
└──────────────┘       └─────────────────┘       └─────────────────┘       └─────────────────┘
                                                           │
                                                           ▼
                                                 ┌─────────────────┐
                                                 │   5. SETTLE     │
                                                 │ Double-entry    │
                                                 │ ledger commit   │
                                                 └─────────────────┘
```

1. **PROPOSE:** An autonomous agent constructs an `ActionProposal` specifying the target action, parameters, requested budget, and intent hash. The agent has no access to credentials or network sockets.
2. **AUTHORIZE:** The `PolicyEngine` evaluates the proposal against deterministic rules (budget ceilings, target allowlists, rate limits, role capabilities). If approved, it generates a cryptographically authenticated `AuthorizationToken` (HMAC-SHA256) binding the proposal hash, policy version, and nonces.
3. **EXECUTE:** A `ControlledExecutor` validates the authorization token, consumes it atomically to prevent replay, reserves necessary credits into `ESCROW`, and performs the bounded side effect (e.g., API call or work order).
4. **VERIFY:** The `Auditor` independently inspects the execution deliverable or receipt, recalculates cryptographic digest bindings, verifies ledger math, and ensures audit chain consistency.
5. **SETTLE:** Upon audit passage, escrow locks are released to their destinations (`ESCROW` → `EXTERNAL_SINK` or provider balance). If verification fails or execution errors, funds roll back to `TREASURY`.

---

## Core Capabilities

### Agent Orchestration (`src/agents/`)
- **`CEOOrchestrator`**: Ingests high-level objectives, decomposes missions into stage graphs, coordinates worker assignments, monitors execution outcomes, and triggers adaptive replanning upon policy rejections.
- **Specialized Roles**: Role-scoped agents (`ResearchAgent`, `StrategyAgent`, `FinanceAgent`) operating under strict capability boundaries.
- **`B2BMarketplaceCoordinator`**: Orchestrates inter-DAO procurement: discovering open marketplace orders, evaluating profit margins, acquiring required capabilities, managing deliverables, and claiming bounties.
- **Adaptive Economics**: Multi-dimensional performance scoring (task success, reliability, resource efficiency, policy compliance) drives dynamic agent lifecycle states (`ACTIVE` ⟷ `PROBATION` ⟷ `RESTRICTED` ⟷ `SUSPENDED` ──► `RETIRED`).

### Deterministic Policy & Governance (`src/governance/`)
- **`PolicyEngine`**: Pure, deterministic validation engine checking rules sequentially with zero external side effects.
- **Rule Enforcement**: Budget ceilings, rolling-window spending limits, allowed action types, recipient contract allowlists, and target network verifications.
- **`SystemCircuitBreaker`**: Authoritative circuit breaker scoped to tenant and organisation, halting operations upon consecutive failures or operator trigger.
- **Admin Governance**: Governed resumption requires a strict **2-of-2 multi-signature quorum** by authorized administrative identities.

### Multi-Tenancy & Execution Authority (`src/identity/`, `src/execution/`)
- **Isolation by Default**: All entities, execution contexts, ledger accounts, and audit streams are partitioned by `tenant_id` and `organisation_id`.
- **`ExecutionAuthority`**: Explicit, organisation-bound authority defining permitted execution scopes without exposing credentials to agents.
- **`ExecutionApprovalManager`**: Durable, HMAC-authenticated human approvals bound to exact intent hashes, policy decision hashes, and expiration windows.
- **Non-Custodial Wallet Boundary**: `WalletIdentity` models store strictly **public metadata** (address, chain ID, verification timestamp). Private keys and seed phrases are never accepted, stored, or processed by Kalyx.

### Controlled Executors & SSRF Protection (`src/execution/`)
- **`BaseExecutor`**: Validates capability scope, authorization validity, token consumption, and escrow reservations before execution.
- **Network Egress Firewall**: Outbound HTTP requests undergo DNS resolution validation. Private IPv4/IPv6 blocks (`10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`), loopbacks (`127.0.0.1`), and cloud metadata endpoints (`169.254.169.254`) are blocked before socket creation.
- **Atomic Token Consumption**: `TokenConsumptionStore` ensures capability tokens are consumed single-use, preventing cross-restart replay attacks.

### Independent Verification & Cryptographic Audit (`src/audit/`, `src/settlement/`)
- **`Auditor`**: Independent verification component inspecting execution receipts, recalculating digest hashes, and validating ledger invariants.
- **Tamper-Evident Hash Chain**: Append-only SHA-256 audit chain linking all system events:
  $$\text{Hash}_n = \text{SHA256}(\text{Hash}_{n-1} \parallel \text{Timestamp} \parallel \text{Payload})$$
- **`WorkDeliverableVerifier`**: Validates deliverable artifact hashes against published specification hashes prior to bounty settlement.

### Double-Entry Ledger & Dual Persistence (`src/domain/`, `src/persistence/`)
- **Double-Entry Bookkeeping**: Strict credit conservation ($\sum \text{Debits} = \sum \text{Credits}$). Mutable column updates to account balances are prohibited; balances are dynamically computed from immutable ledger entries.
- **Dual Datastore Architecture**:
  - **SQLite**: Local development, unit testing, and deterministic sandboxes.
  - **PostgreSQL**: High-concurrency production deployments with connection pooling and transactional advisory locking (`pg_advisory_xact_lock`).
- **Production Fail-Closed**: Startup fails closed in production mode if `KALYX_DATABASE_URL` is missing or does not reference PostgreSQL.

---

## Deployment & Production Boundaries

**Deployable software infrastructure is separate from live consequential economic execution.**

1. **Production Infrastructure Ready**: Kalyx includes production-grade container builds, PostgreSQL migrations, security middleware (rate limiting, body limits, CORS), and health endpoints.
2. **Opt-in Consequential Providers**: External execution adapters (e.g., blockchain networks, external APIs) must be explicitly configured and enabled by the operator.
3. **Custody Isolation**: Never store wallet private keys or mnemonic seed phrases in application environment variables or database records. Transaction signing is delegated to external signers (e.g., EIP-1193 providers or hardware security modules).

---

## Command Centre

Kalyx provides a zero-dependency web interface built with standard ECMAScript, CSS3, and HTML5:

- **Mission Operations**: Visual tracking of mission stages, active proposals, and execution events.
- **Treasury Telemetry**: Real-time double-entry balance breakdown, escrow holdings, and credit conservation monitors.
- **Workforce Monitor**: Agent status, reputation metrics, capability permissions, and lifecycle states.
- **Audit Log Inspector**: Interactive inspection of the cryptographic hash chain and individual receipt signatures.
- **B2B Marketplace Portal**: Active marketplace orders, claimed tasks, and deliverable verification proofs.
- **Operator Emergency Controls**: Human-in-the-loop pause and resume toggles protected by constant-time key verification.

---

## Quickstart

### Prerequisites
- Python 3.11 or higher
- Git

### Local Installation

```bash
# 1. Clone the repository
git clone https://github.com/A-Nuel/kalyx-mao-engine.git
cd kalyx-mao-engine

# 2. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install package with development dependencies
pip install -e ".[dev]"

# 4. Run the demonstration script
python scripts/run_demo.py --fast
```

### Running the Server

```bash
# Start the FastAPI runtime
uvicorn src.api.server:app --host 127.0.0.1 --port 8000 --reload
```

Access the application in your browser:
- **Landing Page**: `http://127.0.0.1:8000/`
- **Command Centre**: `http://127.0.0.1:8000/command-centre`

### Docker Deployment

```bash
# Build the production image
docker build -t kalyx .

# Run with persistent data volume
docker run --rm -p 8000:8000 \
  -e KALYX_OPERATOR_KEY="your-operator-secret-key" \
  -v kalyx-data:/app/data \
  kalyx
```

### Docker Compose (Full Stack)

```bash
# Start API service and PostgreSQL database
docker compose up -d
```

---

## Configuration Reference

Kalyx is configured through environment variables. See [`.env.example`](.env.example) for a template.

| Variable | Type | Default | Description |
|---|---|---|---|
| `KALYX_ENV` | String | `development` | Runtime environment (`development`, `demo`, `production`). |
| `KALYX_DATABASE_URL` | String | *None* | PostgreSQL connection string (mandatory when `KALYX_ENV=production`). |
| `KALYX_DB` | String | `data/kalyx.db` | Local SQLite database path (development/test only). |
| `KALYX_POLICY_SECRET` | String | *Dev Default* | Secret key for HMAC-SHA256 capability tokens (min 32 chars in production). |
| `KALYX_OPERATOR_KEY` | String | *None* | Bearer API key required for operator management endpoints. |
| `KALYX_REQUIRE_OPERATOR_AUTH` | Boolean | `false` | When true, rejects unauthenticated operator actions. |
| `KALYX_CORS_ORIGINS` | String | `*` | Allowed CORS origins (comma-separated; explicit origins required in production). |
| `KALYX_BLOCKCHAIN_RPC_URL` | String | *None* | RPC URL for external blockchain transaction dispatch. |
| `ORBIO_API_KEY` | String | *None* | API key for Orbio model inference and gateway integration. |

---

## Testing & Verification

Kalyx maintains comprehensive test coverage across unit, integration, and security boundaries:

```bash
# Run all tests
python -m pytest -q

# Run specific test suites
python -m pytest tests/unit/ -q              # Pure logic and state transitions
python -m pytest tests/integration/ -q       # Multi-step workflows and persistence
python -m pytest tests/trust_boundaries/ -q  # Adversarial security and boundary enforcement
```

### Continuous Integration (CI)
GitHub Actions enforces automated checks on every push and pull request:
- Python 3.11 and 3.12 test matrices
- SQLite test suite and PostgreSQL integration suite
- Production Docker container build verification
- Strict dependency integrity check (`pip check`)
- Python bytecode compilation verification (`compileall`)
- ECMAScript syntax validation

---

## Documentation Index

- [Architecture Reference](docs/architecture.md): Complete architecture specification, pipeline stages, security boundaries, and persistence models.
- [Production & Operations Runbook](docs/RUNBOOK_P1.md): Local startup, Docker Compose execution, circuit breaker procedures, and crash recovery.
- [Disaster Recovery](docs/disaster_recovery.md): Backup protocols, point-in-time recovery, post-restore verification gates, and reconciliation runbook.
- [Production Readiness](docs/PRODUCTION_READINESS.md): Security gate statuses, production configuration requirements, and deployment verification checklist.

---

## Contributing

1. Create a feature branch from `main` (`git checkout -b feature/your-feature-name`).
2. Ensure changes uphold the core invariant (`AGENTS PROPOSE ──► POLICIES AUTHORIZE ──► EXECUTORS EXECUTE ──► AUDITORS VERIFY ──► LEDGER SETTLES`).
3. Verify integrity before submitting:
   ```bash
   python -m pip check
   python -m compileall -q src tests
   python -m pytest -q
   ```
4. Open a pull request against `main` with detailed verification steps.

---

## License

This project is licensed under the **MIT License**.
