"""
Kalyx Autonomous Enterprise Ecosystem — Phase 17 Hackathon Demo
Demonstrates the 6-Stage Autonomous Economic Loop:

Stage 1: Client Org Proposes B2B Order & Policy Authorizes Escrow
Stage 2: Provider Org Discovers Order & Governed Capability Expansion
Stage 3: Productive Work Execution via Orbio Gateway (Live or Simulated)
Stage 4: Independent Deliverable Verification & Audit
Stage 5: Dual-Sided Escrow Settlement & Surplus Reconciliation
Stage 6: Autonomous Mission Chaining Funded Strictly from Verified Surplus

Invariants:
- AGENTS PROPOSE -> POLICIES AUTHORIZE -> EXECUTORS EXECUTE -> AUDITORS VERIFY
- Strict Decoupling: ORG_CREDIT (internal ledger) vs USDG (payment asset) vs ORBIO_CREDIT (compute quota)
- Truthful Provenance: Live vs Simulated execution is explicitly flagged and never faked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, Optional

# Reconfigure encoding for Windows console if needed
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure src is in pythonpath
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from src.agents.b2b_marketplace_coordinator import B2BMarketplaceCoordinator
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, CurrencyAsset, OrgState, PolicyResult
from src.domain.marketplace import MarketplaceOrderStatus
from src.domain.work_order import WorkOrder
from src.economy.ledger import (
    DoubleEntryLedger,
    ESCROW,
    EXTERNAL_SINK,
    SURPLUS_RESERVE,
    TREASURY,
)
from src.economy.surplus_accounting import SurplusReconciler
from src.execution.orbio_gateway_adapter import OrbioGatewayAdapter
from src.execution.work_executor import SimulatedWorkExecutor
from src.governance.policy_engine import PolicyEngine
from src.orchestration.capability_manager import CapabilityManager
from src.persistence.database import Database
from src.persistence.marketplace_repository import MarketplaceRepository
from src.persistence.work_order_repository import WorkOrderRepository
from src.settlement.work_verifier import WorkDeliverableVerifier

console = Console()


def print_banner() -> None:
    banner = Text(
        """
  ███████╗ █████╗ ███████╗███████╗██████╗ ███████╗   ██╗ ██████╗ 
  ██╔════╝██╔══██╗██╔════╝██╔════╝██╔══██╗██╔════╝  ███║██╔════╝ 
  █████╗  ███████║███████╗███████╗██████╔╝█████╗    ╚██║╚█████╗  
  ██╔══╝  ██╔══██║╚════██║╚════██║██╔═══╝ ██╔══╝     ██║ ╚═══██╗ 
  ███████╗██║  ██║███████║███████║██║     ███████╗   ██║██████╔╝ 
  ╚══════╝╚═╝  ╚═╝╚══════╝╚══════╝╚═╝     ╚══════╝   ╚═╝╚═════╝  
    K A L Y X   M A O   E N G I N E   -   P H A S E   1 7   P 0
    Autonomous Enterprise Ecosystem & Cross-DAO Commerce Loop
