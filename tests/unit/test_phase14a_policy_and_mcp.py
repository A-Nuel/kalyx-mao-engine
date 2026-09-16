"""Phase 14A: policy authority separation, MCP client safety, direct-access prevention."""
import pytest
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, AgentStatus, OrgState, PolicyResult
from src.economy.ledger import DoubleEntryLedger
from src.governance.policy_engine import PolicyEngine
from src.governance.external_economy_rules import (
    ExternalInferenceAuthorityRule,
    ExternalInferenceTargetRule,
    OrbioKeyLifecycleAuthorityRule,
)
from src.governance.rules import (
    AgentStatusRule,
    OrgPauseRule,
    RolePermissionRule,
    SpendLimitRule,
    TreasuryBalanceRule,
)
from src.external.orbio.mcp_client import OrbioMCPClient, OrbioMCPError


def _engine_with_orbio_rules() -> PolicyEngine:
    return PolicyEngine(
        rules=[
            OrgPauseRule(),
            AgentStatusRule(),
            TreasuryBalanceRule(),
            SpendLimitRule(),
            RolePermissionRule(),
            OrbioKeyLifecycleAuthorityRule(),
            ExternalInferenceAuthorityRule(),
            ExternalInferenceTargetRule(),
        ],
        signing_secret="phase14a-test-secret",
        human_approval_threshold=1000,
    )


def test_high_reputation_cannot_create_keys_without_authority():
    engine = _engine_with_orbio_rules()
    ledger = DoubleEntryLedger(initial_treasury=100)
    org = Organisation(id="org-1", mission="t", treasury_balance=100, state=OrgState.EXECUTING)
    agent = AgentRecord(
        id="agent-research",
        role=AgentRole.RESEARCHER,
        authority_ceiling=25,
        reputation_score=100.0,
        credit_balance=20,
        allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.DATA_FETCH],
        status=AgentStatus.ACTIVE,
    )
    org.agents[agent.id] = agent
    proposal = ActionProposal(
        id="p1",
        task_id="t1",
        proposing_agent_id=agent.id,
        action_type=ActionType.ORBIO_KEY_LIFECYCLE,
        target="orbio://key/create",
        parameters={"operation": "CREATE"},
        requested_credits=0,
        expected_value_score=0.5,
        risk_assessment="low",
        rationale="try create key",
    )
    decision = engine.evaluate(proposal, org, ledger=ledger)
    assert decision.result == PolicyResult.REJECTED
    assert decision.violated_rule_id in {"RULE-03", "RULE-ORBIO-02"}


def test_authorized_agent_can_propose_key_lifecycle():
    engine = _engine_with_orbio_rules()
    ledger = DoubleEntryLedger(initial_treasury=100)
    org = Organisation(id="org-1", mission="t", treasury_balance=100, state=OrgState.EXECUTING)
    agent = AgentRecord(
        id="agent-ops",
        role=AgentRole.CEO,
        authority_ceiling=25,
        reputation_score=50.0,
        credit_balance=10,
        allowed_action_types=[ActionType.ORBIO_KEY_LIFECYCLE, ActionType.INTERNAL_ANALYSIS],
        status=AgentStatus.ACTIVE,
    )
    org.agents[agent.id] = agent
    proposal = ActionProposal(
        id="p2",
        task_id="t1",
        proposing_agent_id=agent.id,
        action_type=ActionType.ORBIO_KEY_LIFECYCLE,
        target="orbio://key/create",
        parameters={"operation": "CREATE"},
        requested_credits=0,
        expected_value_score=0.5,
        risk_assessment="low",
        rationale="authorized create",
    )
    decision = engine.evaluate(proposal, org, ledger=ledger)
    assert decision.result == PolicyResult.APPROVED
    assert decision.authorization_token is not None


def test_external_inference_target_allowlist():
    engine = _engine_with_orbio_rules()
    ledger = DoubleEntryLedger(initial_treasury=100)
    org = Organisation(id="org-1", mission="t", treasury_balance=100, state=OrgState.EXECUTING)
    agent = AgentRecord(
        id="agent-r",
        role=AgentRole.RESEARCHER,
        authority_ceiling=25,
        allowed_action_types=[ActionType.EXTERNAL_INFERENCE],
        status=AgentStatus.ACTIVE,
    )
    org.agents[agent.id] = agent
    bad = ActionProposal(
        id="p3",
        task_id="t1",
        proposing_agent_id=agent.id,
        action_type=ActionType.EXTERNAL_INFERENCE,
        target="https://evil.example/api",
        parameters={},
        requested_credits=5,
        expected_value_score=0.5,
        risk_assessment="low",
        rationale="bad target",
    )
    decision = engine.evaluate(bad, org, ledger=ledger)
    assert decision.result == PolicyResult.REJECTED
    assert decision.violated_rule_id == "RULE-ORBIO-01"


def test_mcp_client_refuses_unknown_tools():
    client = OrbioMCPClient("https://www.orbio.so/api/mcp", api_key=None)
    with pytest.raises(OrbioMCPError, match="Refusing unknown MCP tool"):
        client.tools_call("orbio_run_inference", {})


def test_mcp_unauthenticated_get_balance_fails_closed():
    client = OrbioMCPClient("https://www.orbio.so/api/mcp", api_key=None, timeout=10.0)
    with pytest.raises(OrbioMCPError):
        client.get_balance()
