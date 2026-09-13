# Phase 9 — Identity & True Multi-Organisation Architecture

## Objective

Replace the Phase 7 single-organisation-per-database guard with a real tenant/workspace/organisation boundary that is safe to run multiple organisations in one database.

The Phase 9 security invariant is:

> **Every request executes inside an authenticated tenant context; every organisation/resource lookup is scoped to that context; every economic operation is scoped to exactly one organisation treasury.**

A client-supplied tenant ID is never trusted as authorization.

## Target hierarchy

```text
Platform
└── Tenant / Workspace
    ├── Organisation A
    │   ├── Missions
    │   ├── Agents
    │   ├── Treasury / Escrow
    │   ├── Policies
    │   └── Audit
    └── Organisation B
        ├── Missions
        ├── Agents
        ├── Treasury / Escrow
        ├── Policies
        └── Audit
```

## Identity model

Phase 9 introduces three distinct concepts:

- **Principal** — authenticated human/service identity making an API request.
- **Tenant** — security and ownership boundary for a workspace/customer.
- **Organisation** — autonomous economic/operational unit owned by a tenant.

A principal may belong to multiple tenants and may have different roles in each tenant.

Minimum membership roles:

- `owner` — full tenant administration and organisation control.
- `operator` — run/pause/resume missions and manage operational resources.
- `viewer` — read-only access.

Future roles can be added without changing resource ownership semantics.

## Request context

Introduce an immutable request context derived from authentication:

```text
Request
  ↓
Authentication
  ↓
Principal
  ↓
TenantMembership
  ↓
TenantContext
  ↓
Organisation authorization
  ↓
Scoped repository / ledger / policy / execution
```

The API must not use `X-Tenant-ID` or a JSON `tenant_id` field as proof of ownership. In local/demo mode a trusted development principal may be injected, but production authentication must establish the tenant context server-side.

## Data model requirements

Existing persistent entities must become explicitly tenant/organisation scoped where applicable.

Required ownership relationships:

- `tenants.id`
- `tenant_memberships.tenant_id + principal_id`
- `organisations.tenant_id`
- `missions.organisation_id` and `missions.tenant_id`
- `agents.organisation_id` and `agents.tenant_id`
- `tasks.organisation_id` and `mission_id`
- `proposals.organisation_id` and `task_id`
- `policy_decisions.organisation_id` and `proposal_id`
- `execution_receipts.organisation_id` and `proposal_id`
- `verification_receipts.organisation_id` and `execution_id`
- ledger entries scoped to an organisation treasury namespace
- idempotency operations scoped to tenant + operation identity

Where a child already has a foreign key to a parent, the organisation relationship should still be represented or derivable in a way that permits database-level ownership checks and prevents accidental cross-tenant joins.

## IDs

IDs must be unique across the database. Do not solve tenant isolation by making IDs reusable between tenants.

Use opaque/random identifiers for externally visible resource IDs. Human-readable prefixes are acceptable for display but are not an authorization mechanism.

## Organisation treasury isolation

Tenant isolation alone is insufficient: two organisations belonging to the same tenant must not share a treasury.

Canonical account namespace:

```text
<tenant_id>:<organisation_id>:TREASURY
<tenant_id>:<organisation_id>:ESCROW
<tenant_id>:<organisation_id>:<agent_id>
```

The ledger facade should make the organisation scope implicit after construction. Callers should request `TREASURY`, `ESCROW`, or an agent account rather than constructing global account strings.

The organisation-scoped ledger must enforce:

1. account names cannot escape its tenant/organisation namespace;
2. balance reads cannot see another organisation;
3. transfers cannot move credits between organisations through the scoped interface;
4. treasury minting requires an explicit trusted provisioning path;
5. conservation is checked within the organisation scope;
6. global transaction IDs remain globally unique.

Cross-organisation transfers, if ever needed, must be a separate explicitly authorized protocol rather than an accidental side effect of the normal ledger API.

## Repository isolation

Repositories must expose scoped methods instead of relying on callers to remember filters.

Preferred pattern:

```python
repo = repository.for_organisation(context, organisation_id)
repo.get_mission(mission_id)
repo.list_agents()
repo.save_task(task)
```

A scoped repository must verify that the organisation belongs to the authenticated tenant before reading or mutating it.

