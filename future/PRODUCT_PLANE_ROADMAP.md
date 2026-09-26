# Kalyx — Product & Platform Roadmap

> **Status: ACTIVE — Phase 22**
>
> This is the living implementation checklist for turning the Kalyx control plane into a usable, multi-tenant autonomous-organisation platform. Checkboxes are updated as phases land and CI passes.

---

## Current baseline — verified before Phase 22

- [x] Deterministic Policy Engine
- [x] Agent proposal boundary
- [x] Controlled executors
- [x] Independent audit / verification
- [x] Double-entry / scoped ledger
- [x] Tenant + organisation application isolation
- [x] ExecutionContext
- [x] ExecutionAuthority
- [x] Public-only WalletIdentity
- [x] Durable human ExecutionApproval
- [x] External transaction signer boundary
- [x] Orbio CREDIT activation code path (not broadcast)
- [x] Real/testnet blockchain infrastructure smoke test
- [x] Production fail-closed configuration
- [x] Rate/body limits, CORS, health endpoint
- [x] Existing CEO agent + multi-agent orchestration primitives
- [x] Existing agent lifecycle/status rules
- [x] Existing B2B marketplace demonstration primitive
- [x] Future marketplace architecture documented

### Important baseline limitation

- [ ] PostgreSQL RLS — **not enabled yet**; isolation is currently application-enforced.
- [ ] Public self-service signup/login — **Phase 22**
- [ ] Full Product Plane — **Phase 22**
- [ ] Production-scale queue/worker architecture — **future scale phase**
- [ ] Public global agent marketplace — **future**
- [ ] Verified live Orbio economic settlement — **not yet claimed**

---

# Phase 22 — Product Plane

## 22A–22F — Identity → Organisation → Agents → Providers → Policies

### 22A — Account & Identity
- [ ] Wallet-first account creation
- [ ] Wallet challenge/signature verification
- [ ] Session/token lifecycle
- [ ] User identity records
- [ ] Wallet identity records
- [ ] Social-login adapter boundary
- [ ] No private-key/seed storage

### 22B — Workspace / Tenant
- [ ] Create workspace
- [ ] Owner membership
- [ ] Workspace membership model
- [ ] Workspace-scoped API access
- [ ] Tenant isolation checks
- [ ] First-login bootstrap

### 22C — Organisation Builder
- [ ] Create organisation
- [ ] Organisation lifecycle
- [ ] Owner/admin/operator/viewer controls
- [ ] Organisation dashboard bootstrap
- [ ] Treasury/configuration boundary

### 22D — Agent Management
- [ ] Create/configure agent
- [ ] Agent role
- [ ] Capabilities
- [ ] Allowed action types
- [ ] Authority ceiling
- [ ] Agent lifecycle controls
- [ ] Kalyx agent credentials
- [ ] Credential rotation/revocation
- [ ] Agent credentials never expose provider master secrets

### 22E — Provider Connections
- [ ] Provider registry
- [ ] Orbio connection boundary
- [ ] OpenAI connection
- [ ] Anthropic connection
- [ ] Gemini connection
- [ ] Generic OpenAI-compatible provider
- [ ] API-key encryption at rest
- [ ] Never store provider keys as plaintext
- [ ] Prefer delegated/OAuth connection when provider supports it
- [ ] Provider health/status
- [ ] Provider spend/budget boundary

### 22F — Configurable Governance
- [ ] Persist organisation policy profiles
- [ ] Spending limits
- [ ] Human-approval thresholds
- [ ] Allowed actions
- [ ] Target/provider allowlists
- [ ] Per-agent ceilings
- [ ] Policy versioning
- [ ] Policy preview
- [ ] Governance remains non-bypassable
- [ ] User policy configuration cannot disable Kalyx core safety invariants

## 22G–22I — Orchestrator → Mission → Beta Security

### 22G — Governed CEO / Orchestrator
- [ ] CEO/orchestrator identity
- [ ] Objective intake
- [ ] Task decomposition
- [ ] Agent delegation
- [ ] Orchestrator cannot directly execute
- [ ] Orchestrator cannot mint/consume unrestricted provider credentials
- [ ] Orchestrator compute budget
- [ ] Per-agent compute budgets
- [ ] Model/tool allowlists
- [ ] Spend/usage accounting
- [ ] Loop/retry limits
- [ ] Human escalation
- [ ] Kill switch

### 22H — Mission / Organisation Runtime
- [ ] Create mission from human objective
- [ ] Plan → propose → authorize → execute → verify → settle
- [ ] Mission budget
- [ ] Mission deadline
- [ ] Mission resource reservation
- [ ] Async-friendly execution boundary
- [ ] Mission event stream
- [ ] Failure/reconciliation state
- [ ] Organisation LIVE readiness state

