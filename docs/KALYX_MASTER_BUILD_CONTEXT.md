# KALYX — MASTER BUILD CONTEXT

> Canonical engineering context for Kalyx contributors and AI reviewers.
> Updated: 2026-09-13
>
> **Rule:** this document governs scope and architecture. Do not redesign Kalyx from a review comment, benchmark, or model preference. Propose changes against the current phase and its acceptance gate.

## 1. Product thesis

**What becomes possible when blockchain becomes an execution and settlement layer for autonomous software?**

Kalyx is an operating/control layer for autonomous AI organisations: systems that receive a mission, coordinate specialised agents, allocate scarce resources, execute Web2/Web3 actions, and remain accountable through deterministic policy and independent verification.

Kalyx is deliberately **not** another generic agent swarm, trading bot, wallet, registry, or agent marketplace.

Primary positioning:

> Organisational control: resource allocation + authority + reputation + policy enforcement + auditability.

## 2. Core invariant

> **Agents propose. Policies authorize. Executors execute. Auditors verify.**

This is an architectural invariant, not marketing copy.

Reasoning agents are organisational members. Governance and execution infrastructure remains deterministic wherever possible.

Current reasoning roles:
- CEO / Orchestrator
- Researcher
- Strategist
- Financial Analyst

Do not blindly turn Risk, Policy, Executor, or Auditor into LLM agents. They exist primarily as deterministic control-plane infrastructure.

Canonical flow:

`CEO / Research / Strategy / Finance -> Policy Engine -> Executor -> Auditor`

## 3. MVP / MAO

Give an autonomous organisation a mission and finite budget/credits. It should:

1. create a plan;
2. delegate work to specialised agents;
3. allocate scarce resources;
4. enforce deterministic policies;
5. reject unsafe or over-budget proposals;
6. replan after rejection/failure;
7. execute approved actions through controlled adapters;
8. independently verify outcomes;
9. update agent performance/reputation/resource allocation;
10. leave a replayable evidence/audit trail.

The canonical three-minute demonstration is:

`Mission -> Plan -> Research/Strategy/Finance -> Policy rejection -> Replan -> Authorization -> Escrow -> Execute -> Audit -> Economy update`

## 4. Agent economy

The economy is a resource-allocation experiment, not an instruction for agents to preserve themselves.

Resources may include:
- ORG Credits
- compute/task budget
- authority
- reputation

Useful agent score:

`Performance x Reliability x Resource Efficiency x Policy Compliance`

Organisation utility:

`outcome_value - resource_cost - risk_penalty - policy_violations - failure_cost`

Lifecycle:

`ACTIVE -> PROBATION -> RESTRICTED -> SUSPENDED -> RETIRED`

Persistent poor performance should reduce resources/authority and eventually trigger replacement or retirement. **Survival/self-preservation is never the optimisation target.**

Experiments should compare static/equal allocation against performance-based allocation and, where useful, performance + reputation + authority. Report negative results honestly.

## 5. Security model

The trusted boundary is the control plane, not the LLM.

Required properties:
- deterministic policy checks;
- authoritative ledger;
- cryptographically bound authorization;
- least-privilege capabilities;
- authority ceilings;
- escrow before consequential execution;
- durable idempotency;
- ambiguous external operations must not be blindly retried;
- provider idempotency keys for consequential POSTs;
- SSRF/redirect/payload protections;
- independent audit verification;
- tenant and organisation isolation;
- fail-closed authentication in production.

Kalyx is **not yet declared ready for serious real-world money custody**. Real-value settlement requires provider-specific adapters, reconciliation, key management, operational controls, and additional adversarial testing.

## 6. Threat-model priorities

The STRIDE threat model is a release gate, not optional documentation.

Critical requirements:
- no tenant can read another tenant's organisations, agents, treasury, missions, policies, proposals, decisions, or audit records;
- no tenant can spend or authorize another tenant's resources;
- same-tenant organisations must also be isolated;
- production identity must be derived from authenticated principal membership, not trusted client-controlled tenant headers;
- consequential agent actions eventually need a strong agent identity/signing boundary;
- crash-boundary audit/reconciliation gaps must be closed before real-value execution;
- API-wide rate/body limits and production boot fail-closed controls remain later hardening items.

Do not expand execution power while a Critical isolation/authentication issue remains open.

## 7. Current architecture

Core layers:

