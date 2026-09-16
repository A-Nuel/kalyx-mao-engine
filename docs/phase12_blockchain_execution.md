# PHASE 12: REAL BLOCKCHAIN SETTLEMENT & ON-CHAIN EXECUTION BOUNDARY

## 1. Architectural Overview & Fundamental Thesis

> *"What becomes possible when blockchain becomes an execution and settlement layer for autonomous software?"*

In the Kalyx MAO Engine, blockchain infrastructure is treated strictly as an **execution and settlement layer**, never an autonomous governance authority. Autonomous software agents and browser clients possess **zero private keys**, zero direct transaction authority, and zero execution privileges.

The execution boundary preserves the non-negotiable Kalyx invariant:

```
AGENTS PROPOSE
     ↓
POLICIES AUTHORIZE
     ↓
EXECUTORS EXECUTE
     ↓
AUDITORS VERIFY
```

Every on-chain transaction is derived deterministically from an authorized capability token, backed by escrowed credits in a double-entry ledger, executed across an isolated signing boundary, and independently verified against raw blockchain receipts by an auditor.

---

## 2. Target Testnet Specification

- **Target Network**: Ethereum Sepolia Testnet
- **EVM Chain ID**: `11155111`
- **Transaction Type**: EIP-1559 Dynamic Fee Transactions (Type 2)
- **Native Asset**: Sepolia ETH (18 decimals, denominated in Wei for on-chain transit, tracked in integer Credits on the internal ledger)
- **RPC Client Support**:
  - `HttpEvmRpcClient`: Production JSON-RPC transport over HTTP via `httpx`.
  - `SimulatedEvmRpcClient`: In-memory EVM simulation with programmable network latency, drop timeouts, on-chain reverts, and pending mempool states.

---

## 3. Cryptographic Signing Boundary (`LocalKeySigner`)

The signing boundary is strictly isolated within `src/settlement/blockchain/signer.py`:

- **Key Isolation**: Private keys are injected solely via environment variable (`KALYX_BLOCKCHAIN_PRIVATE_KEY`) and loaded into `LocalKeySigner`.
- **Zero Exposure**:
  - `__repr__` and `__str__` mask the key: `<LocalKeySigner address=0x... key=***REDACTED***>`.
  - Global logging filter (`SensitiveDataFilter`) in `src/utils/logging.py` redacts 32-byte hex keys and RPC URLs containing credentials.
  - The browser/UI and LLM prompts never touch private keys or raw signatures.
- **EIP-1559 Construction**: Computes transaction payloads with explicit `chainId`, `maxFeePerGas`, `maxPriorityFeePerGas`, `gasLimit`, `to`, `value`, `data`, and deterministic `nonce`. Signs via `eth_account.Account.sign_transaction`.

---

## 4. Nonce Management Strategy (`NonceManager`)

Concurrent agent proposals and unpredictable network confirmation delays make unmanaged on-chain nonces vulnerable to transaction collisions, stuck queues, and duplicate execution.

Kalyx implements a thread-safe `NonceManager` in `src/settlement/blockchain/nonce_manager.py`:
- **Keyed Tracking**: Monotonically allocates sequential nonces keyed by `(sender_address.lower(), chain_id)`.
- **Idempotent Reuse**: If a consequential operation or idempotency key was previously allocated a nonce (e.g. during an unconfirmed retry or crash recovery), the allocated nonce is reused rather than advancing the counter.
- **On-Chain Initialization**: When an address is first seen or when local tracking needs reconciliation, queries `eth_getTransactionCount(sender, 'pending')` from the authoritative RPC node.

---

## 5. Strongly-Typed Transaction Intent (`BlockchainTransactionIntent`)

Before any byte is signed, the consequential operation's parameters are converted into a `BlockchainTransactionIntent` (`src/domain/blockchain.py`):

```python
class BlockchainTransactionIntent(BaseModel):
    tenant_id: str
    organisation_id: str
    mission_id: Optional[str]
    operation_id: str
    chain_id: int
    network: str
    recipient: str
    amount_wei: int
    amount_credits: int
    asset: str
    token_contract: Optional[str]
    max_fee_per_gas: int
    max_priority_fee_per_gas: int
    gas_limit: int
    data_payload: str
    idempotency_key: str
    policy_decision_id: str
    authorization_token_hash: str
```

- **Intent Hash**: Deterministically computed using canonical JSON serialization and SHA-256 (`compute_intent_hash()`).
- **Cryptographic Binding**: Binds the exact policy decision, authorization token hash, and idempotency key to prevent tampering between authorization and signing.

---

## 6. Deterministic Policy Rules (`RULE-BC-01` to `RULE-BC-05`)

Blockchain transactions are evaluated against five mandatory deterministic policy rules registered in `PolicyEngine`:

1. **`RULE-BC-01` (`AllowedChainRule`)**:
   Enforces that `chain_id` matches the organisation's configured `allowed_chains`. Specifically rejects unauthorized networks and mainnet (`chain_id=1`).
2. **`RULE-BC-02` (`RecipientAllowlistRule`)**:
   Validates checksummed EVM address format and ensures the recipient is present in `org.blockchain_recipient_allowlist`. Rejects zero address (`0x0000...0000`) unless explicitly allowlisted.
