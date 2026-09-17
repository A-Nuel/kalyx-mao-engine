"""Phase 14B.3 — Bridge Orbio purchase intents into Phase 10/12 machinery.

Flow:
  OrbioPurchaseIntent
    → OrbioPurchasePolicy.evaluate
    → (optional human approval)
    → to_blockchain_intent()
    → ConsequentialOperation (CREATED)

Does NOT sign, broadcast, or talk to RPC. Submission remains the existing
ConsequentialExecutionManager + BlockchainSettlementProvider path.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.domain.blockchain import BlockchainTransactionIntent, OrbioPurchaseIntent
from src.domain.entities import ActionProposal, ConsequentialOperation, Organisation
from src.domain.enums import ActionType, OperationState
from src.domain.exceptions import PolicyViolationError, UnauthorizedActionError
from src.governance.orbio_purchase_rules import (
    HumanPurchaseApproval,
    OrbioPurchasePolicy,
    PurchaseDecisionResult,
    PurchasePolicyDecision,
)


@dataclass(frozen=True)
class PurchasePreparation:
    """Result of policy evaluation + intent projection (pre-execution)."""

    purchase_intent: OrbioPurchaseIntent
    purchase_intent_hash: str
    policy_decision: PurchasePolicyDecision
    blockchain_intent: Optional[BlockchainTransactionIntent]
    operation_parameters: Dict[str, Any]

    @property
    def is_authorized(self) -> bool:
        return self.policy_decision.result == PurchaseDecisionResult.ALLOW


class OrbioPurchaseBridge:
    """Integrates typed purchase intents with existing consequential boundary.

    Responsibilities:
    - Run OrbioPurchasePolicy
    - On ALLOW, project to BlockchainTransactionIntent
    - Build ConsequentialOperation parameters consumable by Phase 12 provider
    - Optionally create the durable CREATED operation via a supplied repo/manager

    Non-responsibilities:
    - Signing, nonce allocation, RPC, receipt verification (Phase 12 provider)
    - Escrow / state machine transitions beyond CREATED (Phase 10 manager)
    """

    def __init__(self, purchase_policy: Optional[OrbioPurchasePolicy] = None):
        self.purchase_policy = purchase_policy or OrbioPurchasePolicy()

    def prepare(
        self,
        intent: OrbioPurchaseIntent,
        *,
        human_approval: Optional[HumanPurchaseApproval] = None,
        current_time: Optional[float] = None,
    ) -> PurchasePreparation:
        """Evaluate policy and, if allowed, project to blockchain intent + params."""
        decision = self.purchase_policy.evaluate(
            intent,
            human_approval=human_approval,
            current_time=current_time,
        )
        intent_hash = intent.compute_purchase_intent_hash()

        if decision.result != PurchaseDecisionResult.ALLOW:
            return PurchasePreparation(
                purchase_intent=intent,
                purchase_intent_hash=intent_hash,
                policy_decision=decision,
                blockchain_intent=None,
                operation_parameters={},
            )

        bc_intent = intent.to_blockchain_intent()
        params = self._operation_parameters(intent, bc_intent, decision)
        return PurchasePreparation(
            purchase_intent=intent,
            purchase_intent_hash=intent_hash,
            policy_decision=decision,
            blockchain_intent=bc_intent,
            operation_parameters=params,
        )

    def _operation_parameters(
        self,
        purchase: OrbioPurchaseIntent,
        bc: BlockchainTransactionIntent,
        decision: PurchasePolicyDecision,
    ) -> Dict[str, Any]:
        """Parameters shape expected by BlockchainSettlementProvider._build_intent."""
        return {
            # Phase 12 blockchain fields
            "recipient": bc.recipient,
            "amount_wei": bc.amount_wei,
            "chain_id": bc.chain_id,
            "network": bc.network,
            "data_payload": bc.data_payload,
            "token_contract": bc.token_contract,
            "asset": bc.asset,
            "max_fee_per_gas": bc.max_fee_per_gas,
            "max_priority_fee_per_gas": bc.max_priority_fee_per_gas,
            "gas_limit": bc.gas_limit,
            "mission_id": purchase.mission_id,
            "authorization_token": purchase.authorization_token_hash,
            # Purchase semantics (audit / later event verification)
            "purchase_intent_hash": purchase.compute_purchase_intent_hash(),
            "usdg_in": purchase.usdg_in,
            "min_credit_out": purchase.min_credit_out,
            "beneficiary": purchase.beneficiary,
            "beneficiary_bytes32": purchase.beneficiary_as_bytes32(),
            "max_fills": purchase.max_fills,
            "exchange_contract": purchase.exchange_contract,
            "payment_token": purchase.payment_token,
            "credit_token": purchase.credit_token,
            "function": "buyAndActivate",
            "policy_decision": decision.to_audit_dict(),
        }

    def build_action_proposal(
        self,
        intent: OrbioPurchaseIntent,
        *,
        proposing_agent_id: str,
        task_id: str,
        rationale: str = "Bounded Orbio CREDIT purchase for external inference capacity",
    ) -> ActionProposal:
        """Materialize an ActionProposal for the general PolicyEngine path if needed."""
        return ActionProposal(
            id=f"prop-orbio-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            proposing_agent_id=proposing_agent_id,
            action_type=ActionType.ORBIO_CREDIT_PURCHASE,
            target=f"orbio://exchange/{intent.exchange_contract}",
            parameters={
                "chain_id": intent.chain_id,
                "network": intent.network,
                "usdg_in": intent.usdg_in,
                "min_credit_out": intent.min_credit_out,
                "beneficiary": intent.beneficiary,
                "max_fills": intent.max_fills,
                "exchange_contract": intent.exchange_contract,
                "purchase_intent_hash": intent.compute_purchase_intent_hash(),
            },
            requested_credits=intent.amount_credits,
            expected_value_score=0.5,
            risk_assessment="economic_external_resource_acquisition",
            rationale=rationale,
        )

    def create_operation_from_preparation(
        self,
        preparation: PurchasePreparation,
        org: Organisation,
        *,
        proposal_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        provider_name: str = "blockchain",
        idempotency_key: Optional[str] = None,
    ) -> ConsequentialOperation:
        """Create a CREATED ConsequentialOperation after a successful policy allow.

        Raises PolicyViolationError if preparation is not ALLOW.
        """
        if not preparation.is_authorized:
            codes = ",".join(preparation.policy_decision.denial_codes) or "DENIED"
            raise PolicyViolationError(
                f"Orbio purchase not authorized ({preparation.policy_decision.result.value}): {codes}"
            )
        if preparation.blockchain_intent is None:
            raise PolicyViolationError("Missing blockchain intent projection after ALLOW")

        key = idempotency_key or preparation.purchase_intent.idempotency_key
        prop_id = proposal_id or f"prop-{preparation.purchase_intent.operation_id}"
        dec_id = decision_id or preparation.policy_decision.decision_id

        return ConsequentialOperation(
            id=f"cop-{uuid.uuid4().hex[:12]}",
            tenant_id=preparation.purchase_intent.tenant_id,
            organisation_id=org.id,
            proposal_id=prop_id,
            decision_id=dec_id,
            idempotency_key=key,
            action_type=ActionType.ORBIO_CREDIT_PURCHASE,
            target=f"orbio://exchange/{preparation.purchase_intent.exchange_contract}",
            parameters=preparation.operation_parameters,
            amount=preparation.purchase_intent.amount_credits,
            provider_name=provider_name,
            state=OperationState.CREATED,
        )

    def prepare_and_create_operation(
        self,
        intent: OrbioPurchaseIntent,
        org: Organisation,
        *,
        human_approval: Optional[HumanPurchaseApproval] = None,
        current_time: Optional[float] = None,
        provider_name: str = "blockchain",
    ) -> tuple[PurchasePreparation, ConsequentialOperation]:
        """Convenience: evaluate → authorize → create CREATED operation."""
        if intent.organisation_id != org.id:
            raise UnauthorizedActionError(
                f"Purchase intent organisation '{intent.organisation_id}' does not match org '{org.id}'"
            )
        if intent.tenant_id != getattr(org, "tenant_id", intent.tenant_id):
            raise UnauthorizedActionError("Purchase intent tenant does not match organisation tenant")

        preparation = self.prepare(
            intent,
            human_approval=human_approval,
            current_time=current_time,
        )
        operation = self.create_operation_from_preparation(
            preparation,
            org,
            provider_name=provider_name,
        )
        return preparation, operation

    def verify_operation_matches_intent(
        self,
        operation: ConsequentialOperation,
        intent: OrbioPurchaseIntent,
    ) -> tuple[bool, Optional[str]]:
        """Detect parameter mutation between authorized intent and durable operation."""
        expected_hash = intent.compute_purchase_intent_hash()
        stored = (operation.parameters or {}).get("purchase_intent_hash")
        if stored != expected_hash:
            return False, (
                f"Operation purchase_intent_hash mismatch: stored={stored}, expected={expected_hash}"
            )

        expected_calldata = intent.encode_calldata()
        stored_calldata = (operation.parameters or {}).get("data_payload")
        if stored_calldata != expected_calldata:
            return False, "Operation data_payload does not match authorized purchase calldata"

        expected_recipient = intent.exchange_contract.lower()
        stored_recipient = (operation.parameters or {}).get("recipient", "").lower()
        if stored_recipient != expected_recipient:
            return False, (
                f"Operation recipient mismatch: stored={stored_recipient}, expected={expected_recipient}"
            )

        return True, None
