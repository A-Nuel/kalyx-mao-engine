# Kalyx — Future Roadmap

> **Status: FUTURE / PLANNED**
>
> This document records product and architecture directions discussed for Kalyx. These are planned extensions, not claims about the current implementation.

---

## 1. Policy Builder & Governance UX

Kalyx already has a deterministic Policy Engine and policy enforcement primitives. The future product should expose these controls to organisation administrators through a first-class policy builder.

### Planned capabilities
- Create and edit organisation policies without changing application code.
- Configure spending ceilings per action, mission, agent, and time window.
- Configure allowed actions and capabilities.
- Configure target and integration allowlists.
- Require human approval for selected risk classes or value thresholds.
- Configure production / sensitive-resource restrictions.
- Set policy expiry, versioning and rollout rules.
- Preview a proposal against policies before execution.
- Show exactly which rule approved or rejected an action.

### Governance model

User-configured policies should operate **inside Kalyx's non-bypassable security boundaries**.

    Organisation Policy
           ↓
    Deterministic Policy Engine
           ↓
    Authorization
           ↓
    Controlled Execution

AI should not silently rewrite or override deterministic governance rules.

## 2. Real Executor & Integration Layer

Executors are currently software execution boundaries. Kalyx should evolve these into production-grade integration adapters while preserving the same authorization boundary.

### Planned integrations
- GitHub
- Gmail
- Slack
- Notion
- Salesforce
- Stripe
- AWS
- WhatsApp
- blockchain networks
- Orbio and other autonomous-economy protocols

### Target pattern

    AI Agent
       ↓
    Action Proposal
       ↓
    Policy Engine
       ↓
    Authorization Token
       ↓
    Integration Executor
       ↓
    External System
       ↓
    Independent Verifier
       ↓
    Ledger / Audit

The integration executor should never become an independent decision-maker. It performs only the action authorized by Kalyx.

### Security requirements
- scoped credentials
- least-privilege capabilities
- target allowlists
- authorization binding
- replay protection
- idempotency
- timeout and failure handling
- secret isolation
- external-state reconciliation
- independent verification

## 3. Human + Agent Integration Marketplace

Kalyx should eventually provide a marketplace for connecting autonomous organisations to third-party applications and human-operated services.

Potential integration categories:
- communication
- software development
- finance
- CRM
- cloud infrastructure
- productivity
- data
- commerce
- identity and authentication

The goal is not simply to provide API wrappers. Each integration should become a **governed execution capability**.

## 4. Global Marketplace for Autonomous Agents

The current marketplace is an internal/B2B autonomous work-exchange primitive.

A future **Global Marketplace** should extend this into a broader ecosystem where organisations can discover and hire external autonomous AI programs.

    KALYX COMMAND CENTRE
    │
    ├── ORGANISATION
    │   ├── Agents
    │   ├── Missions
    │   ├── Policies
    │   ├── Treasury
    │   └── Marketplace Orders
    │
    └── GLOBAL MARKETPLACE
        ├── Agents
        ├── Services
        ├── Organisations
        ├── Integrations
        └── Contracts

### Marketplace objects
- Agent listings
- Services
- Organisations
- Capability requirements
- Contracts / assignments
- Scoped permissions
- Escrow
- Verification
- Settlement
- Reputation / performance history

An external agent should **not** be copied into the hiring organisation as if it were an internal employee.

Instead, Kalyx should create a scoped engagement/contract context.

    External Agent
          ↓
    Contract / Assignment
          ↓
    Buyer Policy
          ↓
    Scoped Capabilities
          ↓
    Authorised Work
          ↓
    Verification
          ↓
    Escrow Settlement

## 5. Organisations Hiring Organisations

The Global Marketplace should eventually support organisation-to-organisation autonomous commerce.

Example: a law organisation contracts an autonomous cleaning/service organisation.

There are two governance domains:

    BUYER ORGANISATION
        ↓
    Contract / Budget / Acceptance Policy
        ↓
    PROVIDER ORGANISATION
        ↓
    Provider's Internal Agents + Policies + Execution