3. **`RULE-BC-03` (`TransactionAmountCeilingRule`)**:
   Enforces that requested credits do not exceed `agent.authority_ceiling`, requested wei does not exceed `org.max_transaction_wei`, and treasury has sufficient balance.
4. **`RULE-BC-04` (`GasExposureRule`)**:
   Caps `max_fee_per_gas` (default 100 Gwei) and `gas_limit` (default 500,000) to protect the organisation against network fee surges.
5. **`RULE-BC-05` (`IntentMatchRule`)**:
   Verifies that proposal parameters and target match the recipient, chain, and payload exactly, preventing parameter spoofing.

---

## 7. Lifecycle, Escrow, and Reconciliation State Machine

The durable consequential lifecycle introduced in Phase 10 is strictly preserved:

```
[CREATED]
    ↓ (verify token & agent capabilities)
[AUTHORIZED]
    ↓ (lock credits in ledger: TREASURY -> ESCROW)
[ESCROWED]
    ↓ (sign EIP-1559 & submit to RPC)
[SUBMITTED]
    ├── (On-Chain Confirmed) ──→ [SUCCEEDED]  (ESCROW -> EXTERNAL_SINK)
    ├── (On-Chain Reverted)  ──→ [FAILED]     (ESCROW -> TREASURY)
    └── (Timeout / Drop)     ──→ [UNKNOWN]    (ESCROW PRESERVED)
                                     ↓ (Crash & Restart / Background Check)
                                 [RECONCILED] (Authoritative On-Chain Settlement)
```

### Safety Guarantees:
- **Zero Silent Loss**: If an RPC transport drops, times out, or fails during broadcast, the operation transitions to `UNKNOWN`. Escrow is **NEVER** refunded blindly while the transaction might be confirmed in a mempool.
- **On-Chain Revert Refund**: If the EVM receipt indicates status `0x0` (reverted), escrowed credits are automatically and atomically refunded from `ESCROW` to `TREASURY`.
- **Post-Recovery Reconciliation**: The `ReconciliationService` queries `BlockchainSettlementProvider.status()`. If an on-chain receipt confirms (`status: 1`), escrow is transferred to `EXTERNAL_SINK` and the state becomes `RECONCILED`.

---

## 8. Independent Auditor Verification

The `Auditor` (`src/audit/auditor.py`) verifies all on-chain settlements using an 9-point verification check:
1. `OPERATION_FINGERPRINT`: Tamper detection on operation record.
2. `PROPOSAL_FINGERPRINT`: Proposal integrity.
3. `TOKEN_VALIDITY`: Cryptographic HMAC token signature & policy version check.
4. `AUTH_OPERATION_BINDING`: Proposal/Decision/Org/Amount matching.
5. `PROVIDER_EVIDENCE_INTEGRITY`: Valid transaction hash reference (`0x[0-9a-fA-F]{64}`).
6. `LEDGER_SETTLEMENT`: Exact commit or reconciliation ledger entries.
7. `CREDIT_CONSERVATION`: Double-entry conservation invariant (`TREASURY + ESCROW + EXTERNAL_SINK == TOTAL`).
8. `STATE_CONSISTENCY`: Org not paused.
9. `BLOCKCHAIN_EVIDENCE_INTEGRITY`: Queries the authoritative RPC receipt (or validates normalized evidence) to ensure `SUCCEEDED` operations actually confirmed on-chain and `FAILED` operations actually reverted on-chain.

---

## 9. Configuration & Fail-Closed Startup

Configuration is governed by `src/api/config.py`:
- `KALYX_BLOCKCHAIN_ENABLED`: Defaults to `false`. When `false`, the server defaults to simulation or mock providers.
- When `KALYX_BLOCKCHAIN_ENABLED=true`:
  - `KALYX_BLOCKCHAIN_RPC_URL` and `KALYX_BLOCKCHAIN_PRIVATE_KEY` are mandatory.
  - If either is missing or invalid, the system **fails closed** at startup with an explicit `ConfigurationError`.
  - There is zero silent fallback to mock or simulation when blockchain is enabled.

---

## 10. Verification Milestone & Test Coverage

Run the standalone controlled milestone demonstration:
```bash
# Simulated dry-run verification
python scripts/execute_testnet_settlement.py --simulate

# Live Ethereum Sepolia verification (requires funded testnet key and RPC)
python scripts/execute_testnet_settlement.py --recipient 0x70997970C51812dc3A010C7d01b50e0d17dc79C8 --amount-credits 1
```

### Test Suite:
- **Total Tests**: 253 passed, 5 skipped
- **Phase 12 Tests**:
  - `tests/unit/test_blockchain_intent.py` (5 tests)
  - `tests/unit/test_blockchain_policy_rules.py` (5 tests)
  - `tests/unit/test_blockchain_signer.py` (4 tests)
  - `tests/integration/test_blockchain_settlement_lifecycle.py` (3 tests)
  - `tests/integration/test_blockchain_security_adversarial.py` (4 tests)

---

## 11. Production Disclaimer

> [!WARNING]
> **CRITICAL OPERATIONAL NOTICE**:
> Kalyx is **NOT** production-ready for real-money mainnet settlement merely because a testnet transaction succeeds.
> Production mainnet deployment requires Hardware Security Modules (HSM / AWS KMS / GCP Cloud KMS), multi-party computation (MPC) co-signers, formal smart contract audits, dynamic gas re-pricing (EIP-1559 speedup/cancel), and external multi-signature human approval gates.
