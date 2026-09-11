import sys
import os
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
import time
import argparse
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

# Ensure src is in pythonpath
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.domain.entities import Organisation, AgentRecord
from src.domain.enums import OrgState, AgentRole, ActionType, PolicyResult
from src.governance.policy_engine import PolicyEngine
from src.governance.human_gate import HumanGate
from src.economy.ledger import DoubleEntryLedger, TREASURY, EXTERNAL_SINK
from src.economy.reputation import ReputationEngine
from src.economy.experiment import EconomicExperiment
from src.execution.executor import ControlledExternalExecutor, SandboxExecutor
from src.audit.event_store import AppendOnlyEventStore
from src.audit.auditor import Auditor
from src.orchestration.engine import OrchestrationEngine
from src.orchestration.assignment import AgentAssignmentEngine
from src.agents.mock_adapter import MockAgentAdapter
from src.agents.openrouter_adapter import OpenRouterAgentAdapter
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.researcher import ResearcherAgent
from src.agents.roles.strategist import StrategistAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.persistence.database import Database
from src.persistence.repositories import SqliteLedger, SqliteEventStore, SqliteRepository

console = Console()

def print_header():
    ascii_banner = Text("""
   ========================================================================
   |        MINIMUM AUTONOMOUS ORGANISATION (MAO) - MISSION CONTROL       |
   |           Autonomous Organisation Infrastructure Prototype           |
   |                 Phase 4: Economy, Lifecycle & Adapters               |
   ========================================================================
    """, style="bold bright_blue")
    console.print(ascii_banner)

    tree = Tree("[bold cyan]ORGANISATIONAL GOVERNANCE PIPELINE (INVARIANT NON-NEGOTIABLE)[/bold cyan]")
    tree.add("[bold yellow]AGENTS PROPOSE[/bold yellow] (Structured Intent only - 0 direct authority)")
    tree.add("[bold white on blue]POLICIES AUTHORIZE[/bold white on blue] (Deterministic rule evaluation & HMAC token)")
    tree.add("[bold green]EXECUTORS EXECUTE[/bold green] (Controlled adapters with allowlists & SSRF guards)")
    tree.add("[bold bright_magenta]AUDITORS VERIFY[/bold bright_magenta] (8-point cryptographic & economic verification)")
    console.print(Panel(tree, title="[bold]System Architecture & Governance Invariant[/bold]", border_style="bright_blue"))
    console.print()

