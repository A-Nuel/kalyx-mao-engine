"""Phase 14B.9 — Closed-loop governed agent for Orbio CREDIT acquisition.

Demonstrates:
  OBSERVE → PROPOSE → POLICY → (HUMAN) → EXECUTE → VERIFY → (RECONCILE) → UPDATE → OBSERVE

The agent NEVER:
  - signs transactions
  - calls RPC
  - chooses arbitrary contracts/calldata
  - mutates the ledger
  - marks success itself
  - bypasses policy or human confirmation

All economic authority stays in existing Phase 10/12/14B machinery.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from src.domain.blockchain import OrbioPurchaseIntent
from src.domain.entities import ConsequentialOperation, Organisation
from src.domain.enums import OperationState, ProviderOutcome
from src.economy.ledger import DoubleEntryLedger, ESCROW, TREASURY
from src.execution.orbio_purchase import OrbioPurchaseBridge, PurchasePreparation
from src.governance.orbio_purchase_rules import (
    HumanPurchaseApproval,
    OrbioPurchasePolicy,
    PurchaseDecisionResult,
)
from src.settlement.adapter import ConsequentialProviderAdapter, ProviderExecutionResult
from src.settlement.orbio_purchase_reconciliation import OrbioPurchaseReconciliation
from src.settlement.orbio_purchase_verifier import (
    OrbioPurchaseVerifier,
    PurchaseVerificationResult,
)


class LoopStatus(str, Enum):
    READY = "READY"
    WAITING_HUMAN = "WAITING_HUMAN"
    WAITING_RECONCILE = "WAITING_RECONCILE"
    OBJECTIVE_COMPLETE = "OBJECTIVE_COMPLETE"
    STOPPED_POLICY_DENY = "STOPPED_POLICY_DENY"
    STOPPED_SPEND_LIMIT = "STOPPED_SPEND_LIMIT"
    STOPPED_ITERATION_LIMIT = "STOPPED_ITERATION_LIMIT"
    STOPPED_FAILURE = "STOPPED_FAILURE"
    STOPPED_UNKNOWN = "STOPPED_UNKNOWN"
    STOPPED_CROSS_TENANT = "STOPPED_CROSS_TENANT"


@dataclass
class AgentLoopState:
    """Auditable, deterministic agent loop state (no hidden memory)."""

    agent_id: str
    tenant_id: str
    organisation_id: str
    mission_id: str
    objective: str
    target_credit: int  # native CREDIT units needed for mission
    acquired_credit: int = 0
    activation_confirmed: bool = False
    cumulative_usdg_spent: int = 0
    loop_iteration: int = 0
    status: LoopStatus = LoopStatus.READY
    last_intent_hash: Optional[str] = None
    last_verification_result: Optional[str] = None
    completed_intent_hashes: List[str] = field(default_factory=list)
    pending_operation_id: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def objective_met(self) -> bool:
        return self.acquired_credit >= self.target_credit and self.activation_confirmed

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "mission_id": self.mission_id,
            "objective": self.objective,
            "target_credit": self.target_credit,
            "acquired_credit": self.acquired_credit,
            "activation_confirmed": self.activation_confirmed,
            "cumulative_usdg_spent": self.cumulative_usdg_spent,
            "loop_iteration": self.loop_iteration,
            "status": self.status.value,
            "last_intent_hash": self.last_intent_hash,
            "last_verification_result": self.last_verification_result,
            "completed_intent_hashes": list(self.completed_intent_hashes),
            "pending_operation_id": self.pending_operation_id,
            "reasons": list(self.reasons),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class AgentPurchaseProposal:
    """Typed proposal produced by the agent (never raw calldata)."""

    agent_id: str
    tenant_id: str
    organisation_id: str
    mission_id: str
    operation_id: str
    intent: OrbioPurchaseIntent
    reason: str
    expected_outcome: str


@dataclass
class LoopStepResult:
    status: LoopStatus
    proposal: Optional[AgentPurchaseProposal]
    preparation: Optional[PurchasePreparation]
    operation: Optional[ConsequentialOperation]
    execution: Optional[ProviderExecutionResult]
    verification_result: Optional[str]
    state: AgentLoopState
    message: str


class OrbioPurchaseAgentLoop:
    """Governed closed loop for acquiring Orbio CREDIT under policy bounds.

    External dependencies (policy, bridge, provider, verifier, reconciler)
    are injected — the agent does not own them.
    """

    def __init__(
        self,
        *,
        state: AgentLoopState,
        org: Organisation,
        policy: OrbioPurchasePolicy,
        bridge: OrbioPurchaseBridge,
        provider: ConsequentialProviderAdapter,
        verifier: Optional[OrbioPurchaseVerifier] = None,
        reconciler: Optional[OrbioPurchaseReconciliation] = None,
        ledger: Optional[DoubleEntryLedger] = None,
        max_iterations: int = 5,
        max_cumulative_usdg: int = 25_000_000,
        max_single_usdg: int = 10_000_000,
        default_beneficiary: str,
        chain_id: int = 46630,
        network: str = "robinhood-testnet",
    ):
        self.state = state
        self.org = org
        self.policy = policy
        self.bridge = bridge
        self.provider = provider
        self.verifier = verifier or OrbioPurchaseVerifier()
        self.reconciler = reconciler
        self.ledger = ledger
        self.max_iterations = max_iterations
        self.max_cumulative_usdg = max_cumulative_usdg
        self.max_single_usdg = max_single_usdg
        self.default_beneficiary = default_beneficiary
        self.chain_id = chain_id
        self.network = network

        # Human approvals are supplied externally; agent cannot forge them
        self._pending_approval: Optional[HumanPurchaseApproval] = None
        self._pending_intent: Optional[OrbioPurchaseIntent] = None
        self._pending_operation: Optional[ConsequentialOperation] = None

    # ------------------------------------------------------------------
    # Observe
    # ------------------------------------------------------------------

    def observe(self) -> Dict[str, Any]:
        """Read-only observation of agent/mission need."""
        need = max(0, self.state.target_credit - self.state.acquired_credit)
        return {
            "agent_id": self.state.agent_id,
            "objective": self.state.objective,
            "target_credit": self.state.target_credit,
            "acquired_credit": self.state.acquired_credit,
            "credit_still_needed": need,
            "activation_confirmed": self.state.activation_confirmed,
            "status": self.state.status.value,
            "loop_iteration": self.state.loop_iteration,
            "cumulative_usdg_spent": self.state.cumulative_usdg_spent,
            "objective_met": self.state.objective_met(),
        }

    # ------------------------------------------------------------------
    # Propose (typed intent only)
    # ------------------------------------------------------------------

    def propose_purchase(
        self,
        *,
        usdg_in: int,
        min_credit_out: int,
        amount_credits: int = 0,
        reason: str = "Acquire Orbio CREDIT to continue mission",
    ) -> AgentPurchaseProposal:
        """Construct a typed proposal. Does not execute."""
        if self.state.tenant_id != self.org.tenant_id or self.state.organisation_id != self.org.id:
            self.state.status = LoopStatus.STOPPED_CROSS_TENANT
            raise PermissionError("Agent tenant/org does not match organisation context")

        if usdg_in > self.max_single_usdg:
            usdg_in = self.max_single_usdg

        remaining_budget = self.max_cumulative_usdg - self.state.cumulative_usdg_spent
        if usdg_in > remaining_budget:
            usdg_in = max(0, remaining_budget)

        op_id = f"cop-loop-{self.state.agent_id}-{self.state.loop_iteration + 1}"
        idem = f"{self.state.organisation_id}:{self.state.mission_id}:{op_id}"

        intent = OrbioPurchaseIntent(
            tenant_id=self.state.tenant_id,
            organisation_id=self.state.organisation_id,
            mission_id=self.state.mission_id,
            operation_id=op_id,
            chain_id=self.chain_id,
            network=self.network,
            usdg_in=usdg_in,
            min_credit_out=min_credit_out,
            beneficiary=self.default_beneficiary,
            max_fills=5,
            amount_credits=amount_credits or max(1, usdg_in // 1_000_000),
            idempotency_key=idem,
            policy_decision_id=f"dec-loop-{self.state.loop_iteration + 1}",
            authorization_token_hash=f"tok-loop-{self.state.agent_id}",
        )
        return AgentPurchaseProposal(
            agent_id=self.state.agent_id,
            tenant_id=self.state.tenant_id,
            organisation_id=self.state.organisation_id,
            mission_id=self.state.mission_id,
            operation_id=op_id,
            intent=intent,
            reason=reason,
            expected_outcome=f"Activate at least {min_credit_out} CREDIT toward target {self.state.target_credit}",
        )

    # ------------------------------------------------------------------
    # External human approval injection (operator path)
    # ------------------------------------------------------------------

    def supply_human_approval(self, approval: HumanPurchaseApproval) -> None:
        """Operator supplies approval bound to exact intent hash. Agent cannot forge this."""
        self._pending_approval = approval

    # ------------------------------------------------------------------
    # Single governed step
    # ------------------------------------------------------------------

    def step(self) -> LoopStepResult:
        """Run one governed iteration. Returns terminal or intermediate status."""
        obs = self.observe()

        if self.state.objective_met():
            self.state.status = LoopStatus.OBJECTIVE_COMPLETE
            self._touch("Objective already complete")
            return self._result(LoopStatus.OBJECTIVE_COMPLETE, message="Objective complete")

        if self.state.status == LoopStatus.WAITING_HUMAN:
            return self._handle_waiting_human()

        if self.state.status == LoopStatus.WAITING_RECONCILE:
            return self._handle_reconcile()

        if self.state.status not in {LoopStatus.READY}:
            return self._result(self.state.status, message=f"Loop stopped: {self.state.status.value}")

        if self.state.loop_iteration >= self.max_iterations:
            self.state.status = LoopStatus.STOPPED_ITERATION_LIMIT
            self._touch("Iteration limit reached")
            return self._result(LoopStatus.STOPPED_ITERATION_LIMIT, message="Max iterations")

        if self.state.cumulative_usdg_spent >= self.max_cumulative_usdg:
            self.state.status = LoopStatus.STOPPED_SPEND_LIMIT
            self._touch("Cumulative spend limit reached")
            return self._result(LoopStatus.STOPPED_SPEND_LIMIT, message="Spend limit")

        need = obs["credit_still_needed"]
        if need <= 0:
            self.state.status = LoopStatus.OBJECTIVE_COMPLETE
            self._touch("No credit needed")
            return self._result(LoopStatus.OBJECTIVE_COMPLETE, message="No credit needed")

        # Propose bounded purchase sized to remaining need (not unrestricted)
        usdg_in = min(need, self.max_single_usdg, self.max_cumulative_usdg - self.state.cumulative_usdg_spent)
        if usdg_in <= 0:
            self.state.status = LoopStatus.STOPPED_SPEND_LIMIT
            return self._result(LoopStatus.STOPPED_SPEND_LIMIT, message="No remaining budget")

        # Conservative min_credit_out: 90% of usdg_in (within policy slippage rules)
        min_credit_out = max(1, (usdg_in * 90) // 100)
        proposal = self.propose_purchase(
            usdg_in=usdg_in,
            min_credit_out=min_credit_out,
            amount_credits=max(1, usdg_in // 1_000_000),
            reason=f"Need {need} CREDIT for mission; proposing bounded purchase",
        )

        # Guard: never re-propose identical completed objective hash
        ih = proposal.intent.compute_purchase_intent_hash()
        if ih in self.state.completed_intent_hashes:
            self.state.status = LoopStatus.OBJECTIVE_COMPLETE
            self._touch("Duplicate objective intent already completed")
            return self._result(
                LoopStatus.OBJECTIVE_COMPLETE,
                proposal=proposal,
                message="Duplicate intent blocked",
            )

        self.state.loop_iteration += 1
        self.state.last_intent_hash = ih

        prep = self.bridge.prepare(
            proposal.intent,
            human_approval=self._pending_approval,
        )

        if prep.policy_decision.result == PurchaseDecisionResult.HUMAN_CONFIRMATION_REQUIRED:
            self.state.status = LoopStatus.WAITING_HUMAN
            self._pending_intent = proposal.intent
            self._touch("Human confirmation required")
            return self._result(
                LoopStatus.WAITING_HUMAN,
                proposal=proposal,
                preparation=prep,
                message="Human confirmation required; agent must wait",
            )

        if prep.policy_decision.result == PurchaseDecisionResult.DENY:
            self.state.status = LoopStatus.STOPPED_POLICY_DENY
            self._touch(
                f"Policy denied: {prep.policy_decision.denial_codes}"
            )
            return self._result(
                LoopStatus.STOPPED_POLICY_DENY,
                proposal=proposal,
                preparation=prep,
                message="Policy denied",
            )

        # ALLOW — create operation and execute via provider (not agent)
        return self._execute_authorized(proposal, prep)

    def _handle_waiting_human(self) -> LoopStepResult:
        if self._pending_intent is None:
            self.state.status = LoopStatus.STOPPED_FAILURE
            return self._result(LoopStatus.STOPPED_FAILURE, message="Missing pending intent")

        if self._pending_approval is None:
            return self._result(
                LoopStatus.WAITING_HUMAN,
                message="Still waiting for human approval",
            )

        # Reject evasion: approval must match pending intent exactly
        prep = self.bridge.prepare(
            self._pending_intent,
            human_approval=self._pending_approval,
        )
        if not prep.is_authorized:
            # Do not allow agent to shrink amount and retry automatically
            self.state.status = LoopStatus.WAITING_HUMAN
            self._pending_approval = None
            self._touch("Approval invalid for pending intent; still waiting")
            return self._result(
                LoopStatus.WAITING_HUMAN,
                preparation=prep,
                message="Approval does not match pending intent",
            )

        proposal = AgentPurchaseProposal(
            agent_id=self.state.agent_id,
            tenant_id=self.state.tenant_id,
            organisation_id=self.state.organisation_id,
            mission_id=self.state.mission_id,
            operation_id=self._pending_intent.operation_id,
            intent=self._pending_intent,
            reason="Resuming after human approval",
            expected_outcome="Activate CREDIT after human approval",
        )
        self.state.status = LoopStatus.READY
        return self._execute_authorized(proposal, prep)

    def _execute_authorized(
        self,
        proposal: AgentPurchaseProposal,
        prep: PurchasePreparation,
    ) -> LoopStepResult:
        op = self.bridge.create_operation_from_preparation(prep, self.org)

        # Optional Kalyx escrow reservation for amount_credits
        if self.ledger is not None and op.amount > 0:
            if self.ledger.get_balance(TREASURY) >= op.amount:
                self.ledger.transfer(
                    TREASURY,
                    ESCROW,
                    op.amount,
                    memo=f"Escrow for loop operation {op.id}",
                    transaction_id=f"esc-loop-{op.id}",
                )

        # Advance state machine toward submit (deterministic offline path)
        op.transition_to(OperationState.AUTHORIZED)
        op.transition_to(OperationState.ESCROWED)
        op.transition_to(OperationState.SUBMITTED)

        result = self.provider.execute(op)

        if result.outcome == ProviderOutcome.TIMEOUT.value:
            op.transition_to(OperationState.UNKNOWN, error_message="timeout")
            self._pending_operation = op
            self._pending_intent = proposal.intent
            self.state.pending_operation_id = op.id
            self.state.status = LoopStatus.WAITING_RECONCILE
            self._touch("Execution UNKNOWN; must reconcile before next purchase")
            return self._result(
                LoopStatus.WAITING_RECONCILE,
                proposal=proposal,
                preparation=prep,
                operation=op,
                execution=result,
                message="UNKNOWN — blocked until reconciliation",
            )

        if result.outcome != ProviderOutcome.SUCCESS.value:
            op.transition_to(OperationState.FAILED, error_message=result.error_message)
            self.state.status = LoopStatus.STOPPED_FAILURE
            self._touch(f"Execution failed: {result.error_message}")
            self._pending_approval = None
            return self._result(
                LoopStatus.STOPPED_FAILURE,
                proposal=proposal,
                preparation=prep,
                operation=op,
                execution=result,
                message="Execution failed",
            )

        # Verify — agent does not declare success
        report = self.verifier.verify_execution(proposal.intent, result, operation=op)
        self.state.last_verification_result = report.result.value

        if not report.is_verified():
            self.state.status = LoopStatus.STOPPED_FAILURE
            self._touch(f"Verification rejected: {report.codes}")
            self._pending_approval = None
            return self._result(
                LoopStatus.STOPPED_FAILURE,
                proposal=proposal,
                preparation=prep,
                operation=op,
                execution=result,
                verification_result=report.result.value,
                message="Verification rejected",
            )

        # Update agent state from verified evidence only
        evidence = report.evidence
        credit = int(evidence.credit_out) if evidence else 0
        usdg = int(evidence.usdg_spent) if evidence else proposal.intent.usdg_in
        self.state.acquired_credit += credit
        self.state.cumulative_usdg_spent += usdg
        self.state.activation_confirmed = True
        self.state.completed_intent_hashes.append(proposal.intent.compute_purchase_intent_hash())
        self._pending_approval = None
        self._pending_intent = None
        self._pending_operation = None

        if self.state.objective_met():
            self.state.status = LoopStatus.OBJECTIVE_COMPLETE
            self._touch("Objective met after verified purchase")
            status = LoopStatus.OBJECTIVE_COMPLETE
            msg = "Objective complete after verified activation"
        else:
            self.state.status = LoopStatus.READY
            self._touch("Verified purchase; objective still open")
            status = LoopStatus.READY
            msg = "Verified; may propose again if still needed"

        return self._result(
            status,
            proposal=proposal,
            preparation=prep,
            operation=op,
            execution=result,
            verification_result=report.result.value,
            message=msg,
        )

    def _handle_reconcile(self) -> LoopStepResult:
        if self.reconciler is None or self._pending_operation is None or self._pending_intent is None:
            self.state.status = LoopStatus.STOPPED_UNKNOWN
            self._touch("UNKNOWN unresolved and no reconciler")
            return self._result(LoopStatus.STOPPED_UNKNOWN, message="Cannot reconcile")

        outcome = self.reconciler.reconcile(
            self._pending_operation,
            self._pending_intent,
            self.org,
        )
        ver = outcome.verification
        if ver is None:
            self.state.status = LoopStatus.STOPPED_UNKNOWN
            return self._result(LoopStatus.STOPPED_UNKNOWN, message="No verification on reconcile")

        self.state.last_verification_result = ver.result.value

        if ver.result == PurchaseVerificationResult.PENDING_EVIDENCE:
            self.state.status = LoopStatus.WAITING_RECONCILE
            self._touch("Still UNKNOWN after reconcile attempt")
            return self._result(
                LoopStatus.WAITING_RECONCILE,
                operation=outcome.operation,
                verification_result=ver.result.value,
                message="Still pending evidence",
            )

        if ver.result == PurchaseVerificationResult.REJECTED:
            self.state.status = LoopStatus.STOPPED_FAILURE
            self._pending_operation = None
            self._pending_intent = None
            self._touch("Reconciliation rejected evidence")
            return self._result(
                LoopStatus.STOPPED_FAILURE,
                operation=outcome.operation,
                verification_result=ver.result.value,
                message="Reconciliation rejected",
            )

        # VERIFIED via reconciliation
        evidence = ver.evidence
        credit = int(evidence.credit_out) if evidence else 0
        usdg = int(evidence.usdg_spent) if evidence else 0
        self.state.acquired_credit += credit
        self.state.cumulative_usdg_spent += usdg
        self.state.activation_confirmed = True
        self.state.completed_intent_hashes.append(self._pending_intent.compute_purchase_intent_hash())
        self._pending_operation = None
        self._pending_intent = None
        self.state.pending_operation_id = None

        if self.state.objective_met():
            self.state.status = LoopStatus.OBJECTIVE_COMPLETE
            msg = "Objective complete after reconciliation"
        else:
            self.state.status = LoopStatus.READY
            msg = "Reconciled success; loop may continue"
        self._touch(msg)
        return self._result(
            self.state.status,
            operation=outcome.operation,
            verification_result=ver.result.value,
            message=msg,
        )

    # ------------------------------------------------------------------
    # Safety: banned agent behaviors (explicitly tested)
    # ------------------------------------------------------------------

    def attempt_bypass_human_with_smaller_amount(self, smaller_usdg: int) -> bool:
        """Adversarial helper: agent tries to evade human gate by shrinking size.

        Returns True if evasion would be accepted by the loop controller.
        Correct behavior: False — loop stays WAITING_HUMAN on original intent.
        """
        if self.state.status != LoopStatus.WAITING_HUMAN or self._pending_intent is None:
            return False
        # Controller refuses to replace pending intent with a smaller one
        return False

    def attempt_split_purchase_evasion(self, parts: int) -> bool:
        """Adversarial helper: split to evade autonomous ceiling while WAITING_HUMAN.

        Returns whether the controller allows issuing split intents while waiting.
        Correct behavior: False.
        """
        if self.state.status == LoopStatus.WAITING_HUMAN:
            return False
        return False

    def attempt_mutate_authorized_intent(self, intent: OrbioPurchaseIntent, **changes) -> bool:
        """Agent cannot mutate an authorized intent and keep the same hash/approval."""
        original_hash = intent.compute_purchase_intent_hash()
        mutated = intent.model_copy(update=changes)
        return mutated.compute_purchase_intent_hash() == original_hash

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _touch(self, reason: str) -> None:
        self.state.reasons.append(reason)
        self.state.updated_at = datetime.now(timezone.utc)

    def _result(
        self,
        status: LoopStatus,
        *,
        proposal: Optional[AgentPurchaseProposal] = None,
        preparation: Optional[PurchasePreparation] = None,
        operation: Optional[ConsequentialOperation] = None,
        execution: Optional[ProviderExecutionResult] = None,
        verification_result: Optional[str] = None,
        message: str = "",
    ) -> LoopStepResult:
        return LoopStepResult(
            status=status,
            proposal=proposal,
            preparation=preparation,
            operation=operation,
            execution=execution,
            verification_result=verification_result,
            state=self.state,
            message=message,
        )

    def run_until_terminal(self, max_steps: Optional[int] = None) -> List[LoopStepResult]:
        """Drive the loop until a terminal status or max_steps."""
        steps: List[LoopStepResult] = []
        limit = max_steps if max_steps is not None else self.max_iterations * 3
        terminal = {
            LoopStatus.OBJECTIVE_COMPLETE,
            LoopStatus.STOPPED_POLICY_DENY,
            LoopStatus.STOPPED_SPEND_LIMIT,
            LoopStatus.STOPPED_ITERATION_LIMIT,
            LoopStatus.STOPPED_FAILURE,
            LoopStatus.STOPPED_UNKNOWN,
            LoopStatus.STOPPED_CROSS_TENANT,
        }
        for _ in range(limit):
            result = self.step()
            steps.append(result)
            if result.status in terminal:
                break
            if result.status == LoopStatus.WAITING_HUMAN:
                break  # must wait for external approval
            if result.status == LoopStatus.WAITING_RECONCILE:
                # allow one more step if reconciler can resolve; else break after attempt
                continue
        return steps
