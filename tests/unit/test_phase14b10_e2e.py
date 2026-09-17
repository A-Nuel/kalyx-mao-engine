"""Phase 14B.10 — Final Definition-of-Done: E2E, recovery, and architectural invariants.

Exercises real Phase 14B components together (no architecture mock-away):
  OBSERVE → PROPOSE → POLICY → (HUMAN) → AUTHORIZE → ESCROW → EXECUTE
  → VERIFY → (RECONCILE) → UPDATE → OBJECTIVE COMPLETE

Run focused suite:
  pytest tests/unit/test_phase14b10_e2e.py -q
  pytest tests/unit/test_orbio_purchase_*.py tests/unit/test_phase14b10_e2e.py -q
"""
from __future__ import annotations

from src.agents.orbio_purchase_loop import (
    AgentLoopState,
    LoopStatus,
    OrbioPurchaseAgentLoop,
)
from src.domain.entities import Organisation
from src.domain.enums import ProviderOutcome
from src.economy.ledger import DoubleEntryLedger
from src.execution.orbio_purchase import OrbioPurchaseBridge
from src.governance.orbio_purchase_rules import OrbioPurchasePolicy, PurchaseDecisionResult
from src.settlement.orbio_purchase_reconciliation import OrbioPurchaseReconciliation
from src.settlement.orbio_purchase_verifier import (
    OrbioPurchaseEvidence,
    OrbioPurchaseVerifier,
    PurchaseVerificationResult,
    extract_evidence_from_execution,
)
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider

BENEFICIARY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def _org(tenant: str = "tenant-a", org_id: str = "org-a") -> Organisation:
    return Organisation(id=org_id, mission="14b10", tenant_id=tenant, treasury_balance=100)


def _state(**kw) -> AgentLoopState:
    base = dict(
        agent_id="agent-14b10",
        tenant_id="tenant-a",
        organisation_id="org-a",
        mission_id="mission-14b10",
        objective="Acquire Orbio CREDIT under governed loop",
        target_credit=2_500_000,
    )
    base.update(kw)
    return AgentLoopState(**base)


def _loop(
    *,
    target_credit: int = 2_500_000,
    autonomous: int = 10_000_000,
    absolute: int = 50_000_000,
    usdg_balance: int = 50_000_000,
    max_iterations: int = 5,
    max_cumulative_usdg: int = 50_000_000,
    max_single_usdg: int = 20_000_000,
):
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=autonomous,
        absolute_usdg_ceiling=absolute,
    )
    bridge = OrbioPurchaseBridge(purchase_policy=policy)
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", usdg_balance)
    ledger = DoubleEntryLedger(initial_treasury=100)
    reconciler = OrbioPurchaseReconciliation(
        provider=provider,
        ledger=ledger,
        verifier=OrbioPurchaseVerifier(),
    )
    loop = OrbioPurchaseAgentLoop(
        state=_state(target_credit=target_credit),
        org=_org(),
        policy=policy,
        bridge=bridge,
        provider=provider,
        verifier=OrbioPurchaseVerifier(),
        reconciler=reconciler,
        ledger=ledger,
        max_iterations=max_iterations,
        max_cumulative_usdg=max_cumulative_usdg,
        max_single_usdg=max_single_usdg,
        default_beneficiary=BENEFICIARY,
    )
    return loop, provider, policy, bridge, ledger


def test_e2e_governed_orbio_purchase_loop_happy_path():
    loop, provider, policy, bridge, ledger = _loop(target_credit=2_500_000)

    obs0 = loop.observe()
    assert obs0["acquired_credit"] == 0
    assert obs0["credit_still_needed"] == 2_500_000
    assert obs0["objective_met"] is False

    result = loop.step()

    assert result.proposal is not None
    intent = result.proposal.intent
    intent_hash = intent.compute_purchase_intent_hash()

    assert result.preparation is not None
    assert result.preparation.policy_decision.result == PurchaseDecisionResult.ALLOW
    assert result.preparation.intent_hash == intent_hash

    op = result.operation
    assert op is not None
    assert op.id == intent.operation_id
    assert op.idempotency_key == intent.idempotency_key
    assert op.tenant_id == "tenant-a"
    assert op.organisation_id == "org-a"
    assert op.parameters.get("purchase_intent_hash") == intent_hash
    assert op.parameters.get("usdg_in") == intent.usdg_in

    assert result.execution is not None
    assert result.execution.outcome == ProviderOutcome.SUCCESS.value
    assert result.execution.evidence_hash
    activation_id = (result.execution.raw_response or {}).get("activation_id")
    assert activation_id

    assert result.verification_result == PurchaseVerificationResult.VERIFIED.value

    evidence = extract_evidence_from_execution(
        result.execution, operation=op, intent=intent
    )
    assert evidence is not None
    assert evidence.purchase_intent_hash == intent_hash
    assert evidence.activation_id == activation_id
    assert evidence.usdg_spent == intent.usdg_in
    assert evidence.credit_out >= intent.min_credit_out
    assert evidence.evidence_hash == result.execution.evidence_hash

    assert loop.state.acquired_credit == evidence.credit_out
    assert loop.state.activation_confirmed is True
    assert loop.state.cumulative_usdg_spent == evidence.usdg_spent
    assert intent_hash in loop.state.completed_intent_hashes
    assert result.status == LoopStatus.OBJECTIVE_COMPLETE
    assert loop.state.objective_met()

    usdg_after = provider.get_usdg_balance("org-a")
    second = loop.step()
    assert second.status == LoopStatus.OBJECTIVE_COMPLETE
    assert provider.get_usdg_balance("org-a") == usdg_after
    assert loop.state.loop_iteration == 1


