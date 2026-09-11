import pytest
from src.agents.mock_adapter import MockAgentAdapter
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.researcher import ResearcherAgent
from src.agents.roles.strategist import StrategistAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.domain.entities import ActionProposal
from src.domain.enums import ActionType

def test_agent_roles_with_mock_adapter():
    mock = MockAgentAdapter()
    ceo = CEOAgent("agent-ceo", mock)
    researcher = ResearcherAgent("agent-research", mock)
    strategist = StrategistAgent("agent-strategy", mock)
    analyst = FinancialAnalystAgent("agent-finance", mock)

    # 1. CEO Plan
    plan = ceo.create_initial_plan("Demo Mission", 100)
    assert len(plan.tasks) == 3
    assert plan.tasks[0].assigned_role.value == "RESEARCHER"

    # 2. Researcher
    res = researcher.conduct_research("Research markets")
    assert len(res.key_findings) == 2
    assert "Unvetted crypto liquidity pool" in res.key_findings[0]

    # 3. Strategist
    strat = strategist.evaluate_strategy(res)
    assert strat.expected_roi_score == 0.85
    assert len(strat.opportunities_ranked) == 2

    # 4. Financial Analyst
    fin = analyst.formulate_proposal(strat)
    assert fin.requested_credits == 50 # Initial aggressive test proposal

    # 5. CEO wrap proposal
    prop = ceo.formulate_action_proposal("task-03", fin)
    assert prop.requested_credits == 50
    assert prop.target == "http://unvetted-random-crypto-pool.xyz"

    # 6. CEO replan after rejection
    replanned_prop = ceo.replan_after_rejection(
        task_id="task-03",
        rejected_proposal=prop,
        violated_rule_id="RULE-01",
        violated_rule_description="Exceeds authority ceiling",
        attempt_number=1
    )
    assert replanned_prop.requested_credits == 20
    assert replanned_prop.target == "sandbox://market_index_fund"
