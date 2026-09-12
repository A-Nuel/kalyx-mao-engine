# Kalyx MAO Engine (Minimum Autonomous Organisation)

> **Agents propose. Policies authorize. Executors execute. Auditors verify.**

Kalyx MAO Engine is an operating and governance layer for **Autonomous Organisations**—systems that receive a mission and finite resources, coordinate specialized AI agents, allocate scarce credits, enforce deterministic policies, execute approved Web2/Web3 actions, and remain accountable through cryptographic auditability and measured performance.

This is a long-term infrastructure prototype, not a disposable hackathon demo.

## Core invariant

```text
AGENTS PROPOSE → POLICIES AUTHORIZE → EXECUTORS EXECUTE → AUDITORS VERIFY
```

Agents produce typed proposals with no direct execution authority. Deterministic policies authorize or reject them. Executors operate only within approved boundaries. Independent audit verifies the resulting receipt, ledger settlement, state and audit chain.

## Architecture

```text
Human mission + finite budget
          │
          ▼
   Autonomous Organisation
          │
          ▼
    CEO / Orchestrator
       ┌──┼───────────┐
       ▼  ▼           ▼
 Research Strategy  Finance
       └──┬───────────┘
          ▼
   Deterministic Policy
          │
          ├── REJECT → CEO replans
          │
          ▼
   Cryptographic authorization
          │
          ▼
 Controlled Executor
          │
     ┌────┴─────┐
     ▼          ▼
   Ledger    External target
     ▲          │
     └────┬─────┘
          ▼
 Independent Auditor
          │
          ▼
 reputation / lifecycle / audit trail
```

## Security model

### Authorization
Authorization tokens are HMAC-SHA256 signed and bound to organisation ID, proposal content hash, decision ID, policy-version hash, issue/expiry timestamps and a unique nonce. The crypto layer is abstracted so the signing implementation can later move to Ed25519 or a smart-contract verifier.

### Atomic execution
External execution uses:

```text
AUTHORIZE → RESERVE → EXECUTE → VERIFY → COMMIT
                         │
                         └──── failure → ROLLBACK
```

Escrow prevents a failed external request from silently consuming organisation credits. Network execution is constrained by method, payload, redirect and DNS/IP SSRF checks.

### Ledger
SQLite's double-entry ledger is the accounting source of truth. Treasury balances are derived from ledger entries rather than trusting a mutable organisation field. Conservation and overdraft prevention are enforced.

### Independent verification
The auditor does not simply ask the policy engine whether something is valid. It independently checks proposal fingerprint, token validity, authorization/execution binding, receipt integrity, ledger settlement, audit-chain integrity, credit conservation and state consistency.

## Agent economy

Agents have reputation, performance, reliability, resource efficiency, authority ceilings and lifecycle state. Allocation can therefore adapt to demonstrated performance instead of treating every agent as equally trusted.

Lifecycle:

```text
ACTIVE → PROBATION → RESTRICTED → SUSPENDED → RETIRED
```

The system does **not** give agents a self-preservation objective. Resource pressure is an organisational control mechanism, not an agent motivation.

## Phase 5 — Command Centre & Settlement Boundary

Phase 5 turns the engine into an inspectable control plane without moving authority into the UI.

### Command Centre

`src/api/server.py` exposes a thin FastAPI read/control boundary for:
- organisations and current state
- agents and workforce metrics
- tasks, proposals and policy decisions
- ledger balances and entries
- append-only mission events
- audit-chain and verification status
- bounded pause/resume operator controls

`apps/web/` contains a dependency-light command-centre dashboard. It polls the API for live telemetry and presents:
- treasury and conservation status
- active workforce
- organisation control graph
- mission replay / event stream
- policy telemetry
- ledger state
- human pause/resume controls

The UI never receives policy secrets or execution credentials.

### Settlement boundary

`src/settlement/adapter.py` defines the explicit settlement interface and a deterministic simulated adapter for demos/tests. This keeps on-chain settlement behind a capability boundary instead of pretending that a hackathon UI is already safe for real money.

A production Robinhood Chain adapter belongs behind this same interface and must preserve the existing authorization, escrow, receipt and audit invariants.

### Agent identity

`src/identity/ed25519.py` provides an Ed25519 identity primitive for stable agent public identities and signed messages. Private keys are held by the caller/key-management layer and are never implicitly persisted by Kalyx.

## Phase 6 — Product Hardening

Phase 6 hardens the command centre's operational boundary before adding more autonomous behaviour.

### API hardening

- Production mode requires `KALYX_OPERATOR_KEY` for pause/resume controls and uses constant-time key comparison.
- Demo/local mode remains frictionless for judging and development.
- CORS is configurable with `KALYX_CORS_ORIGINS`; production defaults to same-origin/no cross-origin access unless explicitly configured.
- Baseline security response headers are added (`nosniff`, frame protection, referrer policy).
- The audit endpoint reports a broken/corrupt chain as an explicit invalid result instead of hiding the condition behind a startup exception.
- `/api/health` reports the running API version.

See `.env.example` for runtime configuration.

### CI quality gate

GitHub Actions runs the test matrix on Python 3.11 and 3.12, verifies dependency consistency with `pip check`, compiles the Python source tree, and runs the full pytest suite. The workflow is also manually dispatchable for release/demo verification.

The repository treats CI as the authoritative test result because local execution is environment-dependent.

## Economic benchmark

`src/economy/experiment.py` compares STATIC, PERFORMANCE and ADAPTIVE allocation across multiple workload scenarios and seeds. Results are generated empirically rather than hardcoding the desired winner.

## Project status

- [x] Phase 1 — Deterministic foundation
- [x] Phase 2 — Agent architecture & orchestration
- [x] Phase 3 — Persistence & independent verification
- [x] Phase 3.5 — Cryptographic & policy hardening
- [x] Phase 4 — Agent economy & controlled execution
- [x] Phase 4 hardening — escrow atomicity, DNS SSRF validation, ledger authority, empirical benchmark
- [x] Phase 5 — Command Centre, API/read model, operator controls, Ed25519 identity primitive, settlement boundary, CI
- [x] Phase 6 — API security boundary, audit resilience, production configuration, container healthcheck, CI quality gates

## Quickstart

```bash
git clone https://github.com/A-Nuel/kalyx-mao-engine.git
cd kalyx-mao-engine
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python scripts/run_demo.py --fast
```

Start the command centre:

```bash
uvicorn src.api.server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` after running a demo against the same `KALYX_DB` database.

For a production-style container:

```bash
docker build -t kalyx .
docker run --rm -p 8000:8000 \
  -e KALYX_OPERATOR_KEY="replace-with-a-strong-secret" \
  -v kalyx-data:/app/data \
  kalyx
```

Run tests locally:

```bash
python -m pip check
python -m compileall -q src tests
python -m pytest -q
```

GitHub Actions repeats these checks on pushes to `main`, pull requests, and manual workflow dispatches.

## What Kalyx is trying to prove

The thesis is not that "AI can run a company" today.

The narrower engineering claim is:

> **AI agents can operate as an economically constrained organisation when delegation, authority, execution and verification are explicit system primitives rather than prompt instructions.**

The long-term direction is an infrastructure layer for autonomous workforces, autonomous businesses and machine-native organisations.

## License

MIT License. Designed and architected for autonomous organisation infrastructure.