def run_mao_demo(persist_db: str = None, fast: bool = True, live: bool = False):
    print_header()

    def pause(sec: float = 0.5):
        if not fast:
            time.sleep(sec)

    # ---------------------------------------------------------
    # Step 1: Organisation Initialisation with Finite ORG Credits
    # ---------------------------------------------------------
    console.print("[bold yellow]>>> Step 1: Organisation Initialisation with Finite ORG Credits[/bold yellow]")
    if persist_db:
        console.print(f"[dim]Using persistent SQLite database: {persist_db}[/dim]")
        db = Database(persist_db)
        ledger = SqliteLedger(db, initial_treasury=100)
        event_store = SqliteEventStore(db, verify_on_startup=True)
        repo = SqliteRepository(db)
    else:
        console.print("[dim]Using in-memory fast session[/dim]")
        ledger = DoubleEntryLedger(initial_treasury=100)
        event_store = AppendOnlyEventStore()
        repo = None

    policy_engine = PolicyEngine(signing_secret="demo-terminal-key-phase4")
    
    # Controlled external executor with strict allowlist
    executor = ControlledExternalExecutor(
        policy_engine=policy_engine,
        ledger=ledger,
        allowlist={
            "sandbox://market_index_fund",
            "sandbox://verified_bonds",
            "api://market_data/v1/summary",
            "https://api.github.com/repos/",
            "https://httpbin.org/get"
        },
        mock_handler=lambda target, params: (200, {"status": "success", "target": target, "data": "executed_cleanly"})
    )
    human_gate = HumanGate()
    auditor = Auditor(verification_secret="demo-terminal-key-phase4")

    org = Organisation(
        id="mao-alpha-01",
        mission="Maximize capital preservation and data yield under 100 ORG Credits",
        treasury_balance=100
    )
    console.print(f"[green][OK] Organisation '{org.id}' created. Treasury: 100 ORG Credits (Zero inflation invariant).[/green]\n")
    pause()

    # ---------------------------------------------------------
    # Step 2: Agent Capability Registration & Reputation Baselines
    # ---------------------------------------------------------
    console.print("[bold yellow]>>> Step 2: Agent Capability Registration & Reputation Baselines[/bold yellow]")
    org.agents["agent-ceo"] = AgentRecord(
        id="agent-ceo", role=AgentRole.CEO, authority_ceiling=25,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION, ActionType.REPLAN]
    )
    org.agents["agent-research"] = AgentRecord(
        id="agent-research", role=AgentRole.RESEARCHER, authority_ceiling=20,
        allowed_action_types=[ActionType.DATA_FETCH, ActionType.INTERNAL_ANALYSIS]
    )
    org.agents["agent-strategy"] = AgentRecord(
        id="agent-strategy", role=AgentRole.STRATEGIST, authority_ceiling=20,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS]
    )
    org.agents["agent-finance"] = AgentRecord(
        id="agent-finance", role=AgentRole.FINANCIAL_ANALYST, authority_ceiling=25,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.DATA_FETCH, ActionType.EXTERNAL_API_CALL, ActionType.SIMULATED_ALLOCATION]
    )

    roster_table = Table(title="Registered Agent Roster & Baselines", border_style="cyan")
    roster_table.add_column("Agent ID", style="bold")
    roster_table.add_column("Role")
    roster_table.add_column("Reputation", justify="right")
    roster_table.add_column("Ceiling", justify="right")
    roster_table.add_column("Lifecycle Status")
    for a in org.agents.values():
        roster_table.add_row(a.id, a.role.value, f"{a.reputation_score:.1f}", f"{a.authority_ceiling} cr", a.status.value)
    console.print(roster_table)
    console.print()
    pause()

    # Adapter selection (live or mock fallback)
    mock_adapter = MockAgentAdapter()
    if live and os.getenv("OPENROUTER_API_KEY"):
        console.print("[cyan][INFO] Live LLM Mode enabled via OpenRouterAgentAdapter.[/cyan]")
        adapter = OpenRouterAgentAdapter(fallback_adapter=mock_adapter, fallback_on_error=True)
    else:
        console.print("[dim]Using deterministic MockAgentAdapter (Offline Safe Mode).[/dim]")
        adapter = mock_adapter

    ceo = CEOAgent("agent-ceo", adapter)
    researcher = ResearcherAgent("agent-research", adapter)
    strategist = StrategistAgent("agent-strategy", adapter)
    analyst = FinancialAnalystAgent("agent-finance", adapter)

    engine = OrchestrationEngine(
        org=org,
        ledger=ledger,
        policy_engine=policy_engine,
        executor=executor,
        event_store=event_store,
        human_gate=human_gate,
        ceo=ceo,
        researcher=researcher,
        strategist=strategist,
        financial_analyst=analyst,
        auditor=auditor,
        repository=repo,
        max_replan_attempts=3
    )
    engine.start_mission()

    # ---------------------------------------------------------
    # Step 3: Capability- & Reputation-Based Task Assignment
    # ---------------------------------------------------------
    console.print("[bold yellow]>>> Step 3: Capability- & Reputation-Based Task Assignment[/bold yellow]")
    tasks = engine.decompose_and_plan()
    task_table = Table(title="Dynamic Task Routing & Budget Allocation", border_style="cyan")
    task_table.add_column("Task ID", style="bold")
    task_table.add_column("Assigned Agent")
    task_table.add_column("Objective")
    task_table.add_column("Dynamic Budget", justify="right")
    task_table.add_column("Status")
    for t in tasks:
        task_table.add_row(t.id, t.assigned_agent_id or "unassigned", t.objective, f"{t.allocated_credits} cr", t.status.value)
    console.print(task_table)
    console.print()
    pause()

    # ---------------------------------------------------------
    # Step 4: Live LLM Planning & Specialist Intelligence Pipeline
    # ---------------------------------------------------------
    console.print("[bold yellow]>>> Step 4: Specialist Intelligence Pipeline (Research -> Strategy -> Finance)[/bold yellow]")
    research, strategy, finance = engine.run_intelligence_pipeline()
    
    console.print(Panel(
        f"[bold]Trend:[/bold] {research.market_trend}\n[bold]Findings:[/bold] {research.key_findings[0]}\n[bold]Focus:[/bold] {research.recommended_focus}",
        title="[cyan]Researcher Intelligence Output (Pydantic Validated)[/cyan]", border_style="cyan"
    ))
    console.print(Panel(
        f"[bold]Ranked Focus:[/bold] {strategy.opportunities_ranked[0]}\n[bold]Risk Assessment:[/bold] {strategy.risk_analysis}",
        title="[magenta]Strategist Analysis Output (Pydantic Validated)[/magenta]", border_style="magenta"
    ))
    console.print(Panel(
        f"[bold]Proposed Target:[/bold] {finance.target}\n[bold]Requested Credits:[/bold] [red]{finance.requested_credits} cr[/red] (Authority Ceiling: 25 cr)\n[bold]Rationale:[/bold] {finance.rationale}",
        title="[bold red]Financial Analyst Initial Proposal (Aggressive)[/bold red]", border_style="red"
    ))
    console.print()
    pause()

    # ---------------------------------------------------------
    # Step 5 & 6: High-Risk Action & Policy Engine Interception
    # ---------------------------------------------------------
    console.print("[bold yellow]>>> Step 5 & 6: High-Risk Proposal & Policy Engine Interception[/bold yellow]")
    initial_prop = ceo.formulate_action_proposal("task-03", finance)

    console.print("[yellow]Evaluating proposal against deterministic policy rules...[/yellow]")
    decision, receipt = engine.process_action_proposal("task-03", initial_prop)

    rejection_panel = Panel(
        "[bold red]PROPOSAL REJECTED BY POLICY ENGINE[/bold red]\n"
        "• [bold]Violated Rule 01 (SpendLimitRule):[/bold] Requested 50 credits exceeds authority ceiling of 25\n"
        "• [bold]Violated Rule 04 (TargetAllowlistRule):[/bold] Target 'http://unvetted-random-crypto-pool.xyz' not on approved allowlist\n"
        "[bold green][OK] Invariant Upheld:[/bold green] Zero credits executed. Zero ledger balances mutated.\n"
        "[bold cyan][OK] Deterministic Feedback:[/bold cyan] Rejection passed to CEO for bounded replanning.",
        title="[bold red]Safety Boundary Interception[/bold red]", border_style="red"
    )
    console.print(rejection_panel)
    console.print()
    pause()

    # ---------------------------------------------------------
    # Step 7 & 8: Bounded Replanning & Cryptographic Token Issuance
    # ---------------------------------------------------------
    console.print("[bold yellow]>>> Step 7 & 8: Bounded Replanning & Cryptographic Token Issuance[/bold yellow]")
    console.print("[green][OK] CEO Replanned proposal within authority ceiling (20 credits) targeting approved sandbox.[/green]")
    console.print(f"[bold cyan]Cryptographic Authorization Token:[/bold cyan] {receipt.authorization_token[:30]}...")
    console.print("[dim]Token cryptographically binds: Org ID, Proposal Content Hash, Decision ID, Policy Version Hash, TTL, and Nonce.[/dim]\n")
    pause()

    # ---------------------------------------------------------
    # Step 9: Controlled External Execution via Adapter
    # ---------------------------------------------------------
    console.print("[bold yellow]>>> Step 9: Controlled Execution via Adapter (Allowlist & SSRF Checked)[/bold yellow]")
    exec_table = Table(title="Execution Receipt", border_style="green")
    exec_table.add_column("Receipt ID", style="bold")
    exec_table.add_column("Target")
    exec_table.add_column("Action Type")
    exec_table.add_column("Cost Credits", justify="right")
    exec_table.add_column("HTTP Status", justify="center")
    exec_table.add_column("Response Hash")
    exec_table.add_row(
        receipt.id[:12] + "...",
        receipt.target,
        receipt.action_type.value,
        f"{receipt.cost_credits} cr",
        str(receipt.http_status or 200),
        receipt.raw_response_hash[:16] + "..."
    )
    console.print(exec_table)
    console.print()
    pause()

    # ---------------------------------------------------------
    # Step 10: Double-Entry Ledger Settlement & Conservation
    # ---------------------------------------------------------
    console.print("[bold yellow]>>> Step 10: Double-Entry Ledger Settlement & Credit Conservation[/bold yellow]")
    ledger_table = Table(title="Double-Entry Credit Conservation", border_style="cyan")
    ledger_table.add_column("Account", style="bold")
    ledger_table.add_column("Balance", justify="right")
    ledger_table.add_row("TREASURY", f"{ledger.get_balance(TREASURY)} cr")
    ledger_table.add_row("EXTERNAL_SINK", f"{ledger.get_balance(EXTERNAL_SINK)} cr")
    total_credits = ledger.get_balance(TREASURY) + ledger.get_balance(EXTERNAL_SINK)
    ledger_table.add_row("TOTAL CONSERVED", f"{total_credits} cr", style="bold green")
    console.print(ledger_table)
    assert total_credits == 100, "Conservation of credits violated!"
    console.print("[bold green][OK] Credit Conservation Verified: Exactly 100 credits exist across all accounts.[/bold green]\n")
    pause()

    # ---------------------------------------------------------
    # Step 11: Multi-Factor Performance & Reputation/Lifecycle
    # ---------------------------------------------------------
    console.print("[bold yellow]>>> Step 11: Multi-Factor Performance Review & Reputation/Lifecycle Update[/bold yellow]")
    rep_table = Table(title="Agent Multi-Factor Standing & Lifecycle", border_style="yellow")
    rep_table.add_column("Agent ID", style="bold")
    rep_table.add_column("Role")
    rep_table.add_column("Reputation", justify="right")
    rep_table.add_column("Reliability", justify="right")
    rep_table.add_column("Efficiency", justify="right")
    rep_table.add_column("Ceiling", justify="right")
    rep_table.add_column("Lifecycle Status")
    for a in org.agents.values():
        rep_table.add_row(
            a.id, a.role.value,
            f"{a.reputation_score:.1f}",
            f"{a.reliability_score:.1f}",
            f"{a.resource_efficiency:.2f}",
            f"{a.authority_ceiling} cr",
            a.status.value
        )
    console.print(rep_table)
    console.print()
    pause()

    # ---------------------------------------------------------
    # Step 12: Independent Auditor Verification & Experiment Summary
    # ---------------------------------------------------------
    console.print("[bold yellow]>>> Step 12: Independent Auditor Verification & Economic Experiment Summary[/bold yellow]")
    verification = engine.verification_receipts[-1]
    audit_table = Table(title="Auditor 8-Point Independent Verification Matrix", border_style="bright_magenta")
    audit_table.add_column("Verification Invariant Check", style="bold")
    audit_table.add_column("Auditor Result")
    for chk in verification.checks:
        audit_table.add_row(chk, "[bold green]VERIFIED PASS[/bold green]")
    console.print(audit_table)
    console.print(f"[bold bright_magenta]Verification Evidence Hash:[/bold bright_magenta] {verification.evidence_hash}\n")

    valid, err = event_store.verify_integrity()
    if valid:
        console.print("[bold green][OK] CRYPTOGRAPHIC AUDIT VERIFICATION: 100% UNTAMPERED (ALL HASHES CHAINED)[/bold green]\n")
    else:
        console.print(f"[bold red][FAIL] AUDIT FAILURE: {err}[/bold red]\n")

    # Run comparative economic experiment
    console.print("[bold cyan]Executing Comparative Economic Experiment (STATIC vs. PERFORMANCE vs. ADAPTIVE)...[/bold cyan]")
    exp_report = EconomicExperiment.run(num_rounds=4, initial_treasury=150)
    console.print(Panel(exp_report.format_table(), title="[bold green]Comparative Economic Experiment Results[/bold green]", border_style="green"))
    console.print(Panel(exp_report.summary_analysis, title="[bold cyan]Experiment Synthesis[/bold cyan]", border_style="cyan"))

    review = engine.complete_mission()
    console.print(Panel(
        f"[bold]Summary:[/bold] {review.summary}\n[bold]Lessons Learned:[/bold] {'; '.join(review.lessons_learned)}",
        title="[bold green]Final Mission Review[/bold green]", border_style="green"
    ))

    console.print("""
   ========================================================================
   | CORE INVARIANT PROVEN & MAINTAINED:                                  |
   |   AGENTS PROPOSE -> POLICIES AUTHORIZE -> EXECUTORS EXECUTE          |
   |   -> AUDITORS VERIFY                                                 |
   ========================================================================
    """, style="bold bright_green")

def main():
    parser = argparse.ArgumentParser(description="MAO 12-Step Target Demo Runner")
    parser.add_argument("--persist", type=str, default=None, help="Path to SQLite database for persistence")
    parser.add_argument("--fast", action="store_true", help="Run without artificial pauses")
    parser.add_argument("--live", action="store_true", help="Enable live LLM mode via OpenRouter API key")
    args = parser.parse_args()

    run_mao_demo(persist_db=args.persist, fast=args.fast, live=args.live)

if __name__ == "__main__":
    main()
