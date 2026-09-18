"""Unit tests for Phase 17 Governed Dynamic Capability Evolution."""

from datetime import datetime, timezone, timedelta
import pytest
from src.domain.capability import CapabilityGrant, CapabilityGrantStatus, CapabilityProposal
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, OrgState, PolicyResult
from src.governance.capability_policy import CapabilityEvolutionRule
from src.governance.policy_engine import PolicyEngine
from src.orchestration.capability_manager import CapabilityManager
from src.persistence.database import Database
from src.persistence.marketplace_repository import MarketplaceRepository


def test_capability_evolution_rule_high_performance():
    rule = CapabilityEvolutionRule(min_performance_threshold=0.80)
    agent = AgentRecord(id="agent-worker", organisation_id="org-beta", role=AgentRole.RESEARCHER)
    org = Organisation(id="org-beta", mission="Analytics Development", state=OrgState.PLANNING)

    proposal = ActionProposal(
        id="prop-1",
        task_id="task-1",
        proposing_agent_id="ceo-supervisor",
        action_type=ActionType.PROPOSE_CAPABILITY_EXPANSION,
        target="capability://ADVANCED_ANALYTICS",
        parameters={
            "target_agent_id": "agent-worker",
            "proposer_agent_id": "ceo-supervisor",
            "requested_capability": "ADVANCED_ANALYTICS",
            "performance_score": 90.0,
            "supervisor_approved": True,
        },
        requested_credits=0,
        expected_value_score=0.9,
        risk_assessment="Low risk",
        rationale="Promotion",
    )
    result = rule.evaluate(proposal, agent, org)
    assert result is None  # Approved


def test_capability_evolution_rule_low_performance():
    rule = CapabilityEvolutionRule(min_performance_threshold=0.80)
    agent = AgentRecord(id="agent-worker", organisation_id="org-beta", role=AgentRole.RESEARCHER)
    org = Organisation(id="org-beta", mission="Analytics Development", state=OrgState.PLANNING)

    proposal = ActionProposal(
        id="prop-2",
        task_id="task-2",
        proposing_agent_id="ceo-supervisor",
        action_type=ActionType.PROPOSE_CAPABILITY_EXPANSION,
        target="capability://ADVANCED_ANALYTICS",
        parameters={
            "target_agent_id": "agent-worker",
            "proposer_agent_id": "ceo-supervisor",
            "requested_capability": "ADVANCED_ANALYTICS",
            "performance_score": 65.0,  # Below 80.0
            "supervisor_approved": True,
        },
        requested_credits=0,
        expected_value_score=0.9,
        risk_assessment="Low risk",
        rationale="Promotion attempt",
    )
    result = rule.evaluate(proposal, agent, org)
    assert result is not None
    assert "below the required capability promotion threshold" in result


def test_capability_evolution_rule_anti_self_grant():
    rule = CapabilityEvolutionRule(min_performance_threshold=0.80)
    agent = AgentRecord(id="agent-worker", organisation_id="org-beta", role=AgentRole.RESEARCHER)
    org = Organisation(id="org-beta", mission="Analytics Development", state=OrgState.PLANNING)

    # Rogue agent attempts to propose and grant itself a capability without supervisor approval
    proposal = ActionProposal(
        id="prop-3",
        task_id="task-3",
        proposing_agent_id="agent-worker",
        action_type=ActionType.PROPOSE_CAPABILITY_EXPANSION,
        target="capability://ROOT_PRIVILEGES",
        parameters={
            "target_agent_id": "agent-worker",
            "proposer_agent_id": "agent-worker",
            "requested_capability": "ROOT_PRIVILEGES",
            "performance_score": 99.0,
            "supervisor_approved": False,
        },
        requested_credits=0,
        expected_value_score=0.9,
        risk_assessment="High risk self grant",
        rationale="Self promotion",
    )
    result = rule.evaluate(proposal, agent, org)
    assert result is not None
    assert "cannot self-grant capabilities without supervisor/coordinator authorization" in result


def test_capability_manager_grant_and_verify():
    db = Database(":memory:")
    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("org-beta", "tenant-demo", "Beta Mission", 100, "PLANNING"),
        )
    repo = MarketplaceRepository(db)
    policy_engine = PolicyEngine(signing_secret="test-secret")
    cap_manager = CapabilityManager(repository=repo, policy_engine=policy_engine, default_grant_duration_days=7)

    supervisor = AgentRecord(
        id="org-beta-ceo",
        organisation_id="org-beta",
        role=AgentRole.CEO,
        allowed_action_types=[ActionType.PROPOSE_CAPABILITY_EXPANSION],
    )
    worker = AgentRecord(
        id="org-beta-worker",
        organisation_id="org-beta",
        role=AgentRole.RESEARCHER,
    )
    org = Organisation(
        id="org-beta",
        mission="Analytics Development",
        state=OrgState.PLANNING,
        agents={"org-beta-ceo": supervisor, "org-beta-worker": worker},
    )

    proposal = CapabilityProposal(
        tenant_id="tenant-demo",
        organisation_id="org-beta",
        proposal_id="prop-1",
        target_agent_id="org-beta-worker",
        proposer_agent_id="org-beta-ceo",
        requested_capability="EXTERNAL_COMMERCE",
        requested_permission="EXECUTE_ADVANCED",
        current_performance_score=88.5,
        rationale="Strong performance in previous missions",
    )

    grant = cap_manager.evaluate_and_grant(proposal, supervisor, worker, org)
    assert grant is not None
    assert grant.capability_name == "EXTERNAL_COMMERCE"
    assert grant.status == CapabilityGrantStatus.ACTIVE

    # Check capability query
    has_cap = cap_manager.check_agent_capability("tenant-demo", "org-beta", "org-beta-worker", "EXTERNAL_COMMERCE")
    assert has_cap is True

    # Check non-granted capability query
    has_other = cap_manager.check_agent_capability("tenant-demo", "org-beta", "org-beta-worker", "ARBITRARY_TOOL")
    assert has_other is False
