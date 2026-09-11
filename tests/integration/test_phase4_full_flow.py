import pytest
from src.domain.entities import Organisation, AgentRecord, ActionProposal
from src.domain.enums import OrgState, AgentRole, ActionType, PolicyResult
from src.governance.policy_engine import PolicyEngine
from src.governance.human_gate import HumanGate
from src.economy.ledger import DoubleEntryLedger, TREASURY, EXTERNAL_SINK
from src.economy.reputation import ReputationEngine
from src.economy.experiment import EconomicExperiment, AllocationStrategy
from src.execution.executor import ControlledExternalExecutor
from src.audit.event_store import AppendOnlyEventStore
from src.audit.auditor import Auditor
from src.orchestration.engine import OrchestrationEngine
from src.agents.mock_adapter import MockAgentAdapter
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.researcher import ResearcherAgent
from src.agents.roles.strategist import StrategistAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent

@pytest.fixture
def phase4_pipeline():
    ledger = DoubleEntryLedger(initial_treasury=100)
    event_store = AppendOnlyEventStore()
    engine = PolicyEngine(signing_secret="phase4-test-secret-key")
    executor = ControlledExternalExecutor(
        policy_engine=engine,
        ledger=ledger,
        allowlist={
            "sandbox://market_index_fund",
            "sandbox://verified_bonds",
            "api://market_data/v1/summary"
        },
        mock_handler=lambda target, params: (200, {"data": "verified_result", "target": target})
    )
    human_gate = HumanGate()
    auditor = Auditor(verification_secret="phase4-test-secret-key")

    org = Organisation(
        id="org-phase4-flow",
        mission="Deploy capital into verified yields while preserving reserves",
        treasury_balance=100
    )

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
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.DATA_FETCH, ActionType.SIMULATED_ALLOCATION]
    )

    mock = MockAgentAdapter()
    ceo = CEOAgent("agent-ceo", mock)
    researcher = ResearcherAgent("agent-research", mock)
    strategist = StrategistAgent("agent-strategy", mock)
    analyst = FinancialAnalystAgent("agent-finance", mock)

    orchestrator = OrchestrationEngine(
        org=org,
        ledger=ledger,
        policy_engine=engine,
        executor=executor,
        event_store=event_store,
        human_gate=human_gate,
        ceo=ceo,
        researcher=researcher,
        strategist=strategist,
        financial_analyst=analyst,
        auditor=auditor
    )

    return {
        "orchestrator": orchestrator,
        "org": org,
        "ledger": ledger,
        "event_store": event_store,
        "auditor": auditor,
        "executor": executor,
        "ceo": ceo,
        "analyst": analyst
    }

def test_full_phase4_end_to_end_flow(phase4_pipeline):
    orchestrator = phase4_pipeline["orchestrator"]
    org = phase4_pipeline["org"]
    ledger = phase4_pipeline["ledger"]
    event_store = phase4_pipeline["event_store"]
    auditor = phase4_pipeline["auditor"]
    ceo = phase4_pipeline["ceo"]

    # 1. Mission start
    orchestrator.start_mission()
    assert org.state == OrgState.PLANNING

    # 2. Decompose and plan using AgentAssignmentEngine
    tasks = orchestrator.decompose_and_plan()
    assert len(tasks) == 3
    assert org.state == OrgState.EXECUTING
    assert tasks[0].assigned_agent_id == "agent-research"
    assert tasks[1].assigned_agent_id == "agent-strategy"
    assert tasks[2].assigned_agent_id == "agent-finance"

    # 3. Intelligence pipeline
    res, strat, fin = orchestrator.run_intelligence_pipeline()
    assert len(res.market_trend) > 0
    assert fin.requested_credits == 50  # Over budget initial proposal

    # 4. Process proposal with policy rejection and bounded replan
    initial_prop = ceo.formulate_action_proposal("task-03", fin)
    decision, receipt = orchestrator.process_action_proposal("task-03", initial_prop)

    # 5. Verify approved execution receipt and bounded remedy
    assert decision.result == PolicyResult.APPROVED
    assert receipt.cost_credits == 20  # Replan downscaled to 20 cr
    assert receipt.target == "sandbox://market_index_fund"
    assert receipt.action_type == ActionType.SIMULATED_ALLOCATION

    # 6. Double-entry ledger settlement & credit conservation
    assert ledger.get_balance(TREASURY) == 80
    assert ledger.get_balance(EXTERNAL_SINK) == 20
    assert ledger.get_balance(TREASURY) + ledger.get_balance(EXTERNAL_SINK) == 100

    # 7. Independent Auditor Verification
    assert len(orchestrator.verification_receipts) == 1
    verification = orchestrator.verification_receipts[0]
    assert verification.verified is True
    assert len(verification.failures) == 0

    # 8. Multi-factor reputation updates
    ceo_record = org.agents["agent-ceo"]
    assert ceo_record.successful_tasks >= 1
    assert ceo_record.reputation_score > 0
    assert ceo_record.resource_efficiency > 0

    # 9. Mission completion
    review = orchestrator.complete_mission()
    assert org.state == OrgState.COMPLETED
    assert len(review.summary) > 0

    # 10. Audit log cryptographic integrity
    is_valid, err = event_store.verify_integrity()
    assert is_valid is True
    assert err is None

def test_comparative_economic_experiment_integration():
    report = EconomicExperiment.run(num_rounds=3, initial_treasury=120)
    assert report.num_rounds == 3
    assert AllocationStrategy.STATIC in report.results
    assert AllocationStrategy.PERFORMANCE in report.results
    assert AllocationStrategy.ADAPTIVE in report.results
    
    table = report.format_table()
    assert "| STATIC |" in table
    assert "| PERFORMANCE |" in table
    assert "| ADAPTIVE |" in table
