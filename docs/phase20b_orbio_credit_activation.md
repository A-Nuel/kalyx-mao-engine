# Phase 20B — Governed Orbio CREDIT Activation

## Path

```text
Existing CREDIT (already owned)
  → AGENT proposes OrbioCreditActivationIntent
  → POLICY (mainnet 4663, CREDIT allowlist, max 1 CREDIT)
  → HUMAN APPROVAL (bound to intent hash)
  → READ-ONLY PREFLIGHT (chainId, code, balanceOf, preview, gas)
  → AUTHORIZE → ESCROW → BlockchainSettlementProvider
  → CREDIT.activate(amount)
  → RECEIPT + Activated event
  → OrbioCreditActivationVerifier
```

**Not** `buyAndActivate`. **Not** USDG purchase.

## Hard bounds (first production path)

| Constraint | Value |
|------------|-------|
| Chain ID | **4663** only |
| CREDIT | `0xe33322da1380e61e5ae5dfb21e7f62924c73004c` |
| Max amount | **1_000_000** (1.000000 CREDIT) |
| Testnet 46630 | **Forbidden** |
| Simulation fallback | **Forbidden** |
| Human approval | **Required** |

## Calldata (1 CREDIT)

```text
function: activate(uint256)
selector: 0xb260c42a  (keccak256("activate(uint256)")[:4])
calldata: 0xb260c42a00000000000000000000000000000000000000000000000000000000000f4240
```

## Components

- `src/domain/orbio_activation.py` — intent + encoding
- `src/governance/orbio_activation_rules.py` — policy + human gate
- `src/execution/orbio_activation.py` — bridge to Phase 10/12
- `src/settlement/orbio_activation_preflight.py` — read-only RPC checks
- `src/settlement/orbio_activation_verifier.py` — Activated event verification

## Explicit non-goals (this phase)

- No mainnet broadcast
- No activation of user funds
- No API-balance ledger (Phase 21)
- No buyAndActivate

## Tests

```bash
pytest tests/unit/test_orbio_credit_activation.py -q
```