- `src/domain/` — entities, enums, events, exceptions
- `src/governance/` — deterministic policy/rules
- `src/economy/` — ledger/economic logic
- `src/security/` — atomic ledger, idempotency, capabilities, durable executor
- `src/execution/` — controlled execution and authorization
- `src/audit/` — independent verification
- `src/orchestration/` — mission/task coordination
- `src/persistence/` — SQLite persistence/repositories
- `src/identity/` — principals, memberships, identity context
- `src/tenancy/` — tenant and organisation scoping
- `src/settlement/` — settlement abstraction/simulation
- `src/api/` — FastAPI control-plane API
- `apps/web/` — command-centre UI

Conceptual tenancy:

`User -> Workspace/Tenant -> Autonomous Organisation -> Missions`

Required resource identifiers:
`tenant_id`, `organisation_id`, `mission_id`, `agent_id`.

## 8. Engineering roadmap: Phase 1–12

This is the **current implementation roadmap**. The older PRD contains a shorter five-phase product roadmap; that is a high-level product evolution and does not replace this engineering sequence.

### Phase 1 — Minimum Autonomous Organisation

Establish the basic organisation model, agents, tasks, proposals, orchestration loop, and deterministic role boundaries.

**Gate:** a mission can be decomposed and coordinated by a basic MAO model.

### Phase 2 — Governance and controlled action

Introduce structured proposals, deterministic policy decisions, authority/budget checks, controlled execution boundaries, and the basic authorization contract.

**Gate:** an agent cannot directly perform a consequential action outside policy.

### Phase 3 — Persistence, authoritative ledger, audit chain

Persist organisation state; make the ledger authoritative; add append-only cryptographic audit events; support restart/recovery and independent audit verification.

**Gate:** state survives restart and consequential state changes are reconstructible/verifiable.

### Phase 4 — Hardened execution + adaptive economy

Add cryptographic authorization, escrow reservation/commit/rollback, external execution abstraction, durable failure handling, dynamic resource allocation, and comparative economic experiments.

**Gate:** execution failures do not silently spend resources; economic claims are measured rather than assumed.

### Phase 5 — Foundation, packaging and CI

Formalise dependencies, test environments, production containerisation, CI, compile checks, dependency checks, and repeatable test execution.

**Gate:** supported Python versions and the production container pass the complete automated suite.

### Phase 6 — Command Centre API/UI + operational controls

Expose the control plane through FastAPI and the command-centre UI; add mission/operator controls, CORS configuration, security headers, production operator authentication, health checks, and operational documentation.

**Gate:** the product is observable and operable through the control plane without weakening the security boundary.

### Phase 7 — Mission lifecycle / autonomous-organisation experience

Make Mission first-class: lifecycle states, mission creation/run/cancel, deterministic live execution, mission timeline, visible policy rejection/replan, economy updates, and deterministic Judge Demo Mode.

**Gate:** the canonical three-minute mission can run end-to-end through the same control plane used by the product.

### Phase 8 — Security and reliability hardening

Close race conditions, overspend paths, idempotency/replay gaps, capability/authority bypasses, external execution ambiguity, SSRF/redirect issues, and tenant ledger isolation. Document failure semantics.

**Gate:** adversarial tests demonstrate the security invariants; no known critical control-plane bypass remains.

### Phase 9 — Identity, tenancy and organisation isolation

Build the multi-tenant identity boundary: principals, memberships, roles, tenant context, organisation scoping, tenant-scoped persistence, tenant-scoped treasury/ledger reads, and API enforcement.

Required roles:
`OWNER`, `ADMIN`, `OPERATOR`, `VIEWER`.

Required boundary behaviour:
- missing identity -> 401;
- unauthorized membership -> 403;
- cross-tenant resource -> safe 404;
- same-tenant cross-organisation resource -> isolated;
- client tenant headers are scope selectors, never proof of authorization.

**Current gate:** fix organisation tenant persistence/loading and make the full Phase 9 API isolation matrix green.

### Phase 10 — Real execution and settlement adapters

Introduce provider-specific Web2/Web3 execution and settlement adapters behind the existing controlled executor interface. Robinhood Chain is an execution/settlement adapter, not the product itself.

Potential adapter families:
- Web APIs
- GitHub/actions
- payments
- Robinhood Chain / on-chain settlement

**Gate:** every real-value or external side effect remains policy-authorized, capability-scoped, idempotent where possible, and auditable.

### Phase 11 — Observability, evidence and organisational intelligence

Deepen mission telemetry, audit/evidence views, performance attribution, agent reputation/history, economic experiments, replay/debugging, and operator-facing explanations of why decisions happened.

**Gate:** an operator can reconstruct what happened, why it happened, what policy allowed it, what was executed, what was verified, and how resources changed.

### Phase 12 — Production readiness, deployment and judge/demo polish

