# Phase 16 — Full System Integration & Autonomous Operations

## Architectural Invariant

```text
AGENTS PROPOSE → POLICIES AUTHORIZE → EXECUTORS EXECUTE → AUDITORS VERIFY
```

Phase 16 builds upon Phase 14B (Governed Resource Acquisition) and Phase 15 (Self-Sustaining Economic Loop), elevating the proven single-loop economic primitive into a continuous, multi-agent autonomous enterprise operating under deterministic solvency regimes.

---

## The Autonomous Cycle

```text
ORB-CYCLE:
  1. Solvency Regime Derivation (EXPANSION / AUSTERE / STANDBY)
  2. Financial Analyst Preflight Evaluation (Unit Economics & Margin Check)
  3. CEO Work Order Prioritization & Selection
  4. Work Order Authorization & Policy Binding
  5. Governed Compute Acquisition (if CREDIT buffer insufficient)
  6. Deliverable Production (OrbioGatewayAdapter / SimulatedWorkExecutor)
  7. Cryptographic Deliverable Verification (HMAC-SHA256 & Evidence Hash)
  8. Revenue Settlement & Surplus Reconciliation (Mission Budget vs Reserves)
  9. Recursive Mission Lineage Tracking
 10. Autonomous Reinvestment into Subsequent Bounded Cycles
```

---

## Solvency Regimes & Financial Postures

| Regime | Treasury Threshold (USDG) | Min Margin Ratio | Reserve Retention | Acquisition Permitted | Posture |
|---|---|---|---|---|---|
| **`EXPANSION`** | $\ge 150$ | 15% | 20% Reserve / 80% Mission Budget | Yes | Aggressive growth, reinvestment in high-yield orders |
| **`AUSTERE`** | $40 \le \text{Bal} < 150$ | 25% | 50% Reserve / 50% Mission Budget | Yes | Defensive operations, higher margin selectivity |
| **`STANDBY`** | $< 40$ | 50% | 100% Reserve | **No** (Halted) | Capital preservation, pauses compute spend until refueled |

---

## Core Components & Modules

| Component | Location | Role |
|---|---|---|
| **`WorkOrderRepository`** | `src/persistence/work_order_repository.py` | Multi-tenant persistent store for work orders, deliverables, receipts, revenue events, and lineage (SQLite & Postgres). |
| **`UnitEconomicsEvaluation`** | `src/agents/schemas.py` | Preflight viability schema capturing margin ratios, projected direct costs, and expected surplus. |
| **`FinancialAnalystAgent`** | `src/agents/roles/financial_analyst.py` | Evaluates work orders against treasury solvency and minimum margin hurdle rates. |
| **`CEOAgent`** | `src/agents/roles/ceo.py` | Prioritizes viable candidate work orders by expected net surplus and margin ratio. |
| **`WorkOrderCoordinator`** | `src/agents/work_order_coordinator.py` | Orchestrates the multi-agent preflight evaluation, order authorization, loop execution, and persistence. |
| **`OrbioGatewayAdapter`** | `src/execution/orbio_gateway_adapter.py` | Real LLM/Compute execution adapter interfacing `/v1/chat/completions` with token telemetry and graceful offline fallback. |
| **`AutonomousDaemon`** | `src/orchestration/autonomous_daemon.py` | Background engine driving sequential execution cycles, adaptive solvency regimes, and recursive lineage. |
| **Database Migrations** | `migrations/postgres/007_work_orders.sql` | PostgreSQL schema definitions matching SQLite tables for zero-drift parity. |

---

## REST API Endpoints

- `GET /api/v1/organisations/{org_id}/work-orders` — List work orders with associated deliverables and receipts.
- `POST /api/v1/organisations/{org_id}/work-orders` — Submit a new client work order.
- `POST /api/v1/organisations/{org_id}/work-orders/{work_order_id}/execute` — Run coordinated execution for a specific work order.
- `GET /api/v1/organisations/{org_id}/missions/lineage` — Inspect recursive mission parent-child lineage.
- `GET /api/v1/organisations/{org_id}/treasury/breakdown` — Real-time solvency regime and cumulative revenue metrics.
- `POST /api/v1/organisations/{org_id}/daemon/step` — Step a single autonomous cycle via the daemon.

---

## Operator UI Panels (`apps/web/`)

1. **Work Orders Console**: Real-time table displaying client work orders, bounties, lifecycle status, and cryptographic deliverable delivery state.
2. **Mission Lineage Telemetry**: Recursive lineage tracker showing total cycles, root missions, and child missions funded strictly from surplus.
3. **Solvency Regime Indicator**: Dynamic status badge in Treasury view signaling `EXPANSION`, `AUSTERE`, or `STANDBY`.
4. **Economic Metrics Strip**: Real-time reporting on cumulative gross revenue, net surplus, and compute consumption.
5. **Step Daemon Control**: Interactive button enabling operators to manually trigger single-step governed autonomous cycles.
