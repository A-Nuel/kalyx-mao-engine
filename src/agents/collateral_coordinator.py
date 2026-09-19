"""Phase 18 collateral orchestration.

The coordinator is fail-closed on policy authorization: callers must inject a
validator that verifies the exact policy authorization evidence for the tenant,
organisation, and obligation. It never treats an arbitrary string as proof of
authorization.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from src.domain.collateral import CollateralStatus, CreditCollateralPosition
from src.domain.enums import DeliverableStatus
from src.domain.work_order import WorkDeliverable, WorkDeliverableReceipt, WorkOrder
from src.external.orbio.collateral_adapter import CollateralVaultAdapter
from src.settlement.work_verifier import WorkDeliverableVerifier

logger = logging.getLogger(__name__)

PolicyAuthorizationValidator = Callable[[str, str, str, str], bool]


@dataclass
class CollateralSettlementOutcome:
    position: CreditCollateralPosition
    verifier_receipt: WorkDeliverableReceipt
    vault_tx_hash: Optional[str]
    is_simulated: bool


class CollateralCoordinator:
    def __init__(
        self,
        vault: CollateralVaultAdapter,
        verifier: WorkDeliverableVerifier,
        policy_authorization_validator: PolicyAuthorizationValidator,
    ):
        self.vault = vault
        self.verifier = verifier
        self.policy_authorization_validator = policy_authorization_validator

    def propose_and_lock(
        self,
        *,
        tenant_id: str,
        organisation_id: str,
        obligation_reference: str,
        pledging_org_id: str,
        pledging_org_wallet_address: str,
        beneficiary_org_id: str,
        beneficiary_wallet_address: str,
        amount_atoms: int,
        policy_authorization_evidence_hash: str,
    ) -> CreditCollateralPosition:
        if not policy_authorization_evidence_hash:
            raise PermissionError("collateral lock requires policy authorization evidence")

        authorized = self.policy_authorization_validator(
            policy_authorization_evidence_hash,
            tenant_id,
            organisation_id,
            obligation_reference,
        )
        if not authorized:
            raise PermissionError("collateral lock rejected: policy authorization could not be verified")

        position = CreditCollateralPosition.create(
            tenant_id=tenant_id,
            organisation_id=organisation_id,
            position_id=f"collateral-{uuid.uuid4().hex[:10]}",
            obligation_reference=obligation_reference,
            pledging_org_id=pledging_org_id,
            beneficiary_org_id=beneficiary_org_id,
            amount=amount_atoms,
        )
        position.authorize(evidence_hash=policy_authorization_evidence_hash)

        receipt = self.vault.lock(
            position_id=position.position_id,
            pledger_address=pledging_org_wallet_address,
            beneficiary_address=beneficiary_wallet_address,
            amount_atoms=amount_atoms,
        )
        position.lock(onchain_tx_hash=receipt.tx_hash)
        position.activate_obligation()

        logger.info(
            "collateral position %s locked (%s, tx=%s) for obligation %s",
            position.position_id,
            "simulated" if receipt.is_simulated else "on-chain",
            receipt.tx_hash,
            obligation_reference,
        )
        return position

    def settle_from_verification(
        self,
        *,
        position: CreditCollateralPosition,
        work_order: WorkOrder,
        deliverable: WorkDeliverable,
    ) -> CollateralSettlementOutcome:
        verifier_receipt = self.verifier.verify(work_order, deliverable)
        success = verifier_receipt.status == DeliverableStatus.ACCEPTED

        position.record_verification(
            success=success,
            evidence_hash=verifier_receipt.evidence_hash,
        )

        if position.status == CollateralStatus.VERIFIED_SUCCESS:
            vault_receipt = self.vault.release(position_id=position.position_id)
        else:
            vault_receipt = self.vault.forfeit(position_id=position.position_id)

        position.settle(settlement_tx_hash=vault_receipt.tx_hash)

        return CollateralSettlementOutcome(
            position=position,
            verifier_receipt=verifier_receipt,
            vault_tx_hash=vault_receipt.tx_hash,
            is_simulated=vault_receipt.is_simulated,
        )
