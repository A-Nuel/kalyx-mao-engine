<div align="center">

# KALYX
### Machine Autonomous Organisation Engine

**Agents propose · Policies authorize · Executors execute · Auditors verify**

[![CI](https://github.com/A-Nuel/kalyx-mao-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/A-Nuel/kalyx-mao-engine/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

---

## Current Release Status — September 2026

Kalyx is a standalone, deployable control-plane project. The repository currently has:

- **Production configuration gates**: production mode requires PostgreSQL, non-demo governance secrets, operator authentication, and explicit identity authentication.
- **Durable governance**: deterministic policy authorization, durable authorization consumption, tenant/organisation isolation, and tamper-evident audit records.
- **Controlled execution boundaries**: provider adapters and signing remain outside agent authority; the 21D–21H security work adds an explicit organisation-bound execution authority and durable human execution approvals.
- **Multi-organisation security boundaries**: execution context, authority, approval, ledger, and persistence paths are scoped to tenant and organisation boundaries.
- **Real blockchain infrastructure**: EVM signing, nonce management, receipt verification, and a verified Sepolia infrastructure smoke test are implemented.
- **Orbio integration**: live/testnet-aware gateway and CREDIT activation code paths exist, with explicit LIVE/SIMULATED provenance.

### Important deployment boundary

**Deployable production infrastructure is not the same thing as autonomous economic execution with real funds.**

Kalyx can be deployed as a standalone governed control plane, but live consequential providers must still be enabled deliberately and supplied with their own operational credentials. The repository does **not** claim that a live Orbio economic settlement has been completed: the verified Sepolia transaction was an infrastructure smoke test, and the Orbio CREDIT activation path has passed mainnet preflight but has not been broadcast through Kalyx.

For production deployment, keep private keys/seed phrases outside Kalyx organisation data and agent configuration. In particular, do not place a wallet seed phrase or private key in Render environment variables merely to enable the 21D–21H architecture.

---

## 1. What is Kalyx?

**Kalyx** is an operating and governance runtime for **Machine Autonomous Organisations (MAOs)**.

In traditional multi-agent systems, language models are granted direct tool execution permissions, ambient network credentials, or subjective prompt-based guidelines. When an autonomous system attempts real-world economic interactions, this prompt-level boundary inevitably fails: hallucinated calls slip through, credits are drained without receipts, state desynchronizes, and accountability is lost.

Kalyx replaces prompt-level trust with **deterministic systems infrastructure**:
- Organisations receive concrete human missions and strictly bounded, finite budgets.
- Specialized AI agents collaborate to generate structured, typed action proposals.
- An independent policy engine deterministically validates proposals against invariant rules.
- Cryptographically authenticated HMAC-SHA256 tokens bind authorization to the exact proposal payload, preventing modification or replay.
- Controlled executors carry out actions within locked escrow envelopes.
- An independent auditor verifies external receipts, token validity, and double-entry conservation before state transitions commit.
- An immutable double-entry ledger serves as the single source of financial and operational truth.

Kalyx is engineered from day one as resilient infrastructure: zero ambient execution authority, multi-tenant isolation, crash-resilient durable journals, and fail-closed security.

---

## 2. The Core Thesis

The core engineering thesis of Kalyx is:

> **AI agents can reliably operate as an economically constrained organisation only when delegation, authority, execution, and verification are explicit architectural primitives rather than prompt instructions.**

Treating an LLM as both the planner and the executor violates the fundamental principle of separation of duties. Giving an agent direct custody of an API key, private key, or treasury balance creates an unbounded attack surface.

In Kalyx, agents have **zero execution authority**. Authority is an ephemeral, cryptographically authenticated capability granted to a verified intent, bounded by deterministic code, executed by constrained adapters, and validated by independent proof.

---

## 3. Why Kalyx?

| Challenge in Autonomous Systems | How Kalyx Solves It |
|---|---|
| **Ambient & Uncontrolled Execution** | Agents cannot invoke external tools or APIs directly. Every action must be proposed as a typed payload evaluated by a separate policy engine. |
| **Silent Economic Drain & Hallucinated Cost** | Double-entry bookkeeping enforces strict conservation: $\sum \text{Credits} = \text{Constant}$. Unbacked credit creation and unauthorized overdrafts are physically rejected. |
| **Self-Reported Execution Bias** | Executors never declare their own success. An independent Auditor validates external cryptographic receipts, hash chains, and network proofs before committing escrow. |
| **Token Replay & Front-Running** | Authorization tokens are HMAC-SHA256 authenticated and bound to unique nonces, org IDs, policy version hashes, and millisecond timestamps, tracked in a durable consumption store. |
| **Isolated Agent Silos** | Multi-agent coordination with role hierarchies (CEO, Research, Strategy, Finance), performance scoring, probation lifecycles, and cross-DAO B2B commerce. |
| **Transient Failures & Network Partitions** | Escrow holds funds in an authoritative state machine (`CREATED` → `AUTHORIZED` → `ESCROWED` → `SUBMITTED` → `RECONCILING` → `RECONCILED`), recovering gracefully after restarts. |

---

## 4. How the Governance Loop Works

At the core of Kalyx is the 5-stage conceptual loop:

```text
PROPOSE ──▶ AUTHORIZE ──▶ EXECUTE ──▶ VERIFY ──▶ SETTLE
```

```text
┌──────────────┐       ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│  1. PROPOSE  │ ────▶ │  2. AUTHORIZE   │ ────▶ │   3. EXECUTE    │ ────▶ │   4. VERIFY     │
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

1. **PROPOSE:** An autonomous agent (e.g., Finance or Research) constructs an `ActionProposal` specifying the target action, parameters, requested budget, and intent hash. The agent has no access to credentials or network sockets.
2. **AUTHORIZE:** The `PolicyEngine` evaluates the proposal against deterministic invariant rules (budget ceilings, target allowlists, rate limits, role capabilities). If approved, it generates a cryptographically authenticated `AuthorizationToken` (HMAC-SHA256) binding the proposal hash, policy version, and nonces.
3. **EXECUTE:** A `ControlledExecutor` validates the authorization token, consumes it idempotently to prevent replay, reserves necessary credits into `ESCROW`, and performs the bounded side effect (e.g., API call or work order).
4. **VERIFY:** The `Auditor` independently inspects the execution deliverable or receipt, recalculates cryptographic digest bindings, verifies ledger math, and ensures audit chain consistency.
5. **SETTLE:** Upon audit passage, escrow locks are released to their destinations (`ESCROW` → `EXTERNAL_SINK` or provider balance). If verification fails or execution errors, funds roll back to `TREASURY`.

---

## 5. Architecture

```text
                                Human Operator / Web Command Centre
                                                │
                                    (RBAC / Operator Key)
                                                ▼
                     ┌─────────────────────────────────────────────────────┐
                     │              FastAPI Gateway Layer                  │
                     │  /api/missions  /api/organisations  /api/demo       │
                     └──────────────────────────┬──────────────────────────┘
                                                │
                                                ▼
                     ┌─────────────────────────────────────────────────────┐
                     │            Autonomous Organisation Core             │
                     │                                                     │
                     │   ┌─────────────────────────────────────────────┐   │
                     │   │             Agent Orchestrator              │   │
                     │   │   [CEO] ──▶ [Strategy] [Research] [Finance] │   │
                     │   └──────────────────────┬──────────────────────┘   │
                     │                          │ ActionProposal           │
                     │                          ▼                          │
                     │   ┌─────────────────────────────────────────────┐   │
                     │   │           Policy & Governance Engine        │   │
                     │   │   • Rule Validator   • Circuit Breaker      │   │
                     │   │   • Capability Scope • Admin Multi-Sig      │   │
                     │   └──────────────────────┬──────────────────────┘   │
                     │                          │ AuthorizationToken       │
                     │                          ▼                          │
                     │   ┌─────────────────────────────────────────────┐   │
                     │   │            Execution Boundaries             │   │
                     │   │   • SandboxExecutor   • ExternalExecutor    │   │
                     │   │   • WorkExecutor      • OrbioGatewayAdapter │   │
                     │   └──────────────┬───────────────────┬──────────┘   │
                     │                  │                   │              │
                     │                  ▼                   ▼              │
                     │        ┌──────────────────┐  ┌────────────────┐     │
                     │        │ External Network │  │ Escrow / Lock  │     │
                     │        │ (Allowlist/SSRF) │  │ State Machine  │     │
                     │        └─────────┬────────┘  └───────┬────────┘     │
                     │                  │                   │              │
                     │                  └─────────┬─────────┘              │
                     │                            ▼                        │
                     │   ┌─────────────────────────────────────────────┐   │
                     │   │             Independent Auditor             │   │
                     │   │   • Tamper-evident SHA-256 Hash Chain       │   │
                     │   │   • Receipt & Deliverable Verification      │   │
                     │   └──────────────────────┬──────────────────────┘   │
                     │                          │ Verified Transitions     │
                     │                          ▼                          │
                     │   ┌─────────────────────────────────────────────┐   │
                     │   │             Persistence Layer               │   │
                     │   │   • Double-Entry Ledger (SQLite/PostgreSQL) │   │
                     │   │   • Tenant-Isolated Namespaces              │   │
                     │   │   • Durable Nonce & Idempotency Store       │   │
                     │   └─────────────────────────────────────────────┘   │
                     └─────────────────────────────────────────────────────┘
```

---

## 6. What is Actually Implemented

Kalyx distinguishes implemented execution paths from future/provider-specific integrations. The core governance and execution boundaries are enforced in code:

### Agents (`src/agents/`)
- **`CEOOrchestrator`**: Ingests high-level objectives, decomposes missions into stage graphs, coordinates worker assignments, monitors execution outcomes, and triggers adaptive replanning upon policy rejections.
- **Specialized Roles**: Role-scoped agents (`ResearchAgent`, `StrategyAgent`, `FinanceAgent`) operating under strict capability boundaries.
- **`B2BMarketplaceCoordinator`**: Orchestrates inter-DAO procurement: discovering open marketplace orders, evaluating profit margins, acquiring required capabilities, managing deliverables, and claiming bounties.
- **`CollateralCoordinator`**: Manages on-chain collateral pledges for credit-backed contracts without coupling directly to core execution paths.

### Policies (`src/governance/`)
- **`PolicyEngine`**: Pure, deterministic validation engine checking rules sequentially with zero external side effects.
- **Concrete Rules**:
  - `RULE-01` through `RULE-05`: Core budget constraints, transaction limits, capability limits, and target allowlists.
  - `RULE-BC-01` through `RULE-BC-05`: Blockchain-specific rules enforcing authorized network IDs (Ethereum Sepolia), recipient contract allowlists, gas price ceilings, and intent parameter equality.
  - `RULE-ORBIO-01` through `RULE-ORBIO-05`: Rules governing Orbio credit purchase limits, slippage bounds, and exchange parameters.
- **`CircuitBreaker`**: Persisted circuit breaker tripping automatically upon consecutive policy violations, abnormal credit velocity, or operator intervention.

### Authorization (`src/governance/crypto.py`, `src/security/`)
- **HMAC-SHA256 Token Authority**: Authorization tokens are cryptographically authenticated using HMAC-SHA256 bound to:
  $$\text{Token} = \text{HMAC}_{\text{secret}}(\text{OrgID} \parallel \text{ProposalHash} \parallel \text{DecisionID} \parallel \text{PolicyVersion} \parallel \text{Nonce} \parallel \text{Timestamps})$$
- **`TokenConsumptionStore`**: Durable store recording consumed nonces. Prevents cross-restart token replay attacks.
- **`CapabilityRegistry`**: Fine-grained role-based permission scopes (`EXECUTE_API`, `TRADE_MARKET`, `STAKE_COLLATERAL`).

### Execution authority & wallet boundary (`src/execution/`, `src/identity/`, `src/governance/`)
- **`ExecutionAuthority`**: Provider-neutral, organisation-bound authority that makes the permitted execution boundary explicit without giving agents signing capability.
- **`WalletIdentity`**: Public wallet metadata only (tenant, organisation, chain, address, provider); no private key or seed phrase is stored in the identity model.
- **`ExecutionApprovalManager`**: Durable, HMAC-authenticated human approval bound to the tenant, organisation, principal, execution authority, exact intent hash, and exact policy-decision hash, with expiry and atomic replay protection.

### Executors (`src/execution/`)
- **`BaseExecutor`**: Foundation verifying pre-execution invariants: capability scope, authorization validity, token consumption, and escrow reservations.
- **`SandboxExecutor`**: In-memory deterministic simulator producing valid cryptographic execution receipts for rapid testing and demonstrations.
- **`ControlledExternalExecutor`**: Hardened HTTP executor with strict network egress controls: IP/CIDR blocklists (blocking private networks and metadata endpoints to defeat SSRF), domain allowlists, HTTP method restrictions, payload size caps, and timeout guards.
- **`WorkExecutor` & `OrbioGatewayAdapter`**: Executes complex off-chain tasks powered by Orbio models, converting inputs into verifiable `WorkDeliverable` objects.

### Auditors (`src/audit/`, `src/settlement/`)
- **`Auditor`**: Independent verification component that inspects every execution receipt, recalculates proposal digest hashes, validates HMAC authentication codes, and cross-examines the double-entry ledger.
- **`AuditChain`**: Append-only tamper-evident hash chain linking all system events:
  $$\text{Hash}_n = \text{SHA256}(\text{Hash}_{n-1} \parallel \text{Timestamp} \parallel \text{Payload})$$
- **`WorkDeliverableVerifier`**: Specialized verifier validating deliverable artifact hashes against published marketplace order specifications.

### Ledger (`src/domain/`, `src/persistence/`)
- **Double-Entry Accounting**: Immutable credit tracking. Every credit movement requires a balancing debit and credit entry across system accounts (`TREASURY`, `ESCROW`, `EXPENSE`, `EXTERNAL_SINK`).
- **Conservation Invariant**: Mathematical verification asserting $\Delta \text{Assets} = 0$ for all internal transfers.
- **Tenant Isolation**: Deterministic account namespacing formatted as `{tenant_id}:{org_id}:{account_type}`, physically preventing cross-organisation asset manipulation.
- **Dual Persistence Backends**: SQLite for zero-dependency local testing/demoing; PostgreSQL for high-concurrency production deployments with connection pooling and advisory locking.

---

## 7. B2B Marketplace

Phase 17 introduces a **Cross-DAO B2B Marketplace** enabling autonomous organisations to outsource capabilities, pool resources, and settle commercial bounties without human mediation.

### The 6-Stage Autonomous Business Loop

```text
 ┌──────────────────────┐
 │  1. ORDER PROPOSED   │  Buyer DAO locks bounty credits into escrow and broadcasts
 └──────────┬───────────┘  a work order with SLA and technical specification hash.
            │
            ▼
 ┌──────────────────────┐
 │ 2. CAPABILITY EXPAN. │  Provider DAO discovers order, checks capabilities, and proposes
 └──────────┬───────────┘  policy-governed capability expansion if requirements are missing.
            │
            ▼
 ┌──────────────────────┐
 │ 3. ORBIO EXECUTION   │  Provider executes work payload through the Orbio Gateway,
 └──────────┬───────────┘  consuming model inference and generating a WorkDeliverable.
            │
            ▼
 ┌──────────────────────┐
 │ 4. INDEPENDENT AUDIT │  WorkDeliverableVerifier independently checks deliverable hashes
 └──────────┬───────────┘  against buyer spec hash. Bypasses self-reported agent status.
            │
            ▼
 ┌──────────────────────┐
 │ 5. ESCROW SETTLEMENT │  Dual-sided settlement: Escrow releases bounty to Provider,
 └──────────┬───────────┘  charges platform fee, and posts balanced double-entry entries.
            │
            ▼
 ┌──────────────────────┐
 │ 6. MISSION CHAINING  │  Provider reconciles surplus profit and automatically rolls it
 └──────────────────────┘  into the budget of its next mission, completing the loop.
```

---

## 8. Orbio Integration

Orbio provides `$CREDIT`, a transferable ERC-20 token used within the Orbio inference ecosystem on Robinhood Chain.

Kalyx adds an autonomous operating layer on top of Orbio:
1. **Model Gateway (`src/external/orbio/`)**: Connects autonomous agents to Orbio inference endpoints, tracking prompt tokens, completion tokens, latency, and credit burn per request.
2. **Autonomous Inference Procurement (`src/agents/orbio_purchase_loop.py`)**: When compute credits dip below operational watermarks, the organisation autonomously proposes an Orbio purchase order.
3. **Execution & Dual Provenance**: Explicit tracking of execution provenance (`LIVE` vs `SIMULATED`). When running without live credentials, Kalyx falls back gracefully to deterministic simulation while explicitly marking the output as simulated.

---

## 9. Credit Collateral

While Orbio's `$CREDIT` token is transferable, the base ERC-20 contract lacks native on-chain locking or obligation enforcement. Kalyx adds this governance layer via **Phase 18 Credit Collateral**:

- **On-Chain Vault (`contracts/CollateralVault.sol`)**: A secure smart contract that accepts ERC-20 `$CREDIT` deposits, locking tokens until released or forfeited.
- **Cryptographic State Machine (`src/domain/collateral.py`)**:
  ```text
  PROPOSED ──▶ AUTHORIZED ──▶ LOCKED ──▶ OBLIGATION_ACTIVE ──▶ VERIFIED_SUCCESS ──▶ RELEASED
                                                            └──▶ VERIFIED_FAILURE ──▶ FORFEITED
  ```
- **Independent Settle Authority**: Positions are resolved **exclusively** from `WorkDeliverableVerifier` cryptographic proof. An executor cannot unlock its own collateral.

---

## 10. Security Model

Kalyx adheres to defense-in-depth security principles across all layers:

- **Zero Ambient Authority**: Agents receive zero API keys or private keys. Execution adapters operate in isolated runtime contexts with scoped credentials.
- **Fail-Closed Semantics (`KALYX_ENV=production`)**:
  - Rejects default or weak policy secrets.
  - Rejects SQLite (PostgreSQL is required).
  - Enforces operator authentication headers (`X-API-Key`).
  - Restricts CORS to configured origins.
- **SSRF & Egress Protection**: Outbound HTTP requests undergo DNS resolution validation. Private IPv4/IPv6 blocks (`10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`), loopbacks (`127.0.0.1`), and cloud metadata IP (`169.254.169.254`) are blocked before socket creation.
- **Durable Idempotency & Replay Resistance**: Mutations carry client idempotency keys tracked in persistent storage with payload checksums. Authorization nonces are single-use.
- **Double-Entry Balance Authority**: System balances are computed dynamically from immutable ledger records; mutable column updates to account balances are disallowed.

---

## 11. Agent Economy

Kalyx features a dynamic, self-regulating internal economy:

### Multi-Dimensional Performance Tracking
Agents do not hold a static trust rating. The engine continuously measures:
- **Performance**: Task success rate and deliverable quality.
- **Reliability**: Consistency and adherence to timeouts.
- **Resource Efficiency**: Ratio of credits utilized vs budget reserved.
- **Policy Compliance**: Clean proposal rate without policy rejection.

### Evidence-Driven Lifecycle
State transitions are deterministic and backed by cryptographic evidence logs:
```text
ACTIVE ⟷ PROBATION ⟷ RESTRICTED ⟷ SUSPENDED ──▶ RETIRED
```
Agents experiencing elevated policy failures or resource overruns automatically transition to `PROBATION` or `RESTRICTED` status, shrinking their allowed budget caps until performance recovers.

### Adaptive Resource Allocation
The `ResourceAllocator` distributes mission budgets across the workforce using one of three strategies:
- `STATIC`: Equal allocation across operational agents (baseline).
- `PERFORMANCE`: Proportional distribution indexed to agent composite scores.
- `ADAPTIVE`: Dynamic allocation combining composite scores with organisation treasury scarcity damping.

---

## 12. Command Centre

Kalyx features a browser-based Command Centre built with zero external frontend framework dependencies:

- **Mission Operations**: Real-time visualization of mission stages, active proposals, and execution events.
- **Treasury Telemetry**: Live double-entry balance breakdown, escrow holdings, and real-time credit conservation checks.
- **Workforce Monitor**: Agent status, lifecycle state badges, reputation scores, and capability permissions.
- **Audit Log Inspector**: Live visual inspection of the cryptographic hash chain and individual receipt signatures.
- **B2B Marketplace Portal**: Active orders, claimed tasks, deliverable verification statuses, and dual-sided settlement logs.
- **Operator Emergency Controls**: Human-in-the-loop pause and resume toggles protected by constant-time API key verification.

---

## 13. Live Demos

Kalyx includes two live interactive demonstration experiences accessible directly via the Command Centre UI:

### 1. Guided Mission Governance Demo
Walks through a complete end-to-end execution of an autonomous mission:
$$\text{INITIALIZE} \longrightarrow \text{PLAN} \longrightarrow \text{PROPOSE} \longrightarrow \text{AUTHORIZE} \longrightarrow \text{EXECUTE} \longrightarrow \text{VERIFY} \longrightarrow \text{SETTLE} \longrightarrow \text{AUDIT}$$

Each stage surfaces authentic cryptographic tokens, ledger entries, and audit logs.

### 2. Marketplace Live Governance Session
Runs a real Kalyx mission through the same governed execution boundaries used by the main Live Demo, with marketplace-oriented economic context. 

The underlying cross-DAO commerce follows the six-stage business loop:
$$\text{ORDER\_PROPOSED} \longrightarrow \text{CAPABILITY\_EXPANSION} \longrightarrow \text{ORBIO\_EXECUTION} \longrightarrow \text{INDEPENDENT\_AUDIT} \longrightarrow \text{ESCROW\_SETTLEMENT} \longrightarrow \text{MISSION\_CHAINING}$$

**Demo Controls & Modes:**
- **Guided Mode**: Steps through each stage with configurable pacing (~6 seconds per stage) including live countdown progress bars and an instantaneous "Advance Now →" capability.
- **Judge Mode**: Pauses at stage boundaries, allowing judges and developers to inspect intermediate state, examine proofs, and advance transitions manually.

---

## 14. Quickstart

### Prerequisites
- Python 3.11 or higher
- Git

### Local Setup

```bash
# 1. Clone the repository
git clone https://github.com/A-Nuel/kalyx-mao-engine.git
cd kalyx-mao-engine

# 2. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies in editable mode
pip install -e ".[dev]"

# 4. Run the fast demonstration script
python scripts/run_demo.py --fast
```

### Starting the Command Centre

```bash
# Start the FastAPI engine
uvicorn src.api.server:app --host 127.0.0.1 --port 8000 --reload
```

Navigate to `http://127.0.0.1:8000`:
- **Landing Page**: Overview of the Kalyx primitive and architecture.
- **Command Centre (`/command-centre`)**: Real-time management interface, live telemetry, and interactive demo launchers.

### Running with Docker

```bash
# Build the production container
docker build -t kalyx .

# Run container with mounted data volume
docker run --rm -p 8000:8000 \
  -e KALYX_OPERATOR_KEY="your-operator-secret-key" \
  -v kalyx-data:/app/data \
  kalyx
```

### Full Stack Deployment (Docker Compose)

```bash
# Starts Kalyx application and PostgreSQL database with persistent volume
docker compose up -d
```

---

## 15. Configuration

Kalyx is configured via environment variables. See [`.env.example`](.env.example) for the complete reference.

| Variable | Type | Default | Description |
|---|---|---|---|
| `KALYX_ENV` | String | `demo` | Environment mode (`demo` or `production`). |
| `KALYX_DATABASE_URL` | String | *None* | PostgreSQL connection string. Mandatory when `KALYX_ENV=production`. |
| `KALYX_DB` | String | `kalyx.db` | SQLite database path (demo/development only). |
| `KALYX_POLICY_SECRET` | String | *Dev Default* | Secret key for HMAC-SHA256 token authentication (min 32 chars in production). |
| `KALYX_OPERATOR_KEY` | String | *None* | Bearer API key required for operator control endpoints. |
| `KALYX_REQUIRE_OPERATOR_AUTH` | Boolean | `false` | When true, rejects unauthenticated pause/resume requests. |
| `KALYX_CORS_ORIGINS` | String | `*` | Allowed CORS origins (comma-separated). |
| `KALYX_PUBLIC_DEMO` | Boolean | `false` | Enables demo simulation endpoints (`/api/demo/*`). |
| `KALYX_LIVE_DEMO_STAGE_DELAY` | Float | `6.0` | Default seconds per stage during guided demo playback. |
| `KALYX_BLOCKCHAIN_RPC_URL` | String | *None* | Ethereum Sepolia RPC URL for Phase 12 on-chain settlement. |
| `ORBIO_API_KEY` | String | *None* | Orbio API key for live model inference and gateway calls. |

---

## 16. Development Phases

The repository reflects a disciplined engineering progression through Phase 21H:

- **Phase 1 — Deterministic Foundation**: Pure functional state machines and domain primitives.
- **Phase 2 — Agent Architecture & Orchestration**: CEO orchestrator and specialized agent roles.
- **Phase 3 — Persistence & Independent Verification**: Double-entry ledger, cryptographic hash chains.
- **Phase 3.5 — Cryptographic & Policy Hardening**: HMAC authorization tokens, policy versioning.
- **Phase 4 — Agent Economy & Controlled Execution**: Escrow atomicity, outbound SSRF guards.
- **Phase 5 — Command Centre & Settlement Boundary**: FastAPI read/control interface, web dashboard.
- **Phase 6 — Product Hardening**: Constant-time key comparison, security headers, CI build matrix.
- **Phase 7 — Mission Lifecycle**: State machine transitions, durable mission events, Judge Demo Mode.
- **Phase 8 — Security & Reliability Hardening**: Token consumption store, idempotency journals.
- **Phase 9 — Multi-Tenancy & Organisation Isolation**: RBAC roles, tenant-namespaced ledgers.
- **Phase 9.5 — Dual-Backend Persistence**: Unified database abstractions (SQLite + PostgreSQL).
- **Phase 10 — Consequential Execution & Settlement**: Provider state machines, durable reconciliation.
- **Phase 11 — Production Hardening**: Disaster recovery, process crash restarts, advisory locks.
- **Phase 12 — Real Blockchain Settlement**: Ethereum Sepolia EIP-1559 settlement, isolated signing.
- **Phase 13 — Adaptive Economics**: Multi-dimensional scoring, counterfactual allocation benchmarks.
- **Phase 14 — Orbio Integration**: Autonomous inference purchasing, MCP bridge, simulated exchange.
- **Phase 15 — Self-Sustaining Loop**: Autonomous compute replenishment, surplus accounting.
- **Phase 16 — Multi-Agent Coordination**: Inter-agent task handoffs, Orbio gateway adapters.
- **Phase 17 — Autonomous Enterprise & B2B Marketplace**: 6-stage cross-DAO marketplace, capability evolution.
- **Phase 18 — Credit Collateral**: On-chain `CollateralVault.sol`, verifier-settled obligation locks.
- **Phase 19 — Verified Testnet Infrastructure**: First verified Sepolia transaction and reconciliation/audit proof; explicitly an infrastructure smoke test, not Orbio economic settlement.
- **Phase 20A — Orbio Testnet Discovery**: Verified deployment/network boundaries and fail-closed behavior when the target testnet does not expose the required Orbio contracts.
- **Phase 20B — Orbio CREDIT Activation**: Hardened activation intent, policy, preflight, verifier, runtime, and external-signer boundary; live mainnet preflight completed without broadcasting.
- **Phase 21A — Architecture Discovery**: Audited existing tenant, organisation, identity, wallet, and execution boundaries before platform expansion.
- **Phase 21B — Canonical Execution Context**: Immutable principal/tenant/organisation/role/request context and context propagation.
- **Phase 21C — Tenant & Organisation Isolation**: Trusted organisation scoping, context binding, and cross-scope leakage tests.
- **Phase 21D–21H — Platform Security Boundary**: Organisation-bound execution authority, public-only wallet identity, durable human execution approvals, multi-organisation proof, and adversarial security coverage.

---

## 17. Tests / CI

Reliability is backed by a comprehensive automated test suite across multiple testing tiers:

```bash
# Run the test suite
python -m pytest -q

# Run specific test tiers
python -m pytest tests/unit/ -q              # Unit tests
python -m pytest tests/integration/ -q       # Integration tests
python -m pytest tests/trust_boundaries/ -q  # Adversarial security tests
```

### Test Architecture
- **Unit (`tests/unit/`)**: Verifies state transitions, policy rules, cryptography bindings, and ledger balance derivation in isolation.
- **Integration (`tests/integration/`)**: Evaluates multi-step mission workflows, demo API endpoints, database persistence, and crash recovery.
- **Trust Boundary & Adversarial (`tests/trust_boundaries/`)**: Explicitly attempts unauthorized executions, token replay, SSRF escapes, cross-tenant data leakage, and escrow tampering.
- **CI Quality Gate**: GitHub Actions runs automated matrix testing on Python 3.11 and 3.12, enforces `pip check`, verifies source compilation (`compileall`), and tests container builds on every push and PR.

---

## 18. Tech Stack

- **Runtime**: Python 3.11+
- **API Framework**: FastAPI, Uvicorn, Starlette
- **Data Validation**: Pydantic v2
- **Persistence**: PostgreSQL (production), SQLite (development/testing)
- **Cryptography**: `cryptography` (HMAC-SHA256, Ed25519)
- **Web3 / Blockchain**: `web3.py`, `eth-account`, `eth-abi`
- **Smart Contracts**: Solidity ^0.8.20, Foundry
- **HTTP / Networking**: HTTPX
- **Frontend**: Vanilla ECMAScript, CSS3, HTML5 (Zero bundler or node_modules dependencies)
- **Containerization**: Docker, Docker Compose
- **Testing**: pytest, pytest-asyncio

---

## 19. Contributing

We welcome contributions to the Kalyx MAO Engine. Please follow these guidelines:

1. **Fork & Branch**: Create a feature branch from `main` (`git checkout -b feature/your-feature-name`).
2. **Adhere to Invariants**: Ensure your changes do not violate the core invariant (`AGENTS PROPOSE → POLICIES AUTHORIZE → EXECUTORS EXECUTE → AUDITORS VERIFY`).
3. **Verify Integrity**:
   ```bash
   python -m pip check
   python -m compileall -q src tests
   python -m pytest -q
   ```
4. **Submit PR**: Open a pull request against `main` describing your changes, motivation, and verification steps.

---

## 20. License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
