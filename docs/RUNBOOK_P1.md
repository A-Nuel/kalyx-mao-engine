# Kalyx Autonomous DAO — Phase 17 P1 Production Runbook

## 1. System Topography & Core Invariant

The Kalyx MAO Engine executes a 6-stage autonomous economic loop:
```
B2B ORDER -> POLICY AUTHORIZATION -> ESCROW -> CLAIM -> CAPABILITY -> PRODUCTIVE EXECUTION -> INDEPENDENT AUDIT -> ESCROW RELEASE -> VERIFIED REVENUE -> SURPLUS -> NEXT MISSION
```

**Non-Negotiable Architecture Invariant:**
```
AGENTS PROPOSE -> POLICIES AUTHORIZE -> EXECUTORS EXECUTE -> AUDITORS VERIFY
```
Agents NEVER have authority to self-grant capabilities, release escrows, or mutate financial ledgers directly.

---

## 2. Local & Container Startup

### Local Execution (File-Backed SQLite):
```bash
export KALYX_ENV=demo
export KALYX_DB=data/kalyx.db
export KALYX_POLICY_SECRET="your-32-character-or-longer-secret-key"
uvicorn src.api.server:app --host 0.0.0.0 --port 8000
```

### Docker Compose Execution:
```bash
# Start API server with persistent volume kalyx-data
docker compose up -d kalyx-api

# Execute Phase 17 autonomous economic loop demo
docker compose run --rm kalyx-demo

# Run full regression test suite inside container
docker compose run --rm kalyx-test
```

---

## 3. Configuration & Provenance

| Parameter | Recommended Demo | Production Requirement |
|---|---|---|
| `KALYX_ENV` | `demo` | `production` (strict fail-closed checks) |
| `KALYX_DB` | `/app/data/kalyx.db` | PostgreSQL via `KALYX_DATABASE_URL` |
| `KALYX_IDENTITY_AUTH` | `demo` | `production` (Bearer token JWT validation) |
| `KALYX_POLICY_SECRET` | 32+ char key | High-entropy secret from secure KMS |

**Provenance Truthfulness:**
- `SIMULATED`: Orbio compute and deliverable execution performed via local deterministic mocks.
- `TESTNET`: Ethereum Sepolia settlement enabled for test currency.
- `LIVE`: Real Orbio API keys and real settlement networks. Simulated actions must NEVER be labeled as LIVE.

---

## 4. Emergency Circuit Breaker

The circuit breaker is scoped to `(tenant_id, organisation_id)` and authoritative in the database:
- While `PAUSED`: New order publication, claims, and capital allocations are blocked.
- While `PAUSED`: Deliverable verification and reconciliation of already-verified work remain permitted.

### Activation (Single Operator Emergency Action):
```python
from src.governance.circuit_breaker import SystemCircuitBreaker
from src.persistence.database import Database

db = Database("data/kalyx.db")
cb = SystemCircuitBreaker(db)
cb.pause(
    tenant_id="tenant-alpha",
    organisation_id="org-alpha",
    operator_id="operator-admin-1",
    reason="Anomalous deliverable patterns detected",
)
```

### Governed Resumption (Strict 2-of-2 Multi-Signature Quorum):
```python
from src.governance.admin_governance import AdminGovernanceManager

gov = AdminGovernanceManager(db, secret_key=POLICY_SECRET)
# 1. Collect approval from Admin 1
app1 = gov.create_approval(
    tenant_id="tenant-alpha",
    action_type="CIRCUIT_BREAKER_RESUME",
    target_id="org-alpha",
    payload={"organisation_id": "org-alpha"},
    approver_id="admin-security-lead",
)
# 2. Collect approval from Admin 2 (must be distinct identity)
app2 = gov.create_approval(
    tenant_id="tenant-alpha",
    action_type="CIRCUIT_BREAKER_RESUME",
    target_id="org-alpha",
    payload={"organisation_id": "org-alpha"},
    approver_id="admin-operations-lead",
)
# 3. Verify and consume 2-of-2 quorum
valid, err = gov.verify_and_consume_quorum(
    approvals=[app1, app2],
    required_approvals=2,
    expected_tenant_id="tenant-alpha",
    expected_action_type="CIRCUIT_BREAKER_RESUME",
    expected_target_id="org-alpha",
    actual_payload={"organisation_id": "org-alpha"},
)
if valid:
    cb.resume(
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
        approver_ids=["admin-security-lead", "admin-operations-lead"],
    )
```

---

## 5. Crash & Restart Recovery

The system state is fully durable across process/container restarts.

### Diagnostic Recovery Scan:
```python
from src.persistence.recovery import Phase17RecoveryScanner

scanner = Phase17RecoveryScanner(db)
dangling = scanner.scan_dangling_escrows()
stale = scanner.scan_stale_claims()
unknowns = scanner.scan_unknown_consequential_operations()
```

**Recovery Invariants:**
- `UNKNOWN` operations are NEVER promoted to `SUCCEEDED` without independent proof.
- Dangling escrows remain `HELD` until reviewed.
- Unverified revenue NEVER flows to next-generation mission budgets.

---

## 6. Safety Verification Summary

1. **Agents cannot self-authorize**: Verified via `CapabilityEvolutionRule` and `PolicyEngine`.
2. **Double-spend protection**: Enforced via `DoubleEntryLedger` balanced journal entries.
3. **Cross-tenant isolation**: Enforced via compound keys `(tenant_id, organisation_id)` and sanitized `PublicMarketplaceOrder` DTO.
4. **Idempotency**: 3-state canonical hash evaluation (`NEW`, `DUPLICATE`, `CONFLICT`) prevents duplicate revenue or split.
