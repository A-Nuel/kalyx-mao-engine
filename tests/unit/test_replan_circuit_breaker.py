import pytest
from src.agents.mock_adapter import MockAgentAdapter
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.researcher import ResearcherAgent
from src.agents.roles.strategist import StrategistAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.domain.entities import Organisation, AgentRecord
from src.domain.enums import OrgState, AgentRole, ActionType, TaskStatus
from src.domain.exceptions import PolicyViolationError
from src.governance.policy_engine import PolicyEngine
from src.governance.human_gate import HumanGate
from src.economy.ledger import DoubleEntryLedger
from src.execution.executor import SandboxExecutor
from src.audit.event_store import AppendOnlyEventStore
from src.orchestration.engine import OrchestrationEngine

def test_circuit_breaker_aborts_after_max_replans():
    # Adapter configured to persistently propose invalid actions
    mock = MockAgentAdapter(force_repeated_rejection=True)
    ledger = DoubleEntryLedger(initial_treasury=100)
    engine_policy = PolicyEngine(signing_secret="circuit-breaker-secret")
    executor = SandboxExecutor(engine_policy, ledger)
    event_store = AppendOnlyEventStore()
    human_gate = HumanGate()

    org = Organisation(id="org-cb", mission="Circuit Breaker Test", treasury_balance=100)
    ceo_rec = AgentRecord(
        id="agent-ceo",
        role=AgentRole.CEO,
        authority_ceiling=25,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION, ActionType.REPLAN]
    )
    org.agents["agent-ceo"] = ceo_rec

    ceo = CEOAgent("agent-ceo", mock)
    researcher = ResearcherAgent("agent-research", mock)
    strategist = StrategistAgent("agent-strategy", mock)
    analyst = FinancialAnalystAgent("agent-finance", mock)

    orchestrator = OrchestrationEngine(
        org=org,
        ledger=ledger,
        policy_engine=engine_policy,
        executor=executor,
        event_store=event_store,
        human_gate=human_gate,
        ceo=ceo,
        researcher=researcher,
        strategist=strategist,
        financial_analyst=analyst,
        max_replan_attempts=3
    )

    orchestrator.start_mission()
    orchestrator.decompose_and_plan()
    _, _, fin = orchestrator.run_intelligence_pipeline()
    initial_prop = ceo.formulate_action_proposal("task-03", fin)

    # Must raise PolicyViolationError once replan attempt exceeds 3
    with pytest.raises(PolicyViolationError) as exc:
        orchestrator.process_action_proposal("task-03", initial_prop)

    assert "Max replan attempts (3) exceeded" in str(exc.value)
    assert org.state == OrgState.FAILED
    assert orchestrator.tasks["task-03"].status == TaskStatus.FAILED
    # Treasury remains protected
    assert ledger.get_balance("TREASURY") == 100
