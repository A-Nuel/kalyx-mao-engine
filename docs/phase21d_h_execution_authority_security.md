# Phase 21D–21H — Execution Authority, Wallet Boundary & Security Gate

## Status
Implementation complete; CI is the final gate before merge.

## 21D — ExecutionAuthority
Introduces an organisation-bound, provider-neutral authority boundary:
`Tenant → Organisation → ExecutionAuthority → provider adapter → execution boundary`.

The existing `IBlockchainSigner`, `LocalKeySigner`, and `ExternalTransactionSigner` remain the cryptographic boundary. No private key is moved into the new abstraction.

## 21E — Wallet boundary
`WalletIdentity` contains only public wallet metadata:
- tenant / organisation scope
- chain ID
- public address
- provider
- optional label

It intentionally has no private-key, seed-phrase, or signing-secret field.

WalletConnect/EIP-1193 mechanics remain provider adapters; they are not embedded into governance or agents.

## 21F — Durable human approval
`ExecutionApprovalManager` persists approvals and binds them to:
- tenant
- organisation
- authenticated principal
- exact execution authority
- exact intent hash
- exact policy decision ID and decision hash
- issuance/expiry window
- HMAC signature

Consumption is atomic and replay-protected.

This complements, rather than replaces, the existing administrative governance system.

## 21G — Multi-organisation proof
Tests prove two organisations can independently issue and consume their own approvals while an approval cannot cross an organisation or authority boundary.

## 21H — Security gate
Adversarial coverage includes:
- cross-tenant scope substitution
- cross-organisation scope substitution
- authority substitution
- intent mutation
- policy-decision mutation
- expired approval handling
- approval replay
- wallet model secret exclusion

## Environment / Render
This phase introduces **no new mandatory environment variable**.

Existing production deployments should continue using the existing production configuration, especially `KALYX_POLICY_SECRET` for cryptographic governance. Do not add a private key or seed phrase for this work.

If/when a wallet provider is added in a later deployment-specific phase, its credentials should be introduced as a separately scoped secret rather than placed in organisation data or agent configuration.

## Invariant
**AGENTS PROPOSE → POLICIES AUTHORIZE → EXECUTORS EXECUTE → AUDITORS VERIFY → LEDGER SETTLES**

The new authority layer does not give agents signing capability; it makes the execution authority explicit and organisation-bound.
