# Phase 10 — Consequential Execution & Settlement Boundary

## Executive Overview

Phase 10 addresses the core architectural thesis of Kalyx:
> **"Can an autonomous organisation safely cause consequential actions in external systems without allowing the LLM itself to become the authority?"**

In autonomous AI organisations, models produce ideas, strategies, and code. However, models suffer from hallucinations, non-determinism, and vulnerability to prompt injections. If an agent could invoke external APIs or on-chain contracts directly, an autonomous system would be inherently unsafe.

Kalyx enforces the non-negotiable architectural invariant:
```text
AGENTS PROPOSE → POLICIES AUTHORIZE → EXECUTORS EXECUTE → AUDITORS VERIFY
```

The **Consequential Execution & Settlement Boundary** guarantees that:
1. Agents cannot directly trigger external side-effects or spend treasury funds.
2. Every consequential operation is backed by a cryptographically signed, unreplayable authorization token bound to a policy decision and proposal fingerprint.
3. Every operation moves through an authoritative, durable state machine backed by persistent storage.
4. Economic settlement is atomic: funds are locked in escrow prior to provider dispatch and are never double-spent or lost in-flight.
5. In transport timeouts or uncertain external states, the system **fails closed**: escrow remains locked until independent reconciliation verifies the provider's ground truth.
6. The entire lifecycle is verifiable by an independent auditor.

---

## Authoritative State Machine

The consequential operation lifecycle is managed authoritatively by `ConsequentialExecutionManager` (`src/execution/consequential.py`):

```text
       ┌───────────┐
       │  CREATED  │
       └─────┬─────┘
             │ validate token & capability
             ▼
      ┌──────────────┐
      │  AUTHORIZED  │
      └──────┬───────┘
             │ lock escrow (TREASURY -> ESCROW)
             ▼
       ┌───────────┐
       │  ESCROWED │
       └─────┬─────┘
             │ record idempotency & dispatch to provider
             ▼
       ┌───────────┐
       │ SUBMITTED │
       └─────┬─────┘
             ├───────────────────────┬────────────────────────┐
             │ provider success      │ provider timeout/err   │ provider reject
             ▼                       ▼                        ▼
       ┌───────────┐           ┌───────────┐            ┌───────────┐
       │ SUCCEEDED │           │  UNKNOWN  │            │   FAILED  │
       └───────────┘           └─────┬─────┘            └───────────┘
                                     │ start reconciliation
                                     ▼
                              ┌─────────────┐
                              │ RECONCILING │
                              └──────┬──────┘
                                     ├────────────────────────┐
                                     │ still unknown          │ final outcome confirmed
                                     ▼                        ▼
                              ┌─────────────┐          ┌──────────────┐
                              │   UNKNOWN   │          │  RECONCILED  │
                              └─────────────┘          └──────────────┘
```

### Transition Invariants

| From State | Allowed To States | Rationale |
| :--- | :--- | :--- |
| `CREATED` | `AUTHORIZED` | Validates capability, signature, replay status, and parameters. |
| `AUTHORIZED` | `ESCROWED` | Moves required credits from `TREASURY` into isolated `ESCROW`. |
| `ESCROWED` | `SUBMITTED`, `FAILED` | Submits payload to provider; fails if pre-flight dispatch validation fails. |
| `SUBMITTED` | `SUCCEEDED`, `FAILED`, `UNKNOWN`, `RECONCILING` | Terminal direct outcome or transition to pending state (including crash recovery). |
| `UNKNOWN` | `RECONCILING` | Begins authoritative provider inquiry. |
| `RECONCILING` | `RECONCILED`, `UNKNOWN` | Finalizes once provider truth is known, or reverts to `UNKNOWN` if still unresolved. |
| `SUCCEEDED` | *(Terminal)* | Escrow committed (`ESCROW -> EXTERNAL_SINK`). |
| `FAILED` | *(Terminal)* | Escrow rolled back (`ESCROW -> TREASURY`). |
| `RECONCILED` | *(Terminal)* | Reconciled settlement executed exactly once. |

---

## Escrow & Economic Atomicity

Kalyx enforces double-entry ledger conservation across every step of execution:

$$\sum \text{Debits} = \sum \text{Credits}$$

### 1. Pre-Execution Escrow Lock
Before any external provider dispatch occurs, the required credits are transferred:
```text
DEBIT:  {tenant}:{org}:TREASURY
CREDIT: {tenant}:{org}:ESCROW
```
The organisation cannot overdraw its balance, and in-flight operations cannot double-commit resources.

### 2. Immediate Success
If the external provider acknowledges success synchronously:
```text
DEBIT:  {tenant}:{org}:ESCROW
CREDIT: {tenant}:{org}:EXTERNAL_SINK
```
The operation transitions to `SUCCEEDED` and the authorization token is marked consumed.

### 3. Immediate Failure
If the provider explicitly returns a fatal error or network rejection:
```text
DEBIT:  {tenant}:{org}:ESCROW
CREDIT: {tenant}:{org}:TREASURY
```
Credits are refunded to the treasury and the operation transitions to `FAILED`.

### 4. Transport Timeout / Network Uncertainty (`UNKNOWN`)
When a network connection times out or drops after dispatch, the remote system may have processed the action or dropped it. **Kalyx never blindly refunds escrow on timeout.** Doing so would allow an organisation to execute actions externally while recovering its internal credits (a classic double-spend exploit).
Instead:
- The operation transitions to `UNKNOWN`.
- The escrow remains locked in `ESCROW`.
- The operation must be resolved via the `ReconciliationService`.