Production configuration, deployment, migrations, secrets/key management, rate/body limits, operational runbooks, monitoring, backup/recovery, security review, benchmark missions, deterministic judge demo, and final product presentation.

**Gate:** reproducible deployment + green CI + adversarial/security gates + a concise, evidence-backed demonstration.

## 9. Completed implementation state

Through Phase 8 the repo has implemented:
- SQLite persistence;
- authoritative ledger;
- append-only SHA-256 audit chain;
- independent auditor;
- HMAC authorization tokens with proposal/policy binding;
- escrow execution semantics;
- crypto abstraction;
- controlled external executor;
- atomic concurrent treasury transfers;
- durable idempotency journal;
- replay protection;
- capability and authority enforcement;
- suspended-agent enforcement;
- durable external-operation journal;
- provider idempotency keys;
- DNS-based SSRF protection and redirect blocking;
- tenant-scoped ledger primitives;
- production API/container/CI foundations.

The Phase 8 suite had reached 143 passing tests on the reported Python 3.11/3.12 CI run.

## 10. Current Phase 9 state

Already implemented:
- identity models and roles;
- identity context and membership checks;
- identity repository;
- tenant-aware database schema;
- organisation-scoped ledger;
- organisation-scoped task/agent identifiers;
- multiple organisations sharing a database;
- API identity enforcement foundations;
- Phase 9 unit/integration tests.

Current known CI failure:

`tests/integration/test_phase9_api_identity.py::test_cross_tenant_org_returns_not_found`

Reported result: **149 passed, 1 failed** on Python 3.11.

Expected: HTTP 404 with safe `Organisation not found` behaviour.

Observed: HTTP 404 whose detail was effectively `"'NoneType' object has no attribute 'tenant_id'"`.

This is not merely a test mismatch. Investigation found a source-level persistence defect:

`SqliteRepository.save_organisation()` does not persist `Organisation.tenant_id`, and `load_organisation()` does not restore it. The database schema has a `tenant_id` column, but the repository currently omits it from the INSERT/UPDATE/load path, causing the schema default (`tenant-demo`) to be used.

**Do not mask this by weakening the test. Fix persistence and then rerun the complete CI matrix.**

## 11. Current Phase 9 work order

1. Fix `SqliteRepository.save_organisation()` to persist `tenant_id`.
2. Fix organisation update/upsert semantics so tenant identity cannot silently change across updates.
3. Fix `load_organisation()` to restore `tenant_id`.
4. Add regression tests proving tenant identity survives save/load and restart.
5. Verify `require_identity_for_org()` handles nonexistent resources safely before dereferencing tenant state.
6. Run the Phase 9 API isolation matrix:
   - missing identity;
   - inactive principal;
   - inactive membership;
   - wrong tenant header;
   - cross-tenant org;
   - same-tenant cross-org;
   - allowed owner/admin/operator/viewer actions.
7. Verify API read paths for organisations, agents, tasks, proposals, decisions, ledger, events, audit and policies are tenant/org scoped.
8. Run migration/restart tests against existing databases.
9. Run Python 3.11 + 3.12 CI and production container checks.
10. Only after the gate is green, move to Phase 10.

## 12. Delegation rules for other AI models

Other AI models may review or implement **bounded work packages**, but must not redefine the product or roadmap.

Every delegated task must state:
- current phase;
- exact files/scope;
- acceptance tests;
- invariants that must remain true;
- explicit non-goals;
- required CI checks.

Reviewers must distinguish:
- confirmed defect;
- security concern;
- design trade-off;
- optional improvement;
- out-of-scope future work.

Do not accept architectural changes merely because they are cleaner in isolation.

Do not introduce:
- unrestricted LLM authority;
- uncontrolled money movement;
- a generic chat-first product model;
- another trading bot as the product thesis;
- autonomous self-preservation objectives;
- unnecessary new agent roles when deterministic infrastructure is sufficient.

## 13. Definition of Done

A phase is not complete because code was written.

A phase is complete only when:

`Implementation -> Tests -> Adversarial tests -> CI -> Documentation -> Acceptance gate -> Roadmap checkpoint`

The next phase must not silently absorb unresolved Critical issues from the previous phase.

## 14. Source-of-truth hierarchy

When sources disagree, use this order:

1. Current repository behaviour and tests.
2. This Master Build Context for architecture/scope.
3. Phase-specific engineering/security documents.
4. Product PRD/UI/demo documents.
5. External suggestions from reviewers/models.

External suggestions are inputs, not authority.

## 15. One-line project status

**Kalyx has a hardened autonomous-organisation control plane through Phase 8 and is currently closing Phase 9 identity/tenant isolation before progressing to real execution adapters, deeper observability, and production readiness.**