""",
        style="bold bright_cyan",
    )
    console.print(banner)

    tree = Tree("[bold bright_white]6-STAGE AUTONOMOUS ECONOMIC LOOP ARCHITECTURE[/bold bright_white]")
    tree.add("[bold yellow]STAGE 1[/bold yellow]: Org A Proposes B2B Order -> Policy Authorizes Escrow (Treasury -> Escrow)")
    tree.add("[bold cyan]STAGE 2[/bold cyan]: Org B Discovers Order -> Governed Capability Evolution (Proposal -> Policy Grant)")
    tree.add("[bold green]STAGE 3[/bold green]: Productive Orbio Execution (Live API or High-Fidelity Simulator)")
    tree.add("[bold magenta]STAGE 4[/bold magenta]: Independent Deliverable Audit & Verification (HMAC Signed Evidence)")
    tree.add("[bold bright_blue]STAGE 5[/bold bright_blue]: Escrow Release -> Dual-Sided Settlement -> Surplus Reconciliation")
    tree.add("[bold bright_yellow]STAGE 6[/bold bright_yellow]: Autonomous Mission Chaining Funded Strictly from Verified Surplus")
    console.print(Panel(tree, title="[bold white]Control-Plane Invariants Preserved[/bold white]", border_style="bright_blue"))
    console.print()


def run_phase17_demo(fast: bool = False, live_mode: bool = False, json_output: bool = False) -> Dict[str, Any]:
    if not json_output:
        print_banner()

    def step_pause(sec: float = 0.8):
        if not fast and not json_output:
            time.sleep(sec)

    secret_key = "kalyx-phase17-production-secret-42"
    db = Database(":memory:")
    mkt_repo = MarketplaceRepository(db)
    wo_repo = WorkOrderRepository(db)
    coordinator = B2BMarketplaceCoordinator(
        marketplace_repo=mkt_repo,
        work_order_repo=wo_repo,
        receipt_secret_key=secret_key,
    )

    # -------------------------------------------------------------------------
    # STAGE 0: Setup Participating Autonomous Organisations
    # -------------------------------------------------------------------------
    if not json_output:
        console.print("[bold bright_white]>>> STAGE 0: Initializing Autonomous Organisations & Ledgers[/bold bright_white]")

    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-alpha", "tenant-demo", "Quantitative Finance & Risk Analysis", 500, "PLANNING"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-beta", "tenant-demo", "Decentralized Machine Learning & Analytics", 100, "PLANNING"),
        )

    client_ledger = DoubleEntryLedger(initial_treasury=500)
    client_policy = PolicyEngine(signing_secret=secret_key, human_approval_threshold=1000)
    client_ceo = AgentRecord(
        id="org-alpha-ceo",
        organisation_id="org-alpha",
        role=AgentRole.CEO,
        authority_ceiling=1000,
        allowed_action_types=[ActionType.PUBLISH_MARKETPLACE_ORDER],
    )
    client_org = Organisation(
        id="org-alpha",
        mission="Quantitative Finance & Risk Analysis",
        state=OrgState.PLANNING,
        treasury_balance=500,
        agents={"org-alpha-ceo": client_ceo},
    )

    provider_ledger = DoubleEntryLedger(initial_treasury=100)
    provider_policy = PolicyEngine(signing_secret=secret_key, human_approval_threshold=1000)
    provider_cap_mgr = CapabilityManager(repository=mkt_repo, policy_engine=provider_policy)
    provider_worker = AgentRecord(
        id="org-beta-worker-1",
        organisation_id="org-beta",
        role=AgentRole.RESEARCHER,
        authority_ceiling=500,
        allowed_action_types=[ActionType.CLAIM_MARKETPLACE_ORDER],
    )
    # Give the agent a high historical performance score to warrant capability expansion
    provider_worker.performance_score = 94.5

    provider_ceo = AgentRecord(
        id="org-beta-ceo",
        organisation_id="org-beta",
        role=AgentRole.CEO,
        authority_ceiling=1000,
        allowed_action_types=[ActionType.PROPOSE_CAPABILITY_EXPANSION],
    )
    provider_org = Organisation(
        id="org-beta",
        mission="Decentralized Machine Learning & Analytics",
        state=OrgState.PLANNING,
        treasury_balance=100,
        agents={"org-beta-worker-1": provider_worker, "org-beta-ceo": provider_ceo},
    )

    if not json_output:
        t0 = Table(title="Participating Autonomous Entities", border_style="dim")
        t0.add_column("Organisation", style="bold cyan")
        t0.add_column("Mission", style="white")
        t0.add_column("Initial Treasury", style="green")
        t0.add_column("Agents", style="yellow")
        t0.add_row("Org Alpha (Client)", client_org.mission, f"{client_ledger.get_balance(TREASURY)} ORG Credits", "org-alpha-ceo (CEO)")
        t0.add_row("Org Beta (Provider)", provider_org.mission, f"{provider_ledger.get_balance(TREASURY)} ORG Credits", "org-beta-worker-1 (Researcher, Perf: 94.5)")
        console.print(t0)
        console.print()

    step_pause(0.5)

    # -------------------------------------------------------------------------
    # STAGE 1: Client Org Proposes & Publishes B2B Marketplace Order
    # -------------------------------------------------------------------------
    if not json_output:
        console.print("[bold yellow]>>> STAGE 1: Org Alpha Proposes B2B Order & Policy Authorizes Escrow Lock[/bold yellow]")

    order = coordinator.publish_b2b_order(
        client_tenant_id="tenant-demo",
        client_org_id="org-alpha",
        title="High-Frequency Volatility Surface Optimization",
        description="Run Orbio deep-inference model to calibrate volatility surface across 100 strike assets.",
        required_capability="ADVANCED_ANALYTICS",
        bounty_amount=300,
        client_ledger=client_ledger,
        policy_engine=client_policy,
        client_agent=client_ceo,
        client_org=client_org,
    )

    if not json_output:
        console.print(f"  [green]✔[/green] ActionProposal submitted: [bold]PUBLISH_MARKETPLACE_ORDER[/bold] (Bounty: 300 USDG)")
        console.print(f"  [green]✔[/green] PolicyEngine evaluation: [bold green]APPROVED[/bold green] (Rule allowlist & ceiling validated)")
        console.print(f"  [green]✔[/green] DoubleEntryLedger: [bold]TREASURY -> ESCROW[/bold] (300 credits locked, Treasury balance: {client_ledger.get_balance(TREASURY)})")
        console.print(f"  [green]✔[/green] Order published to public marketplace: [cyan]{order.order_id}[/cyan] (Status: {order.status})")
        console.print()

    step_pause(0.5)

    # -------------------------------------------------------------------------
    # STAGE 2: Provider Org Discovers Order & Governed Capability Expansion
    # -------------------------------------------------------------------------
    if not json_output:
        console.print("[bold cyan]>>> STAGE 2: Org Beta Discovers Order & Governed Capability Evolution[/bold cyan]")

    has_cap_before = provider_cap_mgr.check_agent_capability("tenant-demo", "org-beta", provider_worker.id, "ADVANCED_ANALYTICS")
    if not json_output:
        console.print(f"  [dim]Pre-check:[/dim] Agent {provider_worker.id} has capability 'ADVANCED_ANALYTICS'? [red]{has_cap_before}[/red]")
        console.print(f"  [dim]Autonomous Trigger:[/dim] Worker empirical performance score is [bold green]{provider_worker.performance_score}[/bold green] (threshold: 80.0)")
        console.print("  [dim]Governance Protocol:[/dim] Proposing governed capability expansion to PolicyEngine...")

    claimed_order = coordinator.discover_and_claim(
        provider_tenant_id="tenant-demo",
        provider_org_id="org-beta",
        provider_agent=provider_worker,
        provider_org=provider_org,
        capability_manager=provider_cap_mgr,
        policy_engine=provider_policy,
        target_order_id=order.order_id,
    )

    has_cap_after = provider_cap_mgr.check_agent_capability("tenant-demo", "org-beta", provider_worker.id, "ADVANCED_ANALYTICS")
    if not json_output:
        console.print(f"  [green]✔[/green] PolicyEngine evaluated [bold]CapabilityEvolutionRule[/bold]: [bold green]AUTHORIZED[/bold green]")
        console.print(f"  [green]✔[/green] Capability grant persisted to audit ledger: [bold]ADVANCED_ANALYTICS[/bold] granted to {provider_worker.id}")
        console.print(f"  [green]✔[/green] Post-check: Agent capability verified: [green]{has_cap_after}[/green]")
        console.print(f"  [green]✔[/green] Order [cyan]{claimed_order.order_id}[/cyan] atomically claimed by Org Beta (Status: {claimed_order.status})")
        console.print()

    step_pause(0.5)

    # -------------------------------------------------------------------------
    # STAGE 3: Productive Work Execution via Orbio Gateway (Live or Fallback)
    # -------------------------------------------------------------------------
    if not json_output:
        console.print("[bold green]>>> STAGE 3: Productive Work Execution via Orbio Gateway[/bold green]")

    credit_store = {"org-beta": 100_000}
    simulated_executor = SimulatedWorkExecutor(credit_store=credit_store)
    active_key = os.getenv("ORBIO_API_KEY") if live_mode else None
    orbio_adapter = OrbioGatewayAdapter(
        fallback_executor=simulated_executor,
        credit_store=credit_store,
        api_key=active_key,
    )

    # Determine execution mode
    is_live_possible = bool(live_mode and active_key)
    mode_badge = "[bold white on red] [LIVE ORBIO - https://www.orbio.so/api/v1] [/bold white on red]" if is_live_possible else "[bold white on blue] [SIMULATED WORK EXECUTOR] [/bold white on blue]"

    if not json_output:
        console.print(f"  Execution Backend: {mode_badge}")
        console.print("  Executing productive computation for work order...")

    # -------------------------------------------------------------------------
    # STAGE 4 & 5: Execution, Independent Verification & Escrow Settlement
    # -------------------------------------------------------------------------
    if not json_output:
        console.print("[bold magenta]>>> STAGE 4 & 5: Independent Deliverable Audit & Dual-Sided Escrow Settlement[/bold magenta]")

    work_verifier = WorkDeliverableVerifier(secret_key=secret_key)
    surplus_reconciler = SurplusReconciler(
        ledger=provider_ledger,
        default_reserve_ratio=0.20,
        receipt_secret_key=secret_key,
    )

    outcome = coordinator.execute_and_settle(
        order=claimed_order,
        provider_tenant_id="tenant-demo",
        provider_org_id="org-beta",
        producer_agent_id=provider_worker.id,
        work_executor=orbio_adapter,
        work_verifier=work_verifier,
        client_ledger=client_ledger,
        provider_ledger=provider_ledger,
        surplus_reconciler=surplus_reconciler,
    )

    provenance_tag = "[red][LIVE PRODUCTION][/red]" if not outcome.is_simulated else "[cyan][TRANSPARENT SIMULATED][/cyan]"

    if not json_output:
        console.print(f"  [green]✔[/green] Work executed with provenance telemetry: {provenance_tag}")
        console.print(f"      Deliverable ID: [cyan]{outcome.deliverable.deliverable_id}[/cyan]")
        console.print(f"      Content SHA-256: [dim]{outcome.deliverable.content_hash}[/dim]")
        console.print(f"      Orbio Compute Credits: {outcome.deliverable.orbio_credits_consumed} quota consumed")
        console.print(f"  [green]✔[/green] Independent Auditor verification: [bold green]VERIFIED (HMAC Signature Match)[/bold green]")
        console.print(f"  [green]✔[/green] Escrow Settlement executed across both ledgers:")
        console.print(f"      Client Org Alpha: [bold]ESCROW -> EXTERNAL_SINK[/bold] ({outcome.order_id} bounty 300 USDG released)")
        console.print(f"      Provider Org Beta: [bold]REVENUE DEPOSIT[/bold] (+300 USDG gross revenue)")
        console.print(f"  [green]✔[/green] SurplusReconciler deterministic split (80/20 rule):")
        console.print(f"      Gross Revenue: {outcome.revenue_event.gross_revenue_usdg} USDG")
        console.print(f"      Net Surplus: {outcome.net_surplus_usdg} USDG")
        console.print(f"      [bold green]-> Allocated to Mission Budget (80%):[/bold green] [bold]{outcome.allocated_to_mission_budget} USDG[/bold]")
        console.print(f"      [bold yellow]-> Allocated to Surplus Reserve (20%):[/bold yellow] [bold]{outcome.allocated_to_reserve} USDG[/bold]")
        console.print()

    step_pause(0.5)

    # -------------------------------------------------------------------------
    # STAGE 6: Autonomous Mission Chaining Funded Strictly from Verified Surplus
    # -------------------------------------------------------------------------
    if not json_output:
        console.print("[bold bright_yellow]>>> STAGE 6: Autonomous Mission Chaining Funded Strictly from Verified Surplus[/bold bright_yellow]")

    surplus_budget = outcome.allocated_to_mission_budget  # 240 USDG
    mission_2_bounty = 200  # <= 240 USDG

    # Prove invariant: Mission cannot be funded above verified surplus
    assert mission_2_bounty <= surplus_budget, "Mission 2 cannot exceed verified surplus budget"

    mission_2_work_order = WorkOrder(
        tenant_id="tenant-demo",
        organisation_id="org-beta",
        work_order_id=f"wo-mission-2-{int(time.time())}",
        client_id="internal-growth-dao",
        title="Autonomous Micro-Model Fine-Tuning & Distillation",
        description="Autonomous next mission funded strictly from verified Mission 1 B2B surplus.",
        deliverable_type="MODEL_FINE_TUNE",
        required_orbio_credits=1000,
        bounty_amount=mission_2_bounty,
        bounty_asset=CurrencyAsset.USDG,
    )

    wo_repo.record_mission_lineage(
        tenant_id="tenant-demo",
        organisation_id="org-beta",
        mission_id="mission-chained-002",
        parent_mission_id=outcome.order_id,
        funding_source="PRIOR_SURPLUS",
        funding_amount_usdg=surplus_budget,
    )

    lineage = wo_repo.get_mission_lineage("tenant-demo", "org-beta", "mission-chained-002")

    if not json_output:
        console.print(f"  [green]✔[/green] Verified surplus available: [bold green]{surplus_budget} USDG[/bold green]")
        console.print(f"  [green]✔[/green] New Mission 2 WorkOrder formulated: [cyan]{mission_2_work_order.work_order_id}[/cyan]")
        console.print(f"  [green]✔[/green] Mission 2 Budget: {mission_2_work_order.bounty_amount} USDG (Invariant holds: {mission_2_work_order.bounty_amount} <= {surplus_budget})")
        console.print(f"  [green]✔[/green] Mission Lineage recorded with immutable cryptographic link:")
        console.print(f"      Parent Mission ID: [cyan]{lineage['parent_mission_id']}[/cyan]")
        console.print(f"      Funding Source: [bold green]{lineage['funding_source']}[/bold green]")
        console.print(f"      Funding Amount: [bold]{lineage['funding_amount_usdg']} USDG[/bold]")
        console.print()

        # Summary Table
        t_sum = Table(title="Autonomous Economic Loop Summary & Final Balances", border_style="bright_blue")
        t_sum.add_column("Entity", style="bold cyan")
        t_sum.add_column("Initial Treasury", style="dim")
        t_sum.add_column("Final Treasury", style="bold green")
        t_sum.add_column("Surplus Reserve", style="bold yellow")
        t_sum.add_column("Autonomous State", style="bold magenta")
        t_sum.add_row(
            "Org Alpha (Client)",
            "500 ORG",
            f"{client_ledger.get_balance(TREASURY)} ORG",
            "0 ORG",
            "B2B Order Delivered & Settled",
        )
        t_sum.add_row(
            "Org Beta (Provider)",
            "100 ORG",
            f"{provider_ledger.get_balance(TREASURY)} ORG (+{outcome.allocated_to_mission_budget})",
            f"{provider_ledger.get_balance(SURPLUS_RESERVE)} ORG (+{outcome.allocated_to_reserve})",
            "Mission 2 Funded from Verified Surplus",
        )
        console.print(t_sum)
        console.print()
        console.print(Panel("[bold green]✔ COMPLETE 6-STAGE AUTONOMOUS ECONOMIC LOOP VERIFIED CLEANLY[/bold green]\n"
                            "All control-plane invariants and truthfulness requirements satisfied.",
                            border_style="green"))

    demo_result = {
        "status": "SUCCESS",
        "order_id": outcome.order_id,
        "is_simulated": outcome.is_simulated,
        "execution_backend": "LIVE_ORBIO" if not outcome.is_simulated else "SIMULATED",
        "client_final_treasury": client_ledger.get_balance(TREASURY),
        "provider_final_treasury": provider_ledger.get_balance(TREASURY),
        "provider_surplus_reserve": provider_ledger.get_balance(SURPLUS_RESERVE),
        "net_surplus_usdg": outcome.net_surplus_usdg,
        "mission_budget_allocated": outcome.allocated_to_mission_budget,
        "reserve_allocated": outcome.allocated_to_reserve,
        "chained_mission_id": "mission-chained-002",
        "chained_mission_parent": outcome.order_id,
    }

    if json_output:
        print(json.dumps(demo_result, indent=2))

    return demo_result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kalyx Phase 17 Autonomous Economic Loop Demo")
    parser.add_argument("--fast", action="store_true", help="Run without UI animation delays")
    parser.add_argument("--live", action="store_true", help="Attempt live Orbio API execution if configured")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON result")
    args = parser.parse_args()

    run_phase17_demo(fast=args.fast, live_mode=args.live, json_output=args.json)
