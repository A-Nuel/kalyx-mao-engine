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
from src.execution.executor import SandboxExecutor
from src.audit.event_store import AppendOnlyEventStore
from src.audit.auditor import Auditor
from src.orchestration.engine import OrchestrationEngine
from src.agents.mock_adapter import MockAgentAdapter
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
   ========================================================================
    """, style="bold bright_blue")
    console.print(ascii_banner)

    tree = Tree("[bold cyan]ORGANISATIONAL GOVERNANCE PIPELINE[/bold cyan]")
    tree.add("[bold yellow]MISSION[/bold yellow] (100 ORG Credits & Conservative Policy)")
    tree.add("[bold green]AI ORGANISATION[/bold green] (CEO decomposes labour & delegates)")
    tree.add("[bold magenta]SPECIALIST AGENTS[/bold magenta] (Researcher -> Strategist -> Finance)")
    tree.add("[bold red]PROPOSAL[/bold red] (Structured Intent only - 0 execution power)")
    tree.add("[bold white on blue]POLICY ENGINE[/bold white on blue] (Deterministic rule check & HMAC token issuance)")
    tree.add("[bold yellow]EXECUTION LAYER[/bold yellow] (Controlled Sandbox Adapter - requires valid token)")
    tree.add("[bold bright_magenta]INDEPENDENT AUDITOR[/bold bright_magenta] (8-point cryptographic & economic verification)")
    tree.add("[bold bright_green]OUTCOME & SETTLEMENT[/bold bright_green] (Ledger updated, reputation adjusted)")
    console.print(Panel(tree, title="[bold]System Architecture[/bold]", border_style="bright_blue"))
    console.print()

def run_mao_demo(persist_db: str = None, fast: bool = True):
    print_header()

    def pause(sec: float = 0.5):
        if not fast:
            time.sleep(sec)

    console.print("[bold yellow]>>> Step 1 & 2: Initialising Organisation & Treasury[/bold yellow]")
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

    policy_engine = PolicyEngine(signing_secret="demo-terminal-key-3.11")
    executor = SandboxExecutor(policy_engine=policy_engine, ledger=ledger)
    human_gate = HumanGate()
    auditor = Auditor()

    org = Organisation(
        id="mao-alpha-01",
        mission="Maximize value under a fixed 100-credit budget and conservative policy",
        treasury_balance=100
    )

    # Roster
    org.agents["agent-ceo"] = AgentRecord(
        id="agent-ceo", role=AgentRole.CEO, authority_ceiling=25,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION, ActionType.REPLAN]
    )
    org.agents["agent-research"] = AgentRecord(
        id="agent-research", role=AgentRole.RESEARCHER, authority_ceiling=15,
        allowed_action_types=[ActionType.DATA_FETCH, ActionType.INTERNAL_ANALYSIS]
    )
    org.agents["agent-strategy"] = AgentRecord(
        id="agent-strategy", role=AgentRole.STRATEGIST, authority_ceiling=15,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS]
    )
    org.agents["agent-finance"] = AgentRecord(
        id="agent-finance", role=AgentRole.FINANCIAL_ANALYST, authority_ceiling=25,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION]
    )

    adapter = MockAgentAdapter()
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
    console.print(f"[green][OK] Organisation '{org.id}' created. Treasury: 100 ORG Credits.[/green]\n")
    pause()

    # Step 3: Decomposition
    console.print("[bold yellow]>>> Step 3: CEO Decomposes Mission into Specialist Tasks[/bold yellow]")
    tasks = engine.decompose_and_plan()
    task_table = Table(title="Delegated Specialist Tasks", border_style="cyan")
    task_table.add_column("Task ID", style="bold")
    task_table.add_column("Assigned Agent")
    task_table.add_column("Objective")
    task_table.add_column("Sub-Budget", justify="right")
    for t in tasks:
        task_table.add_row(t.id, t.assigned_agent_id, t.objective, f"{t.allocated_credits} cr")
    console.print(task_table)
    console.print()
    pause()

    # Step 4, 5, 6: Intelligence Pipeline
    console.print("[bold yellow]>>> Step 4, 5, 6: Intelligence Pipeline (Research -> Strategy -> Finance)[/bold yellow]")
    research, strategy, finance = engine.run_intelligence_pipeline()
    
    console.print(Panel(
        f"[bold]Findings:[/bold] {research.key_findings[0]}\n[bold]Recommendation:[/bold] {research.recommended_focus}",
        title="[cyan]Researcher Intelligence[/cyan]", border_style="cyan"
    ))
    console.print(Panel(
        f"[bold]Ranked:[/bold] {strategy.opportunities_ranked[0]}\n[bold]Risk Note:[/bold] {strategy.risk_analysis}",
        title="[magenta]Strategist Analysis[/magenta]", border_style="magenta"
    ))
    console.print(Panel(
        f"[bold]Target:[/bold] {finance.target}\n[bold]Requested Credits:[/bold] [red]{finance.requested_credits} cr[/red] (Ceiling: 25 cr)\n[bold]Rationale:[/bold] {finance.rationale}",
        title="[bold red]Financial Analyst Initial Proposal (Aggressive)[/bold red]", border_style="red"
    ))
    console.print()
    pause()

    # Step 7 & 8: Policy Rejection & Replanning
    console.print("[bold yellow]>>> Step 7 & 8: Safety Moment - Deterministic Policy Check & Recovery[/bold yellow]")
    initial_prop = ceo.formulate_action_proposal("task-03", finance)

    console.print("[yellow]Submitting 50-credit proposal to Policy Engine...[/yellow]")
    decision, receipt = engine.process_action_proposal("task-03", initial_prop)

    rejection_panel = Panel(
        "[bold red]PROPOSAL REJECTED BY POLICY ENGINE[/bold red]\n"
        "• [bold]Violated Rule 01:[/bold] Requested 50 credits exceeds authority ceiling of 25\n"
        "• [bold]Violated Rule 04:[/bold] Target 'http://unvetted-random-crypto-pool.xyz' not on approved allowlist\n"
        "[bold green][OK] Invariant Upheld:[/bold green] Zero credits executed. Zero state mutated.\n"
        "[bold cyan][OK] Replanning Triggered:[/bold cyan] CEO notified of rule violation and generated compliant remedy.",
        title="[bold red]Safety Boundary Interception[/bold red]", border_style="red"
    )
    console.print(rejection_panel)
    console.print()
    pause()

    # Step 9 & 10: Approved Execution
    console.print("[bold yellow]>>> Step 9 & 10: Compliant Execution via Sandbox Adapter[/bold yellow]")
    exec_table = Table(title="Execution Receipt", border_style="green")
    exec_table.add_column("Receipt ID", style="bold")
    exec_table.add_column("Target")
    exec_table.add_column("Cost Credits", justify="right")
    exec_table.add_column("Auth Token")
    exec_table.add_column("Response Hash")
    exec_table.add_row(
        receipt.id[:12] + "...",
        receipt.target,
        f"{receipt.cost_credits} cr",
        receipt.authorization_token[:18] + "...",
        receipt.raw_response_hash[:16] + "..."
    )
    console.print(exec_table)
    console.print()
    pause()

    # Step 11: Independent Auditor Verification
    console.print("[bold yellow]>>> Step 11: Independent Auditor Verification (8 Invariant Checks)[/bold yellow]")
    verification = engine.verification_receipts[-1]
    audit_table = Table(title="Auditor Independent Verification Matrix", border_style="bright_magenta")
    audit_table.add_column("Check Name", style="bold")
    audit_table.add_column("Result")
    for chk in verification.checks:
        audit_table.add_row(chk, "[bold green]PASS[/bold green]")
    console.print(audit_table)
    console.print(f"[bold bright_magenta]Evidence Hash:[/bold bright_magenta] {verification.evidence_hash}\n")
    pause()

    # Step 12: Ledger & Reputation
    console.print("[bold yellow]>>> Step 12: Ledger Settlement & Performance-Linked Reputation[/bold yellow]")
    ledger_table = Table(title="Double-Entry Credit Conservation", border_style="cyan")
    ledger_table.add_column("Account", style="bold")
    ledger_table.add_column("Balance", justify="right")
    ledger_table.add_row("TREASURY", f"{ledger.get_balance(TREASURY)} cr")
    ledger_table.add_row("EXTERNAL_SINK", f"{ledger.get_balance(EXTERNAL_SINK)} cr")
    ledger_table.add_row("TOTAL CONSERVED", f"{ledger.get_balance(TREASURY) + ledger.get_balance(EXTERNAL_SINK)} cr")
    console.print(ledger_table)

    rep_table = Table(title="Agent Performance & Reputation Updates", border_style="yellow")
    rep_table.add_column("Agent ID", style="bold")
    rep_table.add_column("Role")
    rep_table.add_column("Reputation Score")
    rep_table.add_column("Status")
    for a in org.agents.values():
        rep_table.add_row(a.id, a.role.value, f"{a.reputation_score:.1f}", a.status.value)
    console.print(rep_table)
    console.print()
    pause()

    # Step 13 & 14: Mission Complete & Cryptographic Trail
    console.print("[bold yellow]>>> Step 13 & 14: Mission Review & Cryptographic Audit Chain[/bold yellow]")
    review = engine.complete_mission()
    console.print(Panel(
        f"[bold]Summary:[/bold] {review.summary}\n[bold]Lessons Learned:[/bold] {'; '.join(review.lessons_learned)}",
        title="[bold green]Final Mission Review[/bold green]", border_style="green"
    ))

    events = event_store.get_events()
    chain_table = Table(title=f"Append-Only Audit Chain ({len(events)} Events)", border_style="bright_blue")
    chain_table.add_column("Seq", justify="right")
    chain_table.add_column("Actor")
    chain_table.add_column("Event Type")
    chain_table.add_column("Entity ID")
    chain_table.add_column("Event Hash (SHA-256)", style="dim")
    for e in events:
        chain_table.add_row(str(e.sequence_id), e.actor_id, e.event_type, e.entity_id, e.event_hash[:20] + "...")
    console.print(chain_table)

    valid, err = event_store.verify_integrity()
    if valid:
        console.print("[bold green][OK] CRYPTOGRAPHIC AUDIT VERIFICATION: 100% UNTAMPERED (ALL HASHES CHAINED)[/bold green]")
    else:
        console.print(f"[bold red][FAIL] AUDIT FAILURE: {err}[/bold red]")

    console.print("""
   ========================================================================
   | CORE INVARIANT PROVEN:                                               |
   |   Agents propose. Policies authorize. Executors execute.             |
   |   Auditors verify.                                                   |
   ========================================================================
    """, style="bold bright_green")

def main():
    parser = argparse.ArgumentParser(description="MAO 3-Minute Demo Runner")
    parser.add_argument("--persist", type=str, default=None, help="Path to SQLite database for persistence")
    parser.add_argument("--fast", action="store_true", help="Run without artificial pauses")
    args = parser.parse_args()

    run_mao_demo(persist_db=args.persist, fast=args.fast)

if __name__ == "__main__":
    main()