def test_e2e_unknown_then_reconcile_then_complete():
    loop, provider, policy, bridge, ledger = _loop(target_credit=2_500_000)

    original_execute = provider.execute

    def timeout_bg(op):
        provider.set_timeout_rule(op.idempotency_key, provider_executes_in_background=True)
        return original_execute(op)

    provider.execute = timeout_bg  # type: ignore

    r1 = loop.step()
    assert r1.status == LoopStatus.WAITING_RECONCILE
    assert r1.execution is not None
    assert r1.execution.outcome == ProviderOutcome.TIMEOUT.value
    assert not loop.state.objective_met()
    assert loop.state.acquired_credit == 0

    blocked = loop.step()
    assert blocked.status == LoopStatus.OBJECTIVE_COMPLETE
    assert loop.state.activation_confirmed is True
    assert loop.state.acquired_credit >= 2_500_000

    again = loop.step()
    assert again.status == LoopStatus.OBJECTIVE_COMPLETE


def test_e2e_pending_unknown_without_background_stays_blocked():
    loop, provider, *_ = _loop(target_credit=2_500_000)
    original = provider.execute

    def timeout_only(op):
        provider.set_timeout_rule(op.idempotency_key)
        return original(op)

    provider.execute = timeout_only  # type: ignore
    r1 = loop.step()
    assert r1.status == LoopStatus.WAITING_RECONCILE
    r2 = loop.step()
    assert r2.status in {LoopStatus.WAITING_RECONCILE, LoopStatus.STOPPED_UNKNOWN}
    assert loop.state.acquired_credit == 0
    assert not loop.state.objective_met()


def test_invariant_policy_deny_produces_no_execution():
    policy = OrbioPurchasePolicy(allowed_chain_ids={46630})
    bridge = OrbioPurchaseBridge(purchase_policy=policy)
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 10_000_000)
    loop = OrbioPurchaseAgentLoop(
        state=_state(),
        org=_org(),
        policy=policy,
        bridge=bridge,
        provider=provider,
        verifier=OrbioPurchaseVerifier(),
        default_beneficiary=BENEFICIARY,
        chain_id=1,
        network="ethereum",
    )
    bal_before = provider.get_usdg_balance("org-a")
    result = loop.step()
    assert result.status == LoopStatus.STOPPED_POLICY_DENY
    assert result.execution is None
    assert provider.get_usdg_balance("org-a") == bal_before


def test_invariant_intent_mutation_fails_verification():
    loop, provider, *_ = _loop()
    result = loop.step()
    assert result.verification_result == PurchaseVerificationResult.VERIFIED.value
    intent = result.proposal.intent
    evidence = extract_evidence_from_execution(
        result.execution, operation=result.operation, intent=intent
    )
    assert evidence is not None
    tampered = OrbioPurchaseEvidence(
        **{**evidence.__dict__, "beneficiary": "0x0000000000000000000000000000000000000001"}
    )
    report = OrbioPurchaseVerifier().verify(
        intent, tampered, provider_outcome=ProviderOutcome.SUCCESS.value
    )
    assert report.result == PurchaseVerificationResult.REJECTED


def test_invariant_agent_cannot_self_declare_success_without_evidence():
    loop, provider, *_ = _loop()
    original = provider.execute

    def always_fail(op):
        provider.set_failure_rule(op.idempotency_key, "forced")
        return original(op)

    provider.execute = always_fail  # type: ignore
    result = loop.step()
    assert result.status == LoopStatus.STOPPED_FAILURE
    assert loop.state.acquired_credit == 0
    assert loop.state.activation_confirmed is False
    assert not loop.state.objective_met()


def test_invariant_cross_tenant_rejected():
    loop, *_ = _loop()
    loop.state.tenant_id = "tenant-other"
    try:
        loop.propose_purchase(usdg_in=1_000_000, min_credit_out=900_000)
        raised = False
    except PermissionError:
        raised = True
    assert raised
    assert loop.state.status == LoopStatus.STOPPED_CROSS_TENANT


def test_invariant_duplicate_completed_purchase_blocked():
    loop, provider, *_ = _loop(target_credit=2_500_000)
    loop.step()
    assert loop.state.objective_met()
    bal = provider.get_usdg_balance("org-a")
    loop.step()
    assert provider.get_usdg_balance("org-a") == bal


def test_invariant_unknown_blocks_continuation():
    loop, provider, *_ = _loop()
    original = provider.execute

    def timeout_only(op):
        provider.set_timeout_rule(op.idempotency_key)
        return original(op)

    provider.execute = timeout_only  # type: ignore
    r1 = loop.step()
    assert r1.status == LoopStatus.WAITING_RECONCILE
    hashes_before = list(loop.state.completed_intent_hashes)
    r2 = loop.step()
    assert r2.status in {LoopStatus.WAITING_RECONCILE, LoopStatus.STOPPED_UNKNOWN}
    assert loop.state.completed_intent_hashes == hashes_before


def test_invariant_human_gate_cannot_be_bypassed():
    loop, provider, policy, bridge, _ = _loop(
        target_credit=10_000_000,
        autonomous=1_000_000,
        absolute=50_000_000,
        max_single_usdg=20_000_000,
    )
    r1 = loop.step()
    assert r1.status == LoopStatus.WAITING_HUMAN
    assert loop.attempt_bypass_human_with_smaller_amount(500_000) is False
    assert loop.attempt_split_purchase_evasion(4) is False
    bal = provider.get_usdg_balance("org-a")
    r2 = loop.step()
    assert r2.status == LoopStatus.WAITING_HUMAN
    assert provider.get_usdg_balance("org-a") == bal
