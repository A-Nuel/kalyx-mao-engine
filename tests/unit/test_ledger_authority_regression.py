import pytest
from src.domain.entities import Organisation, AgentRecord, ActionProposal, Task
from src.domain.enums import OrgState, AgentRole, ActionType, PolicyResult, TaskStatus
from src.domain.exceptions import PolicyViolationError
from src.governance.policy_engine import PolicyEngine
from src.governance.rules import TreasuryBalanceRule
from src.economy.ledger import DoubleEntryLedger, TREASURY
from src.orchestration.engine import OrchestrationEngine
from src.agents.mock_adapter import MockAgentAdapter
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.researcher import ResearcherAgent
from src.agents.roles.strategist import StrategistAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.execution.executor import SandboxExecutor
from src.governance.human_gate import HumanGate
from src.audit.event_store import AppendOnlyEventStore

def test_tampered_org_treasury_rejected_by_policy_engine():
    # Ledger only has 10 credits
    ledger = DoubleEntryLedger(initial_treasury=10)
    engine = PolicyEngine(signing_secret="regression-secret")

    # Attacker or bug tampers with Organisation entity to claim 99,999 credits
    org = Organisation(
        id="org-tampered",
        mission="Adversarial balance test",
        treasury_balance=99999,
        state=OrgState.EXECUTING
    )
    analyst = AgentRecord(
        id="agent-analyst",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=50,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION]
    )
    org.agents["agent-analyst"] = analyst

    # Propose spending 30 credits (less than authority ceiling 50, but greater than real treasury 10)
    proposal = ActionProposal(
        id="prop-overspend",
        task_id="t-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://verified_bonds",
        requested_credits=30,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Attempting to spend phantom credits"
    )

    # When evaluate is called with the authoritative ledger:
    decision = engine.evaluate(proposal, org, ledger=ledger)
    assert decision.result == PolicyResult.REJECTED
    assert decision.violated_rule_id == "RULE-02"
    assert "exceeds available treasury (10)" in decision.violated_rule_description

def test_tampered_org_treasury_rejected_in_orchestration_pipeline():
    # Orchestrator with real ledger of only 15 credits
    ledger = DoubleEntryLedger(initial_treasury=15)
    engine = PolicyEngine(signing_secret="regression-secret-2")
    executor = SandboxExecutor(policy_engine=engine, ledger=ledger)
    event_store = AppendOnlyEventStore()
    human_gate = HumanGate()

    # Tampered org claims 50,000 credits
    org = Organisation(
        id="org-orch-tampered",
        mission="Tampered Orchestrator test",
        treasury_balance=50000,
        state=OrgState.EXECUTING
    )
    mock = MockAgentAdapter()
    ceo = CEOAgent("agent-ceo", mock)
    researcher = ResearcherAgent("agent-research", mock)
    strategist = StrategistAgent("agent-strategy", mock)
    analyst = FinancialAnalystAgent("agent-analyst", mock)

    org.agents["agent-ceo"] = AgentRecord(id="agent-ceo", role=AgentRole.CEO, authority_ceiling=50)
    org.agents["agent-analyst"] = AgentRecord(
        id="agent-analyst",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=50,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION]
    )

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
        max_replan_attempts=1
    )

    task = Task(
        id="t-01",
        mission_id=org.id,
        assigned_agent_id="agent-analyst",
        objective="Test spend",
        allocated_credits=30,
        status=TaskStatus.IN_PROGRESS
    )
    orchestrator.tasks["t-01"] = task

    proposal = ActionProposal(
        id="prop-phantom-spend",
        task_id="t-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://verified_bonds",
        requested_credits=30,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Trying to fool orchestrator with tampered org balance"
    )

    # Must be intercepted by Policy Engine because authoritative ledger has only 15 credits
    # Since max_replan_attempts=1, repeated rejection or policy violation triggers rejection
    try:
        decision, receipt = orchestrator.process_action_proposal("t-01", proposal)
        # If it replanned, check that the executed proposal did not spend more than real treasury (15)
        assert receipt.cost_credits <= 15
    except PolicyViolationError as exc:
        assert "RULE-02" in str(exc) or "Max replan" in str(exc)

    # Invariant: Ledger never went below zero and never spent phantom credits
    assert ledger.get_balance(TREASURY) >= 0
    assert ledger.verify_conservation() is True
