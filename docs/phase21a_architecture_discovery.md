# Phase 21A — Multi-User / Multi-Organisation Architecture Discovery

**Status:** DISCOVERY COMPLETE — NO RUNTIME CODE CHANGED  
**Baseline:** `main` after Phase 20B merge  
**Invariant:** **AGENTS PROPOSE → POLICIES AUTHORIZE → EXECUTORS EXECUTE → AUDITORS VERIFY → LEDGER SETTLES**

## Executive finding

Kalyx is **already partially multi-tenant**. Earlier phases introduced Tenant, Principal, Membership, Organisation, tenant-scoped ledgering, production identity checks, PostgreSQL migrations, and explicit deferred-RLS design.

The current architecture is best described as **application-enforced multi-tenant control plane with partial tenant propagation**, not yet a complete multi-user platform.

The right Phase 21 strategy is therefore to consolidate and harden the existing architecture rather than bolt on a second identity system.

## 1. Current tenancy

Existing foundations:

- `src/tenancy/models.py` — `Tenant`
- `src/tenancy/context.py` — `TenantContext`
- `src/tenancy/ledger.py` — `TenantScopedLedger`
- `src/identity/models.py` — `Principal`, `Membership`, `MembershipRole`
- `src/identity/context.py` — `IdentityContext`
- `src/identity/repository.py` — persisted principals/memberships
- `src/api/identity_auth.py` — authenticated tenant/organisation authorization
- `docs/phase9_identity_multiorg.md` — Phase 9 multi-organisation architecture
- `docs/persistence_postgres.md` — tenant isolation/RLS design

Important current property: `X-Tenant-ID` is a scope selector, not proof of authorization; authenticated mode checks persisted membership.

### Main gap

Tenant propagation is inconsistent.

Some entities carry both `tenant_id` and `organisation_id`; other core entities such as `agents`, `tasks`, `proposals`, `policy_decisions`, and `execution_receipts` reach tenancy mainly through organisation/task relationships.

This can work when ownership and repository scoping are correct, but it is harder to prove and complicates future RLS/composite constraints.

**Do not blindly add `tenant_id` everywhere. First define canonical ownership and audit every access path.**

## 2. Identity model

Current identity is:

`Principal → Membership → Tenant`

Roles are OWNER, ADMIN, OPERATOR, VIEWER.

There is no separate User entity; Principal currently represents a human/service identity.

Missing or insufficiently explicit first-class concepts:

- organisation-level membership
- wallet
- execution authority
- wallet/account binding for approvals
- credential/API-key ownership

Future distinction should be:

`Principal/User` = authenticated actor  
`Membership` = actor's authorization relationship  
`Organisation` = economic/governance boundary  
`Agent` = autonomous worker  
`Wallet` = public blockchain identity  
`ExecutionAuthority` = mechanism permitted to sign/authorize execution  
`Approval` = human authorization over exact intent  
`Credential` = external-service authentication

## 3. Persistence

SQLite has a substantial schema in `src/persistence/database.py`; PostgreSQL migrations exist under `migrations/postgres/`.

The schema already covers identity, organisations, agents, proposals, policy decisions, execution receipts, ledgers, audit events, consequential operations, marketplace state, circuit breakers and approvals.

### RLS

Full PostgreSQL RLS is **not enabled**.

`docs/persistence_postgres.md` explicitly treats application authorization as the primary boundary and RLS as deferred until a trusted `app.current_tenant` can be established from authenticated membership context.

Therefore:

**Current primary isolation = application authorization.**  
**Future defense-in-depth = PostgreSQL RLS.**

RLS should follow canonical context, not precede it.

## 4. Execution authority

`src/settlement/blockchain/signer.py` already contains:

- `IBlockchainSigner`
- `LocalKeySigner`
- `ExternalTransactionSigner`

The external signer preserves private-key isolation and cryptographically verifies the recovered signer against the authorized address.

The remaining architectural gap is that this is still an EVM/blockchain signer abstraction, not a generic organisation-level `ExecutionAuthority`.

Target:

`Organisation → ExecutionAuthority → provider-specific adapter → execution boundary`

