"""Phase 20B — Bridge activation intent into Phase 10/12 machinery.

Does NOT sign, broadcast, or call RPC. Submission remains BlockchainSettlementProvider.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.domain.blockchain import BlockchainTransactionIntent
from src.domain.entities import ConsequentialOperation, Organisation
from src.domain.enums import ActionType, OperationState
from src.domain.exceptions import PolicyViolationError, UnauthorizedActionError
from src.domain.orbio_activation import OrbioCreditActivationIntent
from src.governance.orbio_activation_rules import (
    ActivationDecisionResult,
    ActivationPolicyDecision,
    HumanActivationApproval,
    OrbioCreditActivationPolicy,
)


@dataclass(frozen=True)
class ActivationPreparation:
    activation_intent: OrbioCreditActivationIntent
    activation_intent_hash: str
    policy_decision: ActivationPolicyDecision
    blockchain_intent: Optional[BlockchainTransactionIntent]
    operation_parameters: Dict[str, Any]

    @property
    def is_authorized(self) -> bool:
        return self.policy_decision.result == ActivationDecisionResult.ALLOW


class OrbioCreditActivationBridge:
    """Maps typed activation intents to consequential operations."""

    def __init__(self, policy: Optional[OrbioCreditActivationPolicy] = None):
        self.policy = policy or OrbioCreditActivationPolicy()

    def prepare(
        self,
        intent: OrbioCreditActivationIntent,
        *,
        human_approval: Optional[HumanActivationApproval] = None,
        current_time: Optional[float] = None,
        runtime_mode: Optional[str] = None,
    ) -> ActivationPreparation:
        decision = self.policy.evaluate(
            intent,
            human_approval=human_approval,
            current_time=current_time,
            runtime_mode=runtime_mode,
        )
        intent_hash = intent.compute_activation_intent_hash()

        if decision.result != ActivationDecisionResult.ALLOW:
            return ActivationPreparation(
                activation_intent=intent,
                activation_intent_hash=intent_hash,
                policy_decision=decision,
                blockchain_intent=None,
                operation_parameters={},
            )

        bc = intent.to_blockchain_intent()
        params = intent.to_operation_parameters()
        params["policy_decision"] = decision.to_audit_dict()
        return ActivationPreparation(
            activation_intent=intent,
            activation_intent_hash=intent_hash,
            policy_decision=decision,
            blockchain_intent=bc,
            operation_parameters=params,
        )

    def create_operation_from_preparation(
        self,
        preparation: ActivationPreparation,
        org: Organisation,
        *,
        proposal_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        provider_name: str = "blockchain",
        idempotency_key: Optional[str] = None,
    ) -> ConsequentialOperation:
        if not preparation.is_authorized:
            codes = ",".join(preparation.policy_decision.denial_codes) or "DENIED"
            raise PolicyViolationError(
                f"Orbio activation not authorized ({preparation.policy_decision.result.value}): {codes}"
            )
        if preparation.blockchain_intent is None:
            raise PolicyViolationError("Missing blockchain intent after ALLOW")

        key = idempotency_key or preparation.activation_intent.idempotency_key
        prop_id = proposal_id or f"prop-{preparation.activation_intent.operation_id}"
        dec_id = decision_id or preparation.policy_decision.decision_id

        return ConsequentialOperation(
            id=f"cop-{uuid.uuid4().hex[:12]}",
            tenant_id=preparation.activation_intent.tenant_id,
            organisation_id=org.id,
            proposal_id=prop_id,
            decision_id=dec_id,
            idempotency_key=key,
            action_type=ActionType.ORBIO_CREDIT_ACTIVATION,
            target=f"orbio://credit/{preparation.activation_intent.credit_contract}",
            parameters=preparation.operation_parameters,
            amount=preparation.activation_intent.amount_credits,
            provider_name=provider_name,
            state=OperationState.CREATED,
        )

    def prepare_and_create_operation(
        self,
        intent: OrbioCreditActivationIntent,
        org: Organisation,
        *,
        human_approval: Optional[HumanActivationApproval] = None,
        current_time: Optional[float] = None,
        runtime_mode: Optional[str] = None,
        provider_name: str = "blockchain",
    ) -> tuple[ActivationPreparation, ConsequentialOperation]:
        if intent.organisation_id != org.id:
            raise UnauthorizedActionError(
                f"Activation intent organisation '{intent.organisation_id}' does not match org '{org.id}'"
            )
        org_tenant = getattr(org, "tenant_id", intent.tenant_id)
        if intent.tenant_id != org_tenant:
            raise UnauthorizedActionError("Activation intent tenant does not match organisation tenant")

        preparation = self.prepare(
            intent,
            human_approval=human_approval,
            current_time=current_time,
            runtime_mode=runtime_mode,
        )
        operation = self.create_operation_from_preparation(
            preparation,
            org,
            provider_name=provider_name,
        )
        return preparation, operation
