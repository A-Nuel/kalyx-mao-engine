import pytest
from src.domain.entities import Organisation, AgentRecord, ActionProposal, PolicyDecision
from src.domain.enums import OrgState, AgentRole, ActionType, PolicyResult
from src.domain.exceptions import UnauthorizedActionError
from src.governance.policy_engine import PolicyEngine
from src.governance.rules import PolicyRule, TargetAllowlistRule
from src.execution.executor import SandboxExecutor
from src.economy.ledger import DoubleEntryLedger
from src.audit.auditor import Auditor, AuditVerificationError

class CustomStricterRule(PolicyRule):
    rule_id = "RULE-09"
    description = "Custom strict policy rule requiring all targets to use https"

    def evaluate(self, proposal: ActionProposal, agent: AgentRecord, org: Organisation, **kwargs) -> str | None:
        if not proposal.target.startswith("https://") and not proposal.target.startswith("sandbox://"):
            return "Target must be HTTPS or Sandbox"
        return None

def test_deterministic_policy_version_hash():
    engine_1 = PolicyEngine(signing_secret="fixed-secret")
    engine_2 = PolicyEngine(signing_secret="different-secret")  # Secret does not affect policy version hash

    # Policy version hash depends only on rules and approval threshold, not secret
    assert engine_1.get_policy_version_hash() == engine_2.get_policy_version_hash()
    assert len(engine_1.get_policy_version_hash()) == 64

def test_adding_rule_changes_policy_version():
    engine = PolicyEngine()
    initial_version = engine.get_policy_version_hash()

    engine.rules.append(CustomStricterRule())
    updated_version = engine.get_policy_version_hash()

    assert initial_version != updated_version

def test_modifying_threshold_changes_policy_version():
    engine = PolicyEngine(human_approval_threshold=40)
    v1 = engine.get_policy_version_hash()

    engine.human_approval_threshold = 20
    v2 = engine.get_policy_version_hash()

    assert v1 != v2

def test_adversarial_stale_policy_version_token_rejected_at_execution():
    ledger = DoubleEntryLedger(initial_treasury=100)
    engine = PolicyEngine(signing_secret="sec-version-test")
    executor = SandboxExecutor(engine, ledger)

    org = Organisation(id="org-policy-test", mission="Policy Evolution", treasury_balance=100, state=OrgState.EXECUTING)
    ceo = AgentRecord(
        id="agent-ceo",
        role=AgentRole.CEO,
        authority_ceiling=30,
        allowed_action_types=[ActionType.SIMULATED_ALLOCATION]
    )
    org.agents["agent-ceo"] = ceo

    proposal = ActionProposal(
        id="prop-v-01",
        task_id="t-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=10,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Policy version test"
    )

    # 1. Proposal evaluated and approved under Policy Version A
    decision = engine.evaluate(proposal, org)
    assert decision.result == PolicyResult.APPROVED
    token_v_a = decision.authorization_token

    # 2. Governance updates policies: a new stricter rule is added (Policy Version B)
    engine.rules.append(CustomStricterRule())
    new_version_hash = engine.get_policy_version_hash()

    # 3. Execution attempt with token issued under Policy Version A must fail under Policy Version B
    with pytest.raises(UnauthorizedActionError) as exc:
        executor.execute(proposal, decision, org)
    assert "Policy version mismatch" in str(exc.value)

def test_auditor_rejects_stale_policy_version():
    engine = PolicyEngine(signing_secret="sec-audit-ver")
    auditor = Auditor(verification_secret="sec-audit-ver")

    org = Organisation(id="org-ver-audit", mission="Audit Version Test", treasury_balance=100, state=OrgState.EXECUTING)
    ceo = AgentRecord(id="agent-ceo", role=AgentRole.CEO, authority_ceiling=30, allowed_action_types=[ActionType.SIMULATED_ALLOCATION])
    org.agents["agent-ceo"] = ceo

    proposal = ActionProposal(
        id="prop-va-01",
        task_id="t-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=10,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Audit version check"
    )

    decision = engine.evaluate(proposal, org)
    token = decision.authorization_token

    # Auditor verifies with a different expected policy version hash
    valid, reason = auditor.verify_authorization_token(
        token=token,
        proposal=proposal,
        org=org,
        decision=decision,
        expected_policy_version="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )
    assert valid is False
    assert "Policy version mismatch" in reason