Possible adapters later include external signer, EIP-1193, WalletConnect, Safe/multisig and KMS/HSM.

Agents should not know which implementation is underneath.

## 5. Wallet integration

No first-class persisted Wallet model or wallet connection lifecycle was found.

The current external signer is intentionally compatible with externally held keys, including mobile wallets, without exporting private keys to Kalyx.

Wallet technology belongs behind the execution-authority boundary. Kalyx governance should receive typed intent + authorization + execution evidence, not wallet UI mechanics.

## 6. Human approval

Existing infrastructure includes:

- policy escalation to human
- cryptographically bound approvals
- `admin_approvals`
- `AdminGovernanceManager`
- expiry and consumed/replay protection
- payload hashing
- approver identity
- 2-of-2 circuit-breaker approval
- Orbio human approval bound to exact intent hash

Existing conceptual path:

`Intent → Policy → Human approval → Authorize → Escrow → Submit → Verify`

The missing platform-level relationship is:

`authenticated principal → eligible organisation role → exact intent → approval → wallet/account/execution authority`

An `approver_id` must not by itself be treated as proof of wallet identity.

## 7. Security gaps to attack in Phase 21

Test explicitly for:

1. cross-tenant organisation lookup
2. cross-organisation access
3. reuse of an organisation ID across tenants
4. cross-organisation intent reuse
5. approval replay across organisations
6. approval reuse after intent mutation
7. signer substitution
8. wallet substitution
9. stale nonce reuse
10. changing execution authority while an intent is pending
11. role downgrade while approval remains valid
12. credential/API-key scope confusion
13. tenant context loss between API → service → executor
14. ledger namespace collisions
15. audit records missing ownership scope

The PostgreSQL initial audit schema also deserves explicit review because its audit table does not initially establish the same tenant/organisation ownership columns present in many later tables.

## 8. Minimum future domain model

`Tenant` — workspace/security boundary  
`Principal/User` — authenticated identity  
`Membership` — tenant/org relationship and role  
`Organisation` — economic/governance boundary  
`Agent` — autonomous actor  
`Policy` — deterministic constraints  
`Wallet` — public address/network metadata; never private keys  
`ExecutionAuthority` — configured signing/authorization mechanism  
`Approval` — human authorization bound to exact intent  
`Intent` — typed consequential proposal  
`Execution` — durable execution lifecycle  
`AuditEvent` — append-only evidence  
`Ledger` — authoritative economic accounting

Target relationship:

`Tenant → Membership → Organisation → Agent/Policy/Wallet/ExecutionAuthority → Intent → Approval → Execution → Audit/Ledger`

## 9. Migration strategy

Do not replace existing governance infrastructure.

### 21B — Canonical context
Create one trusted request/execution context carrying principal, tenant, organisation, membership role and correlation ID.

### 21C — Ownership/isolation hardening
Classify every entity as tenant-owned, organisation-owned, global/system-owned or execution-owned. Normalize repository scoping and adversarial tests.

### 21D — ExecutionAuthority
Introduce a generic authority protocol and adapt the existing signer implementations into it.

### 21E — Wallet boundary
Research/implement EIP-1193/WalletConnect-style adapters without putting private keys into Kalyx.

### 21F — Durable human approval
Bind approval to principal + organisation + intent hash + policy decision + execution authority, with expiry/replay protection.

### 21G — Multi-organisation E2E
Prove two organisations independently complete:

`propose → authorize → execute → verify → settle`

with no cross-access.

### 21H — Security gate
Adversarially test tenant escape, organisation escape, signer substitution, approval replay, wallet substitution, stale approvals and credential confusion.

## Phase 21A conclusion

Kalyx already has substantial multi-tenant infrastructure. The next step is **not a new user system**.

The correct next move is to make this chain canonical and testable:

**Principal → Membership → Tenant → Organisation → Policy → Intent → Approval → Execution Authority → Execution → Verification → Ledger**

Phase 21B should focus on canonical identity/organisation context while preserving every existing Phase 20B execution boundary and the core governance invariant.
