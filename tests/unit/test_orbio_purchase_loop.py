"""Phase 14B.9 — Closed-loop Orbio purchase agent tests."""
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
from src.governance.orbio_purchase_rules import OrbioPurchasePolicy
from src.settlement.orbio_purchase_reconciliation import OrbioPurchaseReconciliation
from src.settlement.orbio_purchase_verifier import OrbioPurchaseVerifier
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider

BENEFICIARY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def _org() -> Organisation:
    return Organisation(id="org-a", mission="loop", tenant_id="tenant-a", treasury_balance=100)


def _state(**kw) -> AgentLoopState:
    base = dict(
        agent_id="agent-1",
        tenant_id="tenant-a",
        organisation_id="org-a",
        mission_id="mission-credit",
        objective="Acquire CREDIT for inference mission",
        target_credit=2_500_000,
    )
    base.update(kw)
    return AgentLoopState(**base)


def _loop(
    *,
    target_credit: int = 2_500_000,
    autonomous: int = 5_000_000,
    absolute: int = 25_000_000,
    max_iterations: int = 5,
    max_cumulative_usdg: int = 25_000_000,
    max_single_usdg: int = 10_000_000,
    usdg_balance: int = 50_000_000,
    background_timeout_success: bool = False,
) -> tuple[OrbioPurchaseAgentLoop, SimulatedOrbioExchangeProvider]:
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
    return loop, provider


def test_agent_observes_initial_state():
    loop, _ = _loop()
    obs = loop.observe()
    assert obs["acquired_credit"] == 0
    assert obs["credit_still_needed"] == 2_500_000
    assert obs["objective_met"] is False


def test_agent_creates_typed_purchase_proposal():
    loop, _ = _loop()
    proposal = loop.propose_purchase(usdg_in=3_000_000, min_credit_out=2_500_000)
    assert proposal.intent.usdg_in == 3_000_000
    assert proposal.intent.beneficiary == BENEFICIARY.lower()
    assert not hasattr(proposal.intent, "data_payload") or proposal.intent.encode_calldata().startswith("0x6ebadb6e")
    assert proposal.agent_id == "agent-1"


def test_full_happy_path_verify_updates_state():
    loop, _ = _loop(target_credit=2_500_000)
    result = loop.step()
    assert result.status == LoopStatus.OBJECTIVE_COMPLETE
    assert result.verification_result == "VERIFIED"
    assert loop.state.acquired_credit >= 2_500_000
    assert loop.state.activation_confirmed is True
    assert loop.state.cumulative_usdg_spent > 0


def test_agent_stops_when_objective_complete():
    loop, _ = _loop(target_credit=2_500_000)
    loop.step()
    second = loop.step()
    assert second.status == LoopStatus.OBJECTIVE_COMPLETE
    assert second.message


def test_subsequent_proposal_when_target_not_yet_met():
    # Small autonomous purchases; target requires two steps of capacity
    loop, _ = _loop(
        target_credit=5_000_000,
        max_single_usdg=3_000_000,
        autonomous=5_000_000,
    )
    r1 = loop.step()
    assert r1.status == LoopStatus.READY
    assert loop.state.acquired_credit > 0
    r2 = loop.step()
    assert r2.status in {LoopStatus.READY, LoopStatus.OBJECTIVE_COMPLETE}
    assert loop.state.loop_iteration >= 2 or loop.state.objective_met()


def test_cumulative_spend_limit_stops_loop():
    loop, _ = _loop(
        target_credit=20_000_000,
        max_cumulative_usdg=3_000_000,
        max_single_usdg=3_000_000,
        autonomous=5_000_000,
    )
    steps = loop.run_until_terminal()
    assert any(s.status == LoopStatus.STOPPED_SPEND_LIMIT or s.status == LoopStatus.OBJECTIVE_COMPLETE for s in steps)
    # If spend limit hit before objective, status is spend limit
    if not loop.state.objective_met():
        assert loop.state.status == LoopStatus.STOPPED_SPEND_LIMIT
    assert loop.state.cumulative_usdg_spent <= 3_000_000 + 3_000_000  # at most one overshoot frame


def test_iteration_limit_stops_loop():
    loop, _ = _loop(
        target_credit=50_000_000,
        max_iterations=2,
        max_single_usdg=1_000_000,
        autonomous=5_000_000,
        max_cumulative_usdg=100_000_000,
    )
    steps = loop.run_until_terminal()
    assert loop.state.loop_iteration <= 2
    assert any(s.status == LoopStatus.STOPPED_ITERATION_LIMIT for s in steps) or loop.state.status == LoopStatus.STOPPED_ITERATION_LIMIT


def test_human_required_pauses_execution():
    loop, _ = _loop(
        target_credit=10_000_000,
        autonomous=1_000_000,  # force human for needed amount
        absolute=25_000_000,
        max_single_usdg=10_000_000,
    )
    result = loop.step()
    assert result.status == LoopStatus.WAITING_HUMAN
    assert loop.state.status == LoopStatus.WAITING_HUMAN


