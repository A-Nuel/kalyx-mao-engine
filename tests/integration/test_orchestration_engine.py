import pytest
from src.agents.mock_adapter import MockAgentAdapter
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.researcher import ResearcherAgent
from src.agents.roles.strategist import StrategistAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.domain.entities import Organisation, AgentRecord
from src.domain.enums import OrgState, AgentRole, ActionType, TaskStatus, PolicyResult
from src.governance.policy_engine import PolicyEngine
from src.governance.human_gate import HumanGate
from src.economy.ledger import DoubleEntryLedger, TREASURY, EXTERNAL_SINK
from src.execution.executor import SandboxExecutor
from src.audit.event_store import AppendOnlyEventStore
from src.orchestration.engine import OrchestrationEngine

def test_full_demo_scenario_end_to_end():
    """
    Executes the exact 3-minute demo script:
    0:00 - Create organisation, 100-credit budget, conservative policy
    0:20 - Organisation forms: CEO creates 3 tasks (Research, Strategy, Finance)
    0:50 - Intelligence phase: Research gathers evidence, Strategy ranks, Finance proposes
    1:20 - Safety moment: 50-credit proposal rejected by Policy Engine (RULE-01, RULE-04)
           CEO replans with compliant 20-credit proposal
    1:45 - Execution: 20-credit action approved, executed by SandboxExecutor
    2:20 - Agent economy: Treasury settled (80 remaining), successful agents rewarded
    2:45 - Audit trail: Cryptographic verification passes with 100% tamper detection
    """
    mock = MockAgentAdapter()
    ledger = DoubleEntryLedger(initial_treasury=100)
    policy_engine = PolicyEngine(signing_secret="demo-scenario-secret-key")
    executor = SandboxExecutor(policy_engine, ledger)
    event_store = AppendOnlyEventStore()
    human_gate = HumanGate()

    org = Organisation(
        id="org-demo",
        mission="Achieve highest-value outcome possible under a fixed 100-credit budget and conservative policy",
        treasury_balance=100
    )

    # Roster
    ceo_rec = AgentRecord(
        id="agent-ceo",
        role=AgentRole.CEO,
        authority_ceiling=25,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION, ActionType.REPLAN]
    )
    res_rec = AgentRecord(
        id="agent-research",
        role=AgentRole.RESEARCHER,
        authority_ceiling=15,
        allowed_action_types=[ActionType.DATA_FETCH, ActionType.INTERNAL_ANALYSIS]
    )
    strat_rec = AgentRecord(
        id="agent-strategy",
        role=AgentRole.STRATEGIST,
        authority_ceiling=15,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS]
    )
    fin_rec = AgentRecord(
        id="agent-finance",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=25,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION]
    )

    org.agents["agent-ceo"] = ceo_rec
    org.agents["agent-research"] = res_rec
    org.agents["agent-strategy"] = strat_rec
    org.agents["agent-finance"] = fin_rec

    ceo = CEOAgent("agent-ceo", mock)
    researcher = ResearcherAgent("agent-research", mock)
    strategist = StrategistAgent("agent-strategy", mock)
    analyst = FinancialAnalystAgent("agent-finance", mock)

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
        max_replan_attempts=3
    )

    # 1. Mission start
    engine.start_mission()
    assert org.state == OrgState.PLANNING

    # 2. Decompose mission
    tasks = engine.decompose_and_plan()
    assert len(tasks) == 3
    assert org.state == OrgState.EXECUTING

    # 3. Intelligence pipeline
    research, strategy, finance = engine.run_intelligence_pipeline()
    assert len(research.key_findings) > 0
    assert strategy.expected_roi_score > 0
    assert finance.requested_credits == 50  # Initial over-budget proposal

    # 4. Formulate initial proposal & process (Safety moment & replanning)
    initial_proposal = ceo.formulate_action_proposal("task-03", finance)
    decision, receipt = engine.process_action_proposal("task-03", initial_proposal)

    # Assertions on safety recovery
    assert decision.result == PolicyResult.APPROVED
    assert receipt is not None
    assert receipt.cost_credits == 20  # Replanned value!
    assert receipt.target == "sandbox://market_index_fund"
    assert engine.replan_counts["task-03"] == 1

    # 5. Complete mission
    review = engine.complete_mission()
    assert review.mission_success is True
    assert org.state == OrgState.COMPLETED

    # 6. Economic & Ledger Invariant Checks
    assert ledger.get_balance(TREASURY) == 80  # 100 initial - 20 executed
    assert ledger.get_balance(EXTERNAL_SINK) == 20
    assert ledger.verify_conservation() is True

    # 7. Reputation Verification
    # Researcher succeeded on research task: +3.0 -> 100 (clamped)
    assert res_rec.reputation_score == 100.0
    # CEO had 1 policy violation (-10) and 1 execution success (+3) -> 93.0
    assert ceo_rec.reputation_score == 93.0
    assert ceo_rec.status == "ACTIVE"

    # 8. Cryptographic Audit Trail Verification
    events = event_store.get_events()
    assert len(events) >= 8  # Full lifecycle tracked
    valid, err = event_store.verify_integrity()
    assert valid is True
    assert err is None

    # Check that both the rejection and approved execution are recorded
    event_types = [e.event_type for e in events]
    assert "MISSION_STARTED" in event_types
    assert "MISSION_PLAN_CREATED" in event_types
    assert "RESEARCH_COMPLETED" in event_types
    assert "PROPOSAL_SUBMITTED" in event_types
    assert "POLICY_EVALUATED" in event_types
    assert "REPLAN_TRIGGERED" in event_types
    assert "ACTION_EXECUTED" in event_types
    assert "MISSION_COMPLETED" in event_types
