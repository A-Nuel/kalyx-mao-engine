# Phase 20B — Governed Orbio CREDIT Activation

## 1. Executive Summary & Status

Phase 20B establishes the complete, production-grade architecture for activating already-owned CREDIT on the Orbio protocol via Robinhood Chain Mainnet (`chainId: 4663`).

* **Execution Readiness:** Fully verified and execution-ready through the wallet-signing boundary.
* **Live Mainnet Preflight:** **PASSED** on Robinhood Chain Mainnet with live on-chain state verified via JSON-RPC.
* **Broadcast Status:** **NO TRANSACTION WAS BROADCAST.**
* **Blocker:** Actual on-chain activation is blocked because the operator's Robinhood Wallet does not expose private keys and its native UI does not support inputting arbitrary contract interaction calldata.
* **Governance Invariant:** No workarounds or bypasses to wallet signing were permitted. The system stopped cleanly at the signing boundary with full governance integrity.
* **Target:** Exactly **1 CREDIT** (`1_000_000` atoms).

---

## 2. Governed Execution Architecture

```text
Existing CREDIT (already owned by operator)
  → AGENT proposes OrbioCreditActivationIntent
  → POLICY (Robinhood Chain 4663, CREDIT contract allowlist, exact 1 CREDIT hard bound)
  → HUMAN APPROVAL (cryptographically bound to exact intent hash)
  → LIVE READ-ONLY PREFLIGHT (chainId, contract bytecode, balanceOf, previewActivation, feeBps, nonce, native gas balance, gas estimation)
  → EXTERNAL SIGNER / PAYLOAD EXPORT (canonical EIP-1559 Type-2 dictionary)
  → [WALLET-SIGNING BOUNDARY] — STOPPED (Robinhood Wallet cannot sign arbitrary calldata)
  → (Future broadcast / reconciliation path preserved: --signed-tx-hex or --reconcile-tx)
  → RECEIPT + Activated event parsing
  → OrbioCreditActivationVerifier (strict 8-point check)
```

**Not** `buyAndActivate`. **Not** USDG purchase. **No** simulation fallback.

---

## 3. Strict Execution Parameters & Hard Bounds

| Parameter | Required Value | Notes |
| :--- | :--- | :--- |
| **Network** | `robinhood-mainnet` | Production chain |
| **Chain ID** | `4663` | Testnet `46630` strictly forbidden |
| **CREDIT Contract** | `0xe33322da1380e61e5ae5dfb21e7f62924c73004c` | Allowlisted only |
| **Operator Address** | `0x4675b9d0323479b1af399c87331d1d2436e6be99` | Authorized operator |
| **Activation Amount** | `1_000_000` atomic units | Exactly 1.000000 CREDIT |
| **Function** | `activate(uint256)` | Selector `0xb260c42a` |
| **Value** | `0` | Native token transfer forbidden |
| **Calldata** | `0xb260c42a00000000000000000000000000000000000000000000000000000000000f4240` | Exact 36-byte payload |
| **Gas Limit** | `150,000` | Capped safety limit |
| **Human Approval** | Required | Bound to intent hash |

---

## 4. Live Mainnet Preflight Evidence

Executed against Robinhood Chain Mainnet via JSON-RPC:

| Check | Live On-Chain Value | Status |
| :--- | :--- | :--- |
| **Chain ID** | `4663` | **PASS** |
| **Contract Bytecode** | Present at `0xe33322da1380e61e5ae5dfb21e7f62924c73004c` (262 hex chars) | **PASS** |
| **Operator CREDIT Balance** | `100,064,929` atoms ($\approx 100.064929$ CREDIT) | **PASS** ($\ge 1,000,000$) |
| **previewActivation(1,000,000)** | Credited: `950,000` atoms \| Fee: `50,000` atoms | **PASS** (Conservation $950k + 50k = 1M$) |
| **activationFeeBps()** | `500` bps ($5.00\%$) | **PASS** |
| **Operator Native Gas Token** | `2,939,688,863,821,542` wei ($\approx 0.00294$ ETH) | **PASS** ($> 0$ for gas) |
| **Gas Estimate** | `53,161` gas units | **PASS** ($\le 150,000$ limit) |
| **Pending Nonce** | `3` | **PASS** |
| **Policy Decision** | `ALLOW` | **PASS** |
| **API Balance Invariant** | `api_balance_confirmed = false` | **CONFIRMED** |

---

## 5. Canonical Governed Unsigned Payload (Exported)

The live driver script (`scripts/execute_orbio_activation_mainnet.py --export-unsigned-tx`) constructed and exported the canonical EIP-1559 payload:

```json
{
  "type": 2,
  "chainId": 4663,
  "nonce": 3,
  "maxFeePerGas": 25000000000,
  "maxPriorityFeePerGas": 1500000000,
  "gas": 150000,
  "to": "0xE33322DA1380e61E5Ae5DfB21e7f62924c73004C",
  "value": 0,
  "data": "0xb260c42a00000000000000000000000000000000000000000000000000000000000f4240"
}
```

---

## 6. Architecture & Components

* **`src/domain/orbio_activation.py`**: Domain entity `OrbioCreditActivationIntent`, ABI calldata encoding `activate(uint256)`, intent hash computation, and hard bounds.
* **`src/governance/orbio_activation_rules.py`**: Policy engine requiring explicit human approval bound to the intent hash, enforcing allowlists, chain ID 4663, amount bounds, and rejecting testnet/simulation.
* **`src/settlement/blockchain/signer.py`**: 
  - `IBlockchainSigner`: Signer interface.
  - `LocalKeySigner`: Signer using local private keys.
  - `ExternalTransactionSigner`: Secure external signer abstraction that constructs canonical EIP-1559 dictionaries, cryptographically recovers the signer address via `Account.recover_transaction`, and rejects any address mismatch.
* **`src/settlement/orbio_activation_preflight.py`**: Zero-broadcast, read-only preflight verifying chain ID, bytecode, balances, preview conservation, fee bps, nonce, and gas.
* **`src/settlement/orbio_activation_verifier.py`**: Verifies transaction receipts, success status, log parsing, CREDIT contract address, expected sender, and `Activated(address,uint256,uint256)` event emission.
* **`scripts/execute_orbio_activation_mainnet.py`**: Unified driver supporting `--signer-mode local|external`, `--export-unsigned-tx`, `--signed-tx-hex`, and `--reconcile-tx`.

---

## 7. Verification & Tests

The entire Phase 20B test suite validates all audit points, signer abstractions, preflight rules, verifiers, and CLI workflows:

```bash
pytest tests/unit/test_phase20b_audit.py -v
pytest tests/unit/test_blockchain_signer.py -v
pytest tests/unit/test_orbio_credit_activation.py -v
pytest tests/unit/test_orbio_activation_mainnet_driver.py -v
```

All 81 tests pass cleanly with zero failures.
