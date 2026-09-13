# Phase 8 — Security & Reliability Contract

Phase 8 hardens the Kalyx control plane before any real-value settlement is considered.

## Trusted-core invariant

> Agents propose. Policies authorize. Executors execute. Auditors verify.

LLM output is untrusted input. It is never itself an authorization boundary.

## Resource invariants

1. No account may go negative.
2. A transaction ID cannot be committed twice.
3. Concurrent balance checks must serialize before a spend is committed.
4. Escrowed credits are either committed once or returned to treasury.
5. Tenant account names and transaction IDs are isolated.
6. A capability-ineligible, inactive, or over-authority agent cannot execute an action.
7. An unresolved external operation is not automatically retried.
8. Non-idempotent external providers must support provider-side idempotency keys before they can be considered money-safe.
9. Audit evidence must remain independently verifiable.
10. A mismatch between internal and external state must stop automated progression rather than silently overwrite either side.

## Failure matrix

| Failure | Required behaviour |
|---|---|
| Duplicate transaction | Reject; no ledger mutation |
| Insufficient balance | Reject; no ledger mutation |
| Concurrent overspend | At most one valid spend per available balance |
| External HTTP 4xx/5xx | Roll back reservation |
| External timeout | Mark operation unresolved; reconcile before retry |
| Process crash before external call | Recover/release reservation safely |
| Process crash after external call | Do not blindly retry; reconcile provider using operation key |
| Duplicate POST | Provider idempotency key prevents duplicate side effect |
| Stale authorization | Reject |
| Replayed authorization token | Reject |
| Policy version mismatch | Reject |
| Inactive agent | Reject |
| Capability mismatch | Reject |
| Authority ceiling exceeded | Reject |
| Cross-tenant transaction key collision | Namespace and isolate |
| Audit-chain corruption | Surface corruption; never hide it |

## External execution rule

Exactly-once execution cannot be guaranteed against an arbitrary HTTP endpoint by Kalyx alone. For a consequential POST, Kalyx sends `Idempotency-Key=<proposal id>`. The provider/adapter must implement durable idempotency semantics. If a provider cannot, it must remain outside the money-safe settlement boundary.

## Phase 8 release gate

Phase 8 is complete only when:

- CI passes on all supported Python versions.
- Production Docker image builds.
- Concurrency tests prove no overspend.
- Durable idempotency tests prove key/content binding.
- Tenant isolation tests pass.
- Agent capability tests pass.
- Failure-path tests cover rollback and ambiguous external execution.
- No known money-safety invariant is implemented only in an LLM prompt.

## Explicit non-goal

Phase 8 does **not** claim that Kalyx is ready for unrestricted real-money custody. Real settlement requires provider-specific adapters, reconciliation, key management, operational controls, and adversarial testing in later phases.