The buyer governs the **contract and outcome**.
The provider governs its **internal operations**.

Kalyx should preserve that separation rather than collapsing both organisations into one trust domain.

## 6. External Autonomous Agent Runtime Boundary

External AI programs may be hosted outside Kalyx.

Kalyx should interact with them through secure adapters/runtime boundaries.

    External AI Program
           ↓
    Secure Adapter / Runtime Boundary
           ↓
    Structured Proposal
           ↓
    Kalyx Policy
           ↓
    Authorization
           ↓
    Executor

The external agent should never receive direct access to Kalyx's executor internals, organisation treasury, ledger mutation, unrestricted credentials, or policy secrets.

External agents propose; Kalyx governs and executes.

## 7. Generalised Autonomous Economy

The Orbio integration demonstrates a specific autonomous economic workflow. The longer-term architecture should generalise this into an economy layer that can support multiple external economic systems.

Potential future primitives:
- external assets
- purchases
- service contracts
- escrow
- collateral
- payments
- revenue
- refunds
- performance-based settlement
- autonomous procurement

The existing ledger, escrow, authorization and independent-verification primitives should remain the foundation.

## 8. Policy-Driven Human Approval

Human involvement should become configurable rather than binary.

    <$10       → automatic
    $10–$100   → policy-based automatic execution
    >$100      → human approval
    High risk  → human approval
    Unknown    → pause + reconcile

Human approval should remain cryptographically/hash-bound to the exact proposal where required, preventing an approved action from being silently modified afterward.

## 9. Reputation & Agent Performance Economy

Kalyx already has performance and lifecycle primitives. Future marketplace functionality should expose them as a richer reputation system.

Potential dimensions:
- reliability
- successful completion
- verification quality
- resource efficiency
- policy compliance
- failure rate
- response latency
- economic value delivered

Reputation should be evidence-driven rather than purely based on user ratings.

## 10. Production-Grade Execution Infrastructure

Before unrestricted real-world autonomy, Kalyx should progressively harden the execution boundary.

Planned infrastructure includes:
- HSM/KMS-backed signing
- MPC or multi-party signing
- stronger credential isolation
- production secret management
- durable external operation journals
- provider-specific reconciliation
- production-grade observability
- policy simulation / dry runs
- disaster recovery
- formal security review
- audited smart contracts where applicable

Real-money or irreversible actions should remain behind explicit safety and governance gates.

## 11. Unified Long-Term Architecture

The long-term Kalyx model is:

    HUMAN / ORGANISATION
             │
             ▼
       MISSION / CONTRACT
             │
             ▼
       AI AGENTS / PROGRAMS
             │
             ▼
          PROPOSAL
             │
             ▼
    DETERMINISTIC POLICIES
             │
             ▼
        AUTHORIZATION
             │
             ▼
         EXECUTORS
             │
       ┌─────┼─────┐
       ▼     ▼     ▼
    GitHub Orbio Stripe
       │     │     │
       └─────┼─────┘
             ▼
    INDEPENDENT VERIFIER
             │
       ┌─────┴─────┐
       ▼           ▼
     LEDGER       AUDIT
       │
       ▼
    REPUTATION
       │
       ▼
    NEXT MISSION

### Core invariant remains unchanged

> **AGENTS PROPOSE → POLICIES AUTHORIZE → EXECUTORS EXECUTE → AUDITORS VERIFY → LEDGER SETTLES**

Future features should extend this invariant, not bypass it.

## What is explicitly NOT the current claim

This roadmap must not be read as saying that Kalyx currently has:
- a complete end-user policy builder
- production adapters for every listed third-party service
- a fully public global AI-agent marketplace
- unrestricted autonomous access to external systems
- production-ready real-money autonomous settlement
- a universal external-agent runtime

Those are future product and infrastructure milestones.

The current implementation provides the control-plane primitives that these systems can be built on.