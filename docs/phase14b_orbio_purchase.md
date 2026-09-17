# Phase 14B — Governed Orbio CREDIT Purchase Loop

## Core invariant

```text
AGENTS PROPOSE → POLICIES AUTHORIZE → EXECUTORS EXECUTE → AUDITORS VERIFY
```

## Lifecycle

```text
OBSERVE
  → PROPOSE (typed OrbioPurchaseIntent)
  → POLICY (bounded allow / deny / human required)
  → HUMAN GATE (approval bound to exact intent hash)
  → AUTHORIZE → ESCROW → SUBMIT
  → EXECUTE (SimulatedOrbioExchangeProvider offline)
  → VERIFY (OrbioPurchaseVerifier vs authorized intent)
  → RECONCILE if UNKNOWN (OrbioPurchaseReconciliation)
  → UPDATE agent state from verified evidence only
  → OBJECTIVE COMPLETE or next bounded proposal
```

## What agents may do

- Observe mission need and balances
- Construct a **typed** `OrbioPurchaseIntent` (no arbitrary calldata)
- Submit proposals into existing policy + Phase 10 consequential machinery
- React to verified outcomes and update **bounded** loop state

## What agents must not do

- Sign transactions or hold private keys
- Call RPC
- Bypass policy or human confirmation
- Mutate an authorized intent and keep the same approval
- Self-declare SUCCESS without independent verification
- Issue a second purchase while UNKNOWN is unresolved
- Cross tenant/organisation boundaries

## Components (reuse, not parallel systems)

| Piece | Role |
|-------|------|
| `OrbioPurchaseIntent` | Deterministic `buyAndActivate` encoding + intent hash |
| `OrbioPurchasePolicy` | Spend ceilings, chain/exchange allowlists, human gate |
| `OrbioPurchaseBridge` | Intent → Phase 10 `ConsequentialOperation` |
| `SimulatedOrbioExchangeProvider` | Offline USDG→CREDIT + activation evidence |
| `OrbioPurchaseVerifier` | Evidence bound to authorized intent |
| `OrbioPurchaseReconciliation` | UNKNOWN resolution with verification gate |
| `OrbioPurchaseAgentLoop` | Closed-loop observe/propose/react under bounds |

## Economic / safety bounds

- Max single purchase, max cumulative USDG, max loop iterations
- Idempotency keys prevent duplicate provider settlement
- Ledger escrow remains locked on TIMEOUT/UNKNOWN until verified reconcile
- Exactly-once terminal settlement patterns from Phase 10

## Scope of this implementation

**Offline / simulated only.**

Phase 14B demonstrates governed acquisition of Orbio CREDIT **without** production RPC, live keys, or real funds.

### Intentionally deferred — Phase 14B.6

Live/testnet Robinhood Chain RPC execution, USDG approval transactions, and real signing remain **deferred**. Do not treat the simulated provider as mainnet truth.

## Run the Definition-of-Done tests

```bash
# Authoritative 14B.10 E2E + invariants
pytest tests/unit/test_phase14b10_e2e.py -q

# Full Orbio purchase unit suite
pytest tests/unit/test_orbio_purchase_*.py tests/unit/test_phase14b10_e2e.py -q

# Non-Postgres suite (matches CI SQLite step)
pytest -q -m "not postgres"
```

GitHub Actions on pull requests is the authoritative CI signal (Python 3.11 / 3.12, SQLite, Postgres, compileall, Docker build on 3.12).
