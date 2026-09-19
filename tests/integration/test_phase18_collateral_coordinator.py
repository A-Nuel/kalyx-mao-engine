from src.agents.collateral_coordinator import CollateralCoordinator
from src.domain.collateral import CollateralStatus
from src.domain.enums import CurrencyAsset
from src.domain.work_order import WorkDeliverable, WorkOrder
from src.external.models import ExternalProviderMode
from src.external.orbio.collateral_adapter import CollateralVaultAdapter
from src.external.orbio.collateral_config import CollateralVaultConfig
from src.settlement.work_verifier import WorkDeliverableVerifier

SECRET = "test-verifier-secret"


def _make_vault() -> CollateralVaultAdapter:
    cfg = CollateralVaultConfig(
        mode=ExternalProviderMode.SIMULATED,
        rpc_url="",
        vault_address=None,
        credit_token_address=None,
        signer_private_key=None,
    )
    return CollateralVaultAdapter(config=cfg)


def _make_work_order(work_order_id: str) -> WorkOrder:
    return WorkOrder(
        work_order_id=work_order_id,
        tenant_id="tenant-a",
        organisation_id="org-a",
        client_id="org-a",
        title="Summarize dataset",
        description="Produce a summary of the provided dataset",
        deliverable_type="text_summary",
        required_orbio_credits=5,
        bounty_amount=100,
        bounty_asset=CurrencyAsset.USDG,
    )


def _make_coordinator() -> CollateralCoordinator:
    vault = _make_vault()
    verifier = WorkDeliverableVerifier(secret_key=SECRET)
    return CollateralCoordinator(vault=vault, verifier=verifier)


def test_full_success_path_locks_then_releases():
    coordinator = _make_coordinator()

    position = coordinator.propose_and_lock(
        tenant_id="tenant-a",
        organisation_id="org-a",
        obligation_reference="wo-1",
        pledging_org_id="org-b",
        pledging_org_wallet_address="0xPledgerWallet",
        beneficiary_org_id="org-a",
        amount_atoms=20_000_000,
        policy_authorization_evidence_hash="policy-auth-hash-1",
    )
    assert position.status == CollateralStatus.OBLIGATION_ACTIVE
    assert position.onchain_tx_hash is not None

    work_order = _make_work_order("wo-1")
    deliverable = WorkDeliverable.create(
        work_order_id="wo-1",
        producer_agent_id="agent-b",
        content_payload={"deliverable_type": "text_summary", "summary": "the dataset shows X"},
        orbio_credits_consumed=3,
    )

    outcome = coordinator.settle_from_verification(
        position=position,
        work_order=work_order,
        deliverable=deliverable,
        beneficiary_wallet_address="0xBeneficiaryWallet",
    )

    assert outcome.position.status == CollateralStatus.RELEASED
    assert outcome.is_simulated is True
    assert outcome.vault_tx_hash is not None

    # The vault itself must independently agree the position is released —
    # not just the Python dataclass's own field.
    vault_state = coordinator.vault.get_position(position_id=position.position_id)
    assert vault_state["state"] == "RELEASED"


def test_deliverable_bound_to_wrong_work_order_forfeits_collateral():
    """This exercises WorkDeliverableVerifier's own Check 1 (work order ID
    binding) and confirms the resulting REJECTED receipt correctly drives
    the collateral position to FORFEITED — proving settlement really does
    flow from the independent verifier's evidence, not a hopeful default."""
    coordinator = _make_coordinator()

    position = coordinator.propose_and_lock(
        tenant_id="tenant-a",
        organisation_id="org-a",
        obligation_reference="wo-2",
        pledging_org_id="org-b",
        pledging_org_wallet_address="0xPledgerWallet",
        beneficiary_org_id="org-a",
        amount_atoms=15_000_000,
        policy_authorization_evidence_hash="policy-auth-hash-2",
    )

    work_order = _make_work_order("wo-2")
    # Deliverable claims a DIFFERENT work_order_id than the one it's checked
    # against — WorkDeliverableVerifier must reject this.
    mismatched_deliverable = WorkDeliverable.create(
        work_order_id="wo-999-wrong",
        producer_agent_id="agent-b",
        content_payload={"deliverable_type": "text_summary", "summary": "mismatched"},
        orbio_credits_consumed=2,
    )

    outcome = coordinator.settle_from_verification(
        position=position,
        work_order=work_order,
        deliverable=mismatched_deliverable,
        beneficiary_wallet_address="0xBeneficiaryWallet",
    )

    assert outcome.position.status == CollateralStatus.FORFEITED
    vault_state = coordinator.vault.get_position(position_id=position.position_id)
    assert vault_state["state"] == "FORFEITED"
    assert vault_state["beneficiary"] == "0xBeneficiaryWallet"


def test_settlement_cannot_be_driven_by_a_second_call():
    """Once settled, calling settle_from_verification again for the same
    position must fail loudly (via the underlying state machine/vault guard)
    rather than silently re-paying or re-forfeiting."""
    coordinator = _make_coordinator()
    position = coordinator.propose_and_lock(
        tenant_id="tenant-a",
        organisation_id="org-a",
        obligation_reference="wo-3",
        pledging_org_id="org-b",
        pledging_org_wallet_address="0xPledgerWallet",
        beneficiary_org_id="org-a",
        amount_atoms=8_000_000,
        policy_authorization_evidence_hash="policy-auth-hash-3",
    )
    work_order = _make_work_order("wo-3")
    deliverable = WorkDeliverable.create(
        work_order_id="wo-3",
        producer_agent_id="agent-b",
        content_payload={"deliverable_type": "text_summary", "summary": "fine"},
        orbio_credits_consumed=1,
    )
    coordinator.settle_from_verification(
        position=position,
        work_order=work_order,
        deliverable=deliverable,
        beneficiary_wallet_address="0xBeneficiaryWallet",
    )
    assert position.status == CollateralStatus.RELEASED

    import pytest
    from src.domain.collateral import CollateralTransitionError

    with pytest.raises(CollateralTransitionError):
        coordinator.settle_from_verification(
            position=position,
            work_order=work_order,
            deliverable=deliverable,
            beneficiary_wallet_address="0xBeneficiaryWallet",
        )