### 22I — Beta / Security / Scale Gate
- [ ] First-user onboarding E2E
- [ ] Multi-user isolation E2E
- [ ] Agent credential E2E
- [ ] Provider credential secrecy tests
- [ ] CEO spend-boundary tests
- [ ] Policy bypass/adversarial tests
- [ ] Rate-limit/load protections
- [ ] Production auth verification
- [ ] CI green on Python 3.11/3.12, SQLite, PostgreSQL, container
- [ ] Merge Phase 22

---

# CEO / Orchestrator governance invariant

The CEO is **intelligence, not authority**.

```
Human objective
      ↓
CEO / Orchestrator
      ↓
Agent delegation
      ↓
Typed proposal
      ↓
Deterministic Policy Engine
      ↓
ExecutionAuthority / human approval
      ↓
Executor
      ↓
Independent Auditor
      ↓
Ledger
```

The CEO cannot:
- directly spend treasury
- directly execute an external API action
- bypass deterministic policy
- alter policy definitions
- access private keys
- obtain unrestricted provider credentials
- raise its own budget
- silently change an approved proposal
- approve its own high-risk action

LLM token/API spend is itself treated as a governed resource:
- organisation budget
- orchestrator budget
- per-agent budget
- per-mission budget
- provider/model/tool allowlists
- rate and retry limits
- usage accounting
- hard stop when exhausted

---

# Orbio infrastructure model

Orbio is not merely an LLM provider.

Current Orbio materials describe:
- one Orbio key spending a live credit balance
- hundreds of model routes through the gateway
- an agent/tool catalogue
- web/social/chain tools
- usage metered against the same balance
- agent key creation, status and revocation

Kalyx therefore treats Orbio as a **governed infrastructure gateway**.

```
Kalyx Agent
   ↓
Kalyx credential
   ↓
Governed provider gateway
   ↓
Orbio connection
   ├── LLM inference
   ├── web/search/scrape
   ├── social tools
   ├── chain reads
   └── future infrastructure tools
```

Kalyx must still enforce budgets before tool/model calls. Orbio's account balance is not a substitute for Kalyx policy.

---

# API-key model

There are two different credential classes.

### 1. Kalyx credentials

Used by users/agents to authenticate to Kalyx.

- random secret
- only a hash is persisted
- prefix/metadata stored for identification
- revocable
- rotatable
- scoped to an agent/organisation
- never reused as a provider secret

### 2. Provider credentials

Used by Kalyx to reach an external provider.

- never hash-only because Kalyx may need to use the credential
- encrypt at rest with KMS/secret-manager-backed encryption in production
- decrypt only at the provider execution boundary
- never return through API responses
- never give raw provider credentials to agents
- prefer OAuth/delegated connections where available

This gives a multi-agent organisation one governed provider connection without handing every agent a separate raw provider key.

---

# Scale roadmap

Kalyx currently uses a modular-monolith architecture.

That is intentional for this stage.

Before large public traffic, add:

- CDN/WAF
- load balancer
- horizontally scalable API instances
- Redis/cache where useful
- durable queue
- worker pool
- async external operations
- database connection pooling
- PostgreSQL RLS defense-in-depth
- observability/tracing
- per-tenant and per-user rate limits
- backpressure/circuit breakers
- autoscaling
- load testing

Do not split into microservices merely for appearance. Extract services only where load, reliability or ownership boundaries justify it.

---

# Future marketplace — unchanged scope

The marketplace remains a later extension of autonomous organisations, not a replacement for them.

### Current direction

```
Buyer Organisation
      ↓
Governed engagement
      ↓
Provider Organisation / Agent
      ↓
Execution
      ↓
Verification
      ↓
Escrow settlement
```

### Orbio Launchpad integration idea

Future Kalyx marketplace discovery may integrate with the Orbio Agent Launchpad.

Possible model:

```
Orbio Agent Launchpad
        ↓
Agent / Project discovery
        ↓
Kalyx "Hire / Govern"
        ↓
Scoped marketplace engagement
        ↓
Buyer policy
        ↓
Provider execution
        ↓
Verification + settlement
```

Orbio remains the launch/discovery ecosystem; Kalyx supplies governed contracting, execution boundaries and settlement semantics.

---

# After Phase 22

- [ ] PostgreSQL RLS
- [ ] Async queue + worker runtime
- [ ] Horizontal scaling/load testing
- [ ] KMS/HSM/MPC credential/signing hardening
- [ ] Production provider OAuth/delegation integrations
- [ ] Rich policy builder
- [ ] Mission streaming / realtime events
- [ ] External agent runtime adapters
- [ ] Global autonomous-agent marketplace
- [ ] Orbio Launchpad integration
- [ ] Organisation-to-organisation commerce
- [ ] Production-grade autonomous economic execution