---

## Reconciliation Engine

The `ReconciliationService` (`src/settlement/reconciliation.py`) inspects pending operations against external provider truth:

```python
reconciliation_service = ReconciliationService(
    operation_repo=op_repo,
    provider=provider_adapter,
    ledger=ledger,
    audit_chain=audit_chain,
)
result_op = reconciliation_service.reconcile_operation(
    tenant_id=tenant_id,
    organisation_id=org_id,
    operation_id=operation_id,
)
```

### Reconciliation Properties

1. **Tenant & Organisation Isolation**: Cross-tenant reconciliation requests are rejected immediately.
2. **Crash Recovery**: If an engine crashes during dispatch (`SUBMITTED`), startup recovery transitions in-flight operations to `UNKNOWN` and queues them for reconciliation.
3. **Idempotent Exactly-Once Settlement**: If an operation is already `SUCCEEDED`, `FAILED`, or `RECONCILED`, duplicate reconciliation calls return the current state without duplicating ledger transactions.
4. **Definitive Reconciled Settlement**:
   - Provider reports `SUCCESS`:
     - Escrow commits: `ESCROW -> EXTERNAL_SINK` with transaction ID `tx-rec-{op_hash}`.
     - State transitions to `RECONCILED`.
     - Audited as `CONSEQUENTIAL_OP_RECONCILED_SUCCESS`.
   - Provider reports `FAILURE`:
     - Escrow refunds: `ESCROW -> TREASURY` with transaction ID `rollback-rec-{op_hash}`.
     - State transitions to `RECONCILED`.
     - Audited as `CONSEQUENTIAL_OP_RECONCILED_FAILURE`.
   - Provider reports `PENDING` / still unreachable:
     - Escrow remains in `ESCROW`.
     - State returns to `UNKNOWN`.
     - Audited as `CONSEQUENTIAL_OP_RECONCILE_STILL_PENDING`.

---

## Simulated Provider Adapter

`SimulatedConsequentialProvider` (`src/settlement/simulated_provider.py`) simulates realistic external Web2/Web3 settlement without risking real financial custody:

- **Isolated External State Store**: Maintains its own record of operations, mock transactions, and outcomes independent of the Kalyx database.
- **Configurable Fault Injection**:
  - `set_timeout_rule(idempotency_key, eventual_outcome)`: Simulates network drop upon submission, then records the outcome out-of-band for background polling.
  - `set_failure_rule(idempotency_key, error_message)`: Simulates explicit downstream provider failures.
- **Provider Protocol**: Implements `ConsequentialProviderAdapter` with `execute_consequential(...)` and `check_status(...)`.

---

## Independent Auditor Verification

The `Auditor` (`src/audit/auditor.py`) verifies consequential operations without trusting the execution manager's internal assertions:

```python
verification = auditor.verify_consequential_operation(
    operation=op,
    token=token,
    stored_proposal=proposal,
    ledger=ledger,
)
assert verification.is_valid
```

The verification checks:
1. **Proposal Content Fingerprint**: Verifies SHA-256 hash of proposal fields against token proposal hash.
2. **Cryptographic Token Signature**: Verifies HMAC-SHA256 signature using the system policy secret and validates timestamp expiry.
3. **Operation Binding**: Confirms `operation.token_nonce == token.nonce` and `operation.proposal_id == token.proposal_id`.
4. **Provider Execution Evidence**: Verifies non-empty provider reference and external outcome proof.
5. **Ledger Settlement Transactions**: Inspects the double-entry journal to confirm exact credit movements match operation state (`SUCCEEDED`, `FAILED`, or `RECONCILED`).

---

## Multi-Tenant API Endpoints

The API exposes isolated operations endpoints (`src/api/server.py`):

### 1. List Operations
```http
GET /api/organisations/{org_id}/operations?state=UNKNOWN
X-Tenant-ID: tenant-prod-1
```
Returns all operations belonging to the specified organisation and tenant, optionally filtered by state.

### 2. Get Operation Detail
```http
GET /api/organisations/{org_id}/operations/{op_id}
X-Tenant-ID: tenant-prod-1
```
Returns operation details, state, escrow amounts, provider reference, and error message.

### 3. Trigger Reconciliation
```http
POST /api/organisations/{org_id}/operations/{op_id}/reconcile
X-Tenant-ID: tenant-prod-1
```
Triggers authoritative reconciliation against the provider. Returns updated `ConsequentialOperation`.

---

## Dual-Backend Schema

The schema is synchronized across SQLite and PostgreSQL:

```sql
CREATE TABLE consequential_operations (
    operation_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    token_nonce TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK(state IN (
        'CREATED', 'AUTHORIZED', 'ESCROWED', 'SUBMITTED',
        'SUCCEEDED', 'UNKNOWN', 'FAILED', 'RECONCILING', 'RECONCILED'
    )),
    target_system TEXT NOT NULL,
    action_type TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    escrow_amount_credits INTEGER NOT NULL,
    provider_reference TEXT,
    provider_outcome TEXT,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_consequential_ops_tenant_org 
    ON consequential_operations (tenant_id, organisation_id);
CREATE INDEX idx_consequential_ops_state 
    ON consequential_operations (state);
```
