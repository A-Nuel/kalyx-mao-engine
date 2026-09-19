from src.agents.collateral_coordinator import CollateralCoordinator
from src.domain.collateral import CollateralStatus
from src.domain.enums import CurrencyAsset
from src.domain.work_order import WorkDeliverable, WorkOrder
from src.external.models import ExternalProviderMode
from src.external.orbio.collateral_adapter import CollateralVaultAdapter
from src.external.orbio.collateral_config import CollateralVaultConfig
from src.settlement.work_verifier import WorkDeliverableVerifier
import pytest

SECRET = "test-verifier-secret"


def _make_vault() -> CollateralVaultAdapter:
    return CollateralVaultAdapter(config=CollateralVaultConfig(
        mode=ExternalProviderMode.SIMULATED,
        rpc_url="",
        vault_address=None,
        credit_token_address=None,
        owner_private_key=None,
        pledger_private_key=None,
    ))


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


def _make_coordinator(authorized: bool = True) -> CollateralCoordinator:
    return CollateralCoordinator(
        vault=_make_vault(),
        verifier=WorkDeliverableVerifier(secret_key=SECRET),
        policy_authorization_validator=lambda *_args: authorized,
    )


def _lock(coordinator: CollateralCoordinator, order_id: str, amount: int = 20_000_000):
    return coordinator.propose_and_lock(
        tenant_id="tenant-a",
        organisation_id="org-a",
        obligation_reference=order_id,
        pledging_org_id="org-b",
        pledging_org_wallet_address="0xPledgerWallet",
        beneficiary_org_id="org-a",
        beneficiary_wallet_address="0xBeneficiaryWallet",
        amount_atoms=amount,
        policy_authorization_evidence_hash="policy-auth-hash",
    )


def test_full_success_path_locks_then_releases():
    coordinator = _make_coordinator()
    position = _lock(coordinator, "wo-1")
    assert position.status == CollateralStatus.OBLIGATION_ACTIVE

    outcome = coordinator.settle_from_verification(
        position=position,
        work_order=_make_work_order("wo-1"),
        deliverable=WorkDeliverable.create(
            work_order_id="wo-1",
            producer_agent_id="agent-b",
            content_payload={"deliverable_type": "text_summary", "summary": "the dataset shows X"},
            orbio_credits_consumed=3,
        ),
    )
    assert outcome.position.status == CollateralStatus.RELEASED
    assert outcome.is_simulated is True
    assert coordinator.vault.get_position(position_id=position.position_id)["state"] == "RELEASED"


def test_deliverable_bound_to_wrong_work_order_forfeits_collateral():
    coordinator = _make_coordinator()
    position = _lock(coordinator, "wo-2", 15_000_000)
    outcome = coordinator.settle_from_verification(
        position=position,
        work_order=_make_work_order("wo-2"),
        deliverable=WorkDeliverable.create(
            work_order_id="wo-999-wrong",
            producer_agent_id="agent-b",
            content_payload={"deliverable_type": "text_summary", "summary": "mismatched"},
            orbio_credits_consumed=2,
        ),
    )
    assert outcome.position.status == CollateralStatus.FORFEITED
    vault_state = coordinator.vault.get_position(position_id=position.position_id)
    assert vault_state["state"] == "FORFEITED"
    assert vault_state["beneficiary"] == "0xBeneficiaryWallet"


def test_settlement_cannot_be_driven_by_a_second_call():
    coordinator = _make_coordinator()
    position = _lock(coordinator, "wo-3", 8_000_000)
    work_order = _make_work_order("wo-3")
    deliverable = WorkDeliverable.create(
        work_order_id="wo-3",
        producer_agent_id="agent-b",
        content_payload={"deliverable_type": "text_summary", "summary": "fine"},
        orbio_credits_consumed=1,
    )
    coordinator.settle_from_verification(position=position, work_order=work_order, deliverable=deliverable)
    with pytest.raises(Exception):
        coordinator.settle_from_verification(position=position, work_order=work_order, deliverable=deliverable)


def test_collateral_lock_fails_closed_without_verified_policy_authorization():
    coordinator = _make_coordinator(authorized=False)
    with pytest.raises(PermissionError, match="policy authorization"):
        _lock(coordinator, "wo-denied")
