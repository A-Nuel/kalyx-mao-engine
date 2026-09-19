"""
Kalyx Credit Collateral — Phase 18 Demo

Demonstrates the collateral primitive Kalyx adds on top of Orbio's
transferable CREDIT: pledge -> authorize -> lock -> obligation -> verify
-> settle (release on success, forfeit on failure), settled exclusively
from WorkDeliverableVerifier's independent evidence.

Runs two scenarios back to back:
  1. SUCCESS  — a valid deliverable is verified ACCEPTED -> collateral RELEASED
  2. FAILURE  — a deliverable bound to the wrong work order is verified
               REJECTED -> collateral FORFEITED to the counterparty

Mode is controlled by KALYX_COLLATERAL_MODE (disabled|simulated|live), exactly
like KALYX_ORBIO_MODE controls the inference adapter. Defaults to simulated
so this runs with zero setup; every printed tx reference is explicitly
labeled [SIMULATED] or [ON-CHAIN] — never blurred.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.agents.collateral_coordinator import CollateralCoordinator
from src.domain.collateral import CollateralStatus
from src.domain.enums import CurrencyAsset
from src.domain.work_order import WorkDeliverable, WorkOrder
from src.external.orbio.collateral_adapter import build_collateral_vault_adapter
from src.external.orbio.collateral_config import CollateralVaultConfig
from src.external.models import ExternalProviderMode
from src.settlement.work_verifier import WorkDeliverableVerifier

console = Console()


def _mode_label(is_simulated: bool) -> str:
    return "[yellow][SIMULATED][/yellow]" if is_simulated else "[bold green][ON-CHAIN][/bold green]"


def _print_position(position, title: str) -> None:
    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")
    d = position.to_dict()
    for key in (
        "position_id",
        "obligation_reference",
        "pledging_org_id",
        "beneficiary_org_id",
        "amount",
        "asset",
        "status",
        "onchain_tx_hash",
        "settlement_tx_hash",
    ):
        table.add_row(key, str(d.get(key)))
    console.print(table)


def run_success_scenario(coordinator: CollateralCoordinator) -> None:
    console.print(Panel("[bold]Scenario 1 — verified success -> collateral RELEASED[/bold]", border_style="green"))

    position = coordinator.propose_and_lock(
        tenant_id="tenant-demo",
        organisation_id="org-client",
        obligation_reference="wo-demo-success",
        pledging_org_id="org-provider",
        pledging_org_wallet_address="0xProviderWallet",
        beneficiary_org_id="org-client",
        amount_atoms=20_000_000,  # 20 CREDIT at 6 decimals
        policy_authorization_evidence_hash="policy-auth-demo-1",
    )
    is_sim = position.onchain_tx_hash.startswith("sim-") if position.onchain_tx_hash else True
    console.print(f"  {_mode_label(is_sim)} Locked {position.amount / 1_000_000:.2f} CREDIT — tx {position.onchain_tx_hash}")

    work_order = WorkOrder(
        work_order_id="wo-demo-success",
        tenant_id="tenant-demo",
        organisation_id="org-client",
        client_id="org-client",
        title="Summarize quarterly metrics",
        description="Produce a structured summary of the provided dataset",
        deliverable_type="text_summary",
        required_orbio_credits=5,
        bounty_amount=100,
        bounty_asset=CurrencyAsset.USDG,
    )
    deliverable = WorkDeliverable.create(
        work_order_id="wo-demo-success",
        producer_agent_id="agent-provider-1",
        content_payload={"deliverable_type": "text_summary", "summary": "Revenue up 12% QoQ."},
        orbio_credits_consumed=3,
    )

    outcome = coordinator.settle_from_verification(
        position=position,
        work_order=work_order,
        deliverable=deliverable,
        beneficiary_wallet_address="0xClientWallet",
    )
    console.print(
        f"  Verifier status: [bold]{outcome.verifier_receipt.status.value}[/bold] "
        f"({outcome.verifier_receipt.verification_notes})"
    )
    console.print(
        f"  {_mode_label(outcome.is_simulated)} Settlement tx {outcome.vault_tx_hash} "
        f"-> position status [bold green]{outcome.position.status.value}[/bold green]"
    )
    _print_position(outcome.position, "Final position — Scenario 1")
    assert outcome.position.status == CollateralStatus.RELEASED


def run_failure_scenario(coordinator: CollateralCoordinator) -> None:
    console.print()
    console.print(Panel("[bold]Scenario 2 — verified failure -> collateral FORFEITED[/bold]", border_style="red"))

    position = coordinator.propose_and_lock(
        tenant_id="tenant-demo",
        organisation_id="org-client",
        obligation_reference="wo-demo-failure",
        pledging_org_id="org-provider",
        pledging_org_wallet_address="0xProviderWallet",
        beneficiary_org_id="org-client",
        amount_atoms=15_000_000,
        policy_authorization_evidence_hash="policy-auth-demo-2",
    )
    is_sim = position.onchain_tx_hash.startswith("sim-") if position.onchain_tx_hash else True
    console.print(f"  {_mode_label(is_sim)} Locked {position.amount / 1_000_000:.2f} CREDIT — tx {position.onchain_tx_hash}")

    work_order = WorkOrder(
        work_order_id="wo-demo-failure",
        tenant_id="tenant-demo",
        organisation_id="org-client",
        client_id="org-client",
        title="Summarize quarterly metrics",
        description="Produce a structured summary of the provided dataset",
        deliverable_type="text_summary",
        required_orbio_credits=5,
        bounty_amount=100,
        bounty_asset=CurrencyAsset.USDG,
    )
    # Deliverable claims the wrong work_order_id — WorkDeliverableVerifier's
    # own Check 1 rejects this. This is a real verifier decision, not a
    # scripted "pretend it failed" branch.
    bad_deliverable = WorkDeliverable.create(
        work_order_id="wo-does-not-match",
        producer_agent_id="agent-provider-1",
        content_payload={"deliverable_type": "text_summary", "summary": "wrong binding"},
        orbio_credits_consumed=2,
    )

    outcome = coordinator.settle_from_verification(
        position=position,
        work_order=work_order,
        deliverable=bad_deliverable,
        beneficiary_wallet_address="0xClientWallet",
    )
    console.print(
        f"  Verifier status: [bold red]{outcome.verifier_receipt.status.value}[/bold red] "
        f"({outcome.verifier_receipt.verification_notes})"
    )
    console.print(
        f"  {_mode_label(outcome.is_simulated)} Settlement tx {outcome.vault_tx_hash} "
        f"-> position status [bold red]{outcome.position.status.value}[/bold red]"
    )
    _print_position(outcome.position, "Final position — Scenario 2")
    assert outcome.position.status == CollateralStatus.FORFEITED


def main() -> None:
    banner = Text(
        "\n"
        "  ================================================================\n"
        "  |     KALYX PHASE 18 — CREDIT COLLATERAL PRIMITIVE DEMO         |\n"
        "  |  Orbio: transferable CREDIT.  Kalyx: governed collateral.     |\n"
        "  ================================================================\n",
        style="bold bright_blue",
    )
    console.print(banner)

    config = CollateralVaultConfig.from_env()
    if config.mode == ExternalProviderMode.DISABLED:
        console.print("[yellow]KALYX_COLLATERAL_MODE unset — defaulting to SIMULATED for this demo run.[/yellow]")
        config = CollateralVaultConfig(
            mode=ExternalProviderMode.SIMULATED,
            rpc_url="",
            vault_address=None,
            credit_token_address=None,
            signer_private_key=None,
        )
    vault = build_collateral_vault_adapter(config)
    assert vault is not None

    console.print(f"Collateral vault mode: [bold]{config.mode.value.upper()}[/bold]")
    if config.mode == ExternalProviderMode.LIVE:
        console.print(f"  Vault contract: {config.vault_address}")
        console.print(f"  CREDIT token:   {config.credit_token_address}")
    console.print()

    verifier = WorkDeliverableVerifier(secret_key=os.getenv("KALYX_RECEIPT_SECRET_KEY", "demo-secret-key"))
    coordinator = CollateralCoordinator(vault=vault, verifier=verifier)

    run_success_scenario(coordinator)
    run_failure_scenario(coordinator)

    console.print()
    console.print(
        Panel(
            "[bold green]Both scenarios complete.[/bold green] Settlement in both cases was driven "
            "exclusively by WorkDeliverableVerifier's independent evidence — never by a "
            "self-reported outcome from the executing agent.",
            border_style="green",
        )
    )


if __name__ == "__main__":
    main()
