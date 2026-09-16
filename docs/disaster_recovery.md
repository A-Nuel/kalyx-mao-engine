# Kalyx MAO Engine: Disaster Recovery & Operational Runbook

This document defines the disaster recovery (DR) architecture, backup/restore procedures, post-recovery integrity gates, and operational reconciliation runbook for Kalyx production deployments.

---

## 1. Core Recovery Invariants

Across any crash, network partition, node eviction, or database restore:

1. **State Isolation**:
   - `UNKNOWN` consequential operations must NEVER be blindly retried upon application restart.
   - Authoritative operation states (`ESCROWED`, `SUBMITTED`, `UNKNOWN`, `RECONCILING`, `RECONCILED`) survive in persistent storage.

2. **Economic Conservation**:
   - Total resources across accounts must satisfy conservation invariants:
     $$\text{TREASURY} + \text{ESCROW} + \text{EXTERNAL\_SINK} = \text{TOTAL\_INITIAL\_TREASURY}$$
   - No credits may be created, leaked, or destroyed across a crash or recovery cycle.

3. **Reconciliation Atomicity**:
   - Each `UNKNOWN` operation must reconcile to exactly one economic settlement:
     - Remote **SUCCESS** $\rightarrow$ `ESCROW` to `EXTERNAL_SINK`.
     - Remote **FAILURE** $\rightarrow$ `ESCROW` refunded to `TREASURY`.
   - Repeated reconciliation attempts are strictly idempotent no-ops.

4. **Audit Cryptographic Continuity**:
   - The SHA-256 event hash chain (`previous_event_hash` $\rightarrow$ `event_hash`) must remain intact across restarts and recovery.
   - Restoring from backup must verify hash chain integrity before serving API traffic.

---

## 2. Backup Procedures

### 2.1 PostgreSQL (Authoritative Production Datastore)

In production (`KALYX_ENV=production`), PostgreSQL is the authoritative persistent store.

#### Automated Nightly Logical Backup
```bash
# Dump entire Kalyx database in custom compressed format
pg_dump \
  --format=custom \
  --blobs \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --dbname="${KALYX_DATABASE_URL}" \
  --file="/backups/kalyx_$(date +%Y%m%d_%H%M%S).dump"
```

#### Consistent Point-in-Time Recovery (PITR)
- Ensure Write-Ahead Logging (WAL) archiving is active: `wal_level = replica`, `archive_mode = on`.
- Ship archived WAL segments to encrypted cloud object storage (S3/GCS) every 60 seconds.

### 2.2 SQLite (Development & Demo Deployments)

For local/demo deployments using SQLite:
```bash
# Atomic online backup using sqlite3 VACUUM INTO
sqlite3 "${KALYX_DB:-kalyx.db}" "VACUUM INTO '/backups/kalyx_backup_$(date +%Y%m%d_%H%M%S).db';"
```

---

## 3. Restore Procedures

### 3.1 PostgreSQL Restore

1. **Stop Kalyx Service Instances**:
   ```bash
   kubectl scale deployment/kalyx-control-plane --replicas=0
   ```
2. **Restore Database from Dump**:
   ```bash
   pg_restore \
     --clean \
     --if-exists \
     --no-owner \
     --no-privileges \
     --dbname="${KALYX_DATABASE_URL}" \
     /backups/kalyx_target.dump
   ```
3. **Execute Pending Migrations**:
   ```bash
   python scripts/run_postgres_migrations.py
   ```

### 3.2 Post-Restore Verification Gates

Before bringing the application online, run the post-restore verification script:

```bash
python scripts/verify_system_integrity.py
```

This verification script performs:
1. **Ledger Conservation Audit**:
   Queries all tenant and organisation ledger accounts and checks that sum of balances matches deposit totals.
2. **Audit Hash Chain Audit**:
   Iterates through the sequence of audit events, re-computing:
   $$\text{expected\_hash} = \text{SHA256}(\text{sequence\_id} \parallel \text{timestamp} \parallel \text{actor\_id} \parallel \text{event\_type} \parallel \text{entity\_id} \parallel \text{payload\_hash} \parallel \text{previous\_event\_hash})$$
   Fails closed if any hash mismatch is detected.
3. **Unresolved Operations Report**:
   Lists all consequential operations remaining in `UNKNOWN` or `RECONCILING` state.

---

## 4. Operational Reconciliation Runbook

When the system restarts following an unexpected crash or network partition, consequential operations may remain in the `UNKNOWN` state.

### Step 1: Identify Unresolved Operations

Via API:
```bash
curl -s -H "Authorization: Bearer ${OPERATOR_TOKEN}" \
     -H "X-Tenant-ID: ${TENANT_ID}" \
     https://kalyx.internal/api/organisations/${ORG_ID}/operations/summary
```

Response indicates unresolved count:
```json
{
  "total_operations": 12,
  "counts": {
    "unknown": 2,
    "reconciled": 10
  },
  "unresolved_count": 2,
  "total_escrowed": 50,
  "has_unknown": true
}
```

### Step 2: Trigger Controlled Reconciliation

Trigger reconciliation for each unresolved operation:
```bash
curl -X POST -s \
     -H "Authorization: Bearer ${OPERATOR_TOKEN}" \
     -H "X-Tenant-ID: ${TENANT_ID}" \
     https://kalyx.internal/api/organisations/${ORG_ID}/operations/${OP_ID}/reconcile
```

The `ReconciliationService`:
1. Acquires row-level / advisory lock on the operation.
2. Queries the remote provider adapter for definitive settlement status (`query_status`).
3. If provider confirms SUCCESS:
   - Commits `ESCROW` to `EXTERNAL_SINK`.
   - Transitions state to `RECONCILED`.
   - Records `CONSEQUENTIAL_OPERATION_RECONCILED` audit event.
4. If provider confirms FAILURE:
   - Rolls back `ESCROW` to `TREASURY`.
   - Transitions state to `RECONCILED`.
   - Records `CONSEQUENTIAL_OPERATION_RECONCILED` audit event.
5. If provider reports PENDING / TIMEOUT:
   - Preserves `UNKNOWN` state and `ESCROW` balance.
   - Refuses to settle or refund until upstream status resolves.

---

## 5. Automated Disaster Recovery Testing

To prevent regressions in crash recovery semantics, Kalyx includes an automated end-to-end disaster recovery drill:

```bash
python -m pytest tests/integration/test_disaster_recovery.py -v
```

This test simulates:
- Mission creation and policy authorization.
- Consequential operation dispatch with simulated upstream network drop.
- Mid-execution hard crash (destruction of memory, DB connections, and execution managers).
- Cold restart from the persistent database.
- Integrity verification of ledger balances, operation states, and audit hash chains across the crash boundary.
- Post-recovery reconciliation settling remote success and refunding remote failure.