def test_agent_cannot_bypass_human_gate_with_smaller_amount():
    loop, _ = _loop(
        target_credit=10_000_000,
        autonomous=1_000_000,
        absolute=25_000_000,
        max_single_usdg=10_000_000,
    )
    loop.step()
    assert loop.state.status == LoopStatus.WAITING_HUMAN
    assert loop.attempt_bypass_human_with_smaller_amount(500_000) is False
    # Still waiting — no auto-execute
    again = loop.step()
    assert again.status == LoopStatus.WAITING_HUMAN


def test_agent_cannot_split_to_evade_while_waiting_human():
    loop, _ = _loop(target_credit=10_000_000, autonomous=1_000_000, absolute=25_000_000)
    loop.step()
    assert loop.attempt_split_purchase_evasion(5) is False


def test_human_approval_resumes_and_completes():
    policy = OrbioPurchasePolicy(autonomous_usdg_ceiling=1_000_000, absolute_usdg_ceiling=25_000_000)
    bridge = OrbioPurchaseBridge(purchase_policy=policy)
    provider = SimulatedOrbioExchangeProvider()
    provider.set_usdg_balance("org-a", 50_000_000)
    loop = OrbioPurchaseAgentLoop(
        state=_state(target_credit=2_500_000),
        org=_org(),
        policy=policy,
        bridge=bridge,
        provider=provider,
        verifier=OrbioPurchaseVerifier(),
        default_beneficiary=BENEFICIARY,
        max_single_usdg=10_000_000,
    )
    r1 = loop.step()
    assert r1.status == LoopStatus.WAITING_HUMAN
    assert loop._pending_intent is not None
    approval = policy.issue_human_approval(loop._pending_intent, operator_id="ops")
    loop.supply_human_approval(approval)
    r2 = loop.step()
    assert r2.status == LoopStatus.OBJECTIVE_COMPLETE
    assert loop.state.activation_confirmed is True


def test_agent_cannot_mutate_authorized_intent_hash():
    loop, _ = _loop()
    proposal = loop.propose_purchase(usdg_in=3_000_000, min_credit_out=2_500_000)
    assert loop.attempt_mutate_authorized_intent(proposal.intent, usdg_in=9_000_000) is False


def test_unknown_blocks_duplicate_purchase():
    loop, provider = _loop(target_credit=2_500_000)
    # Force timeout without background settle
    # Intercept by setting timeout on any key the loop will use — set after observe path
    # Use a provider that always times out first execute
    original_execute = provider.execute

    def timeout_once(op):
        provider.set_timeout_rule(op.idempotency_key)
        return original_execute(op)

    provider.execute = timeout_once  # type: ignore
    result = loop.step()
    assert result.status == LoopStatus.WAITING_RECONCILE
    # Next step must not create another purchase while UNKNOWN
    result2 = loop.step()
    assert result2.status in {
        LoopStatus.WAITING_RECONCILE,
        LoopStatus.STOPPED_UNKNOWN,
        LoopStatus.STOPPED_FAILURE,
        LoopStatus.OBJECTIVE_COMPLETE,  # if reconcile somehow verified
        LoopStatus.READY,
    }
    # Must not have completed two purchase intent hashes from a double-spend
    assert len(loop.state.completed_intent_hashes) <= 1


def test_successful_reconciliation_resumes_loop():
    loop, provider = _loop(target_credit=2_500_000)
    original_execute = provider.execute

    def timeout_bg(op):
        provider.set_timeout_rule(op.idempotency_key, provider_executes_in_background=True)
        return original_execute(op)

    provider.execute = timeout_bg  # type: ignore
    r1 = loop.step()
    assert r1.status == LoopStatus.WAITING_RECONCILE
    r2 = loop.step()
    assert r2.status == LoopStatus.OBJECTIVE_COMPLETE
    assert loop.state.activation_confirmed is True


def test_policy_denial_stops_loop():
    # Use unauthorized chain via loop defaults override
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
        chain_id=1,  # denied
        network="ethereum",
    )
    result = loop.step()
    assert result.status == LoopStatus.STOPPED_POLICY_DENY


def test_cross_tenant_proposal_rejected():
    loop, _ = _loop()
    loop.state.tenant_id = "tenant-attacker"
    try:
        loop.propose_purchase(usdg_in=1_000_000, min_credit_out=900_000)
        ok = True
    except PermissionError:
        ok = False
    assert ok is False
    assert loop.state.status == LoopStatus.STOPPED_CROSS_TENANT


def test_agent_never_holds_signer_or_rpc():
    loop, _ = _loop()
    assert not hasattr(loop, "signer")
    assert not hasattr(loop, "rpc_client")
    assert not hasattr(loop, "sign_transaction")


def test_duplicate_objective_does_not_double_spend_after_complete():
    loop, provider = _loop(target_credit=2_500_000)
    loop.step()
    bal_after = provider.get_usdg_balance("org-a")
    loop.step()  # complete again
    assert provider.get_usdg_balance("org-a") == bal_after