Never implement security by:

```python
SELECT * FROM agents WHERE id = ?
```

when the caller is tenant-scoped. The secure form must bind the ownership boundary in the query or verify it through an authoritative parent relation before returning the resource.

## API authorization

Every organisation-scoped endpoint must perform both:

1. **Authentication** — who is making the request?
2. **Authorization** — does that principal have the required role for this tenant/organisation?

Expected behaviour:

- unauthenticated production request → `401`;
- authenticated but not a tenant member → `403`;
- authenticated tenant member accessing another tenant's organisation → `404` or `403` according to the chosen enumeration policy;
- viewer attempting a mutation → `403`;
- valid operator/owner mutation → continue into the existing governance control plane.

Resource existence must not be leaked through cross-tenant error differences.

## Mission service changes

Remove the current Phase 7 guard:

```text
Phase 7 MVP supports one persistent organisation per database
```

Mission creation must instead:

1. authenticate principal;
2. resolve tenant membership;
3. create a new organisation under that tenant;
4. create an organisation-scoped treasury with the requested initial budget;
5. create globally unique agent IDs;
6. create globally unique mission/task/proposal IDs;
7. construct all governance/execution components with the same organisation scope;
8. persist the complete ownership graph atomically enough that a partial creation cannot expose a usable half-created organisation.

## Audit scope

The audit chain currently spans the database globally. Phase 9 must preserve global cryptographic integrity while adding tenant and organisation ownership to event payload/metadata.

Audit readers must be scoped. A tenant must never be able to retrieve another tenant's audit events through an organisation endpoint.

Whether the final production architecture keeps one global chain or introduces independent per-tenant chains is an implementation decision; integrity and isolation are separate requirements.

## Idempotency scope

An idempotency key must be bound to the authenticated principal/tenant and the operation fingerprint. A caller in tenant A must not be able to replay an operation from tenant B by reusing its key.

For organisation mutations, bind at minimum:

```text
tenant_id + organisation_id + operation + idempotency_key + request_fingerprint
```

## Migration strategy

Phase 9 should be backward-compatible with the existing demo database where practical:

1. add membership/identity tables;
2. add missing ownership columns and indexes;
3. backfill existing data into `tenant-demo` and its existing organisation;
4. enforce non-null ownership after backfill;
5. introduce scoped repositories and organisation ledger;
6. remove the one-organisation guard;
7. add multi-tenant adversarial tests;
8. only then expose multi-organisation creation through the API/UI.

Do not silently reinterpret an existing organisation's treasury. The authoritative ledger remains the source of economic truth.

## Phase 9 adversarial test gate

The phase is not complete until tests demonstrate:

- tenant A cannot read tenant B organisations;
- tenant A cannot list tenant B agents/tasks/proposals/receipts;
- tenant A cannot read tenant B audit events;
- tenant A cannot pause/resume tenant B organisations;
- tenant A cannot run a mission in tenant B;
- organisation A and B under the same tenant have independent treasuries;
- spending in organisation A cannot reduce organisation B's balance;
- agent IDs cannot collide across organisations;
- task/proposal/receipt IDs cannot collide across organisations;
- idempotency keys cannot cross tenant boundaries;
- a forged tenant header cannot change request scope;
- a forged organisation ID cannot escape the authenticated tenant;
- a viewer cannot mutate an organisation;
- suspended/inactive principals cannot perform privileged operations;
- concurrent requests cannot provision or spend the same treasury incorrectly;
- deleting/invalidating membership immediately removes access;
- all existing Phase 8 security tests remain green.

## Non-goals

Phase 9 does **not** make Kalyx ready for serious real-world money custody. It establishes the identity and isolation boundary required before real execution adapters are introduced.

Real-money readiness still requires provider-specific settlement, key management, reconciliation, operational controls, disaster recovery, and further adversarial/security review.

## Release gate

Phase 9 is complete only when:

```text
Authenticated Principal
        ↓
Tenant Membership
        ↓
Organisation Scope
        ↓
Scoped Repository + Scoped Ledger
        ↓
Existing Policy / Authorization
        ↓
Existing Executor
        ↓
Existing Auditor
```

is enforced end-to-end, with adversarial tests proving that changing tenant or organisation identifiers cannot cross the boundary.
