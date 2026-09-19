"""Phase 18 Collateral Coordinator — pledges CREDIT collateral behind a B2B
work order and settles it exclusively from WorkDeliverableVerifier evidence.

Composes with, rather than modifies, B2BMarketplaceCoordinator: an order's
USDG bounty escrow (EscrowAgreement) and its optional CREDIT collateral
position are independent commitments that can be reasoned about, tested,
and audited separately. This keeps the existing, already-tested B2B
marketplace flow untouched while adding the new primitive alongside it —
deliberately, given the collateral piece is new and the marketplace
coordinator is not.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from src.domain.collateral import CollateralStatus, CreditCollateralPosition
from src.domain.enums import DeliverableStatus
from src.domain.work_order import WorkDeliverable, WorkDeliverableReceipt, WorkOrder
from src.external.orbio.collateral_adapter import CollateralVaultAdapter
from src.settlement.work_verifier import WorkDeliverableVerifier

logger = logging.getLogger(__name__)


@dataclass
class CollateralSettlementOutcome:
    position: CreditCollateralPosition
    verifier_receipt: WorkDeliverableReceipt
    vault_tx_hash: Optional[str]
    is_simulated: bool


class CollateralCoordinator:
    """Orchestrates the collateral side of a B2B work order:

        propose -> authorize (policy) -> lock (on-chain/simulated vault)
        -> [work happens elsewhere, via the normal B2B flow] ->
        settle (driven ONLY by WorkDeliverableVerifier's independent receipt)

    This class never accepts a plain boolean "did it succeed" from a caller.
    settle_from_verification() takes a WorkDeliverableReceipt and derives
    success/failure from its .status field — the same independent evidence
    the rest of Kalyx's settlement already trusts — so there is no path
    for an executor's self-report to reach collateral settlement directly.
    """

    def __init__(self, vault: CollateralVaultAdapter, verifier: WorkDeliverableVerifier):
        self.vault = vault
        self.verifier = verifier

    def propose_and_lock(
        self,
        *,
        tenant_id: str,
        organisation_id: str,
        obligation_reference: str,
        pledging_org_id: str,
        pledging_org_wallet_address: str,
        beneficiary_org_id: str,
        amount_atoms: int,
        policy_authorization_evidence_hash: str,
    ) -> CreditCollateralPosition:
        """Creates a position and drives it PROPOSED -> AUTHORIZED -> LOCKED.

        `policy_authorization_evidence_hash` must come from an actual policy
        engine decision made by the caller before this is invoked — this
        method does not itself re-run policy evaluation. It records the
        hash it's given; it does not manufacture one, so a caller cannot
        skip real authorization and still produce a position that looks
        authorized.
        """
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
        beneficiary_wallet_address: str,
    ) -> CollateralSettlementOutcome:
        """Runs the independent verifier and settles the position from ITS
        evidence — never from a caller-supplied boolean. This is the one
        call site that is allowed to transition a position out of
        OBLIGATION_ACTIVE, and it always goes through WorkDeliverableVerifier
        first.
        """
        verifier_receipt = self.verifier.verify(work_order, deliverable)
        success = verifier_receipt.status == DeliverableStatus.ACCEPTED

        position.record_verification(
            success=success,
            evidence_hash=verifier_receipt.evidence_hash,
        )

        if position.status == CollateralStatus.VERIFIED_SUCCESS:
            vault_receipt = self.vault.release(position_id=position.position_id)
        else:
            vault_receipt = self.vault.forfeit(
                position_id=position.position_id,
                beneficiary_address=beneficiary_wallet_address,
            )

        position.settle(settlement_tx_hash=vault_receipt.tx_hash)

        logger.info(
            "collateral position %s settled -> %s (%s, tx=%s)",
            position.position_id,
            position.status.value,
            "simulated" if vault_receipt.is_simulated else "on-chain",
            vault_receipt.tx_hash,
        )

        return CollateralSettlementOutcome(
            position=position,
            verifier_receipt=verifier_receipt,
            vault_tx_hash=vault_receipt.tx_hash,
            is_simulated=vault_receipt.is_simulated,
        )
