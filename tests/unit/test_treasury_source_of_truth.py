import pytest
from src.domain.entities import Organisation, AgentRecord, ActionProposal
from src.domain.enums import OrgState, AgentRole, ActionType, PolicyResult
from src.domain.exceptions import InsufficientCreditsError, UnauthorizedActionError
from src.governance.policy_engine import PolicyEngine
from src.execution.executor import SandboxExecutor
from src.economy.ledger import DoubleEntryLedger, TREASURY, EXTERNAL_SINK
from src.persistence.database import Database
from src.persistence.repositories import SqliteLedger, SqliteRepository

def test_ledger_is_sole_authoritative_source_of_truth():
    ledger = DoubleEntryLedger(initial_treasury=100)
    engine = PolicyEngine()
    executor = SandboxExecutor(engine, ledger)

    org = Organisation(id="org-truth", mission="Source of truth test", treasury_balance=100, state=OrgState.EXECUTING)
    ceo = AgentRecord(id="agent-ceo", role=AgentRole.CEO, authority_ceiling=80, allowed_action_types=[ActionType.SIMULATED_ALLOCATION])
    org.agents["agent-ceo"] = ceo

    # In-memory tampering attack: an adversary manipulates org.treasury_balance to 1,000,000
    org.treasury_balance = 1_000_000

    # PolicyEngine evaluated with authoritative ledger sees actual balance
    proposal = ActionProposal(
        id="prop-overdraft",
        task_id="t-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=200,  # exceeds actual 100 credits in ledger
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Overdraft attempt"
    )

    decision_with_ledger = engine.evaluate(proposal, org, ledger=ledger)
    assert decision_with_ledger.result == PolicyResult.REJECTED
    assert decision_with_ledger.violated_rule_id == "RULE-02"

def test_executor_enforces_ledger_balance_despite_tampered_org_balance():
    ledger = DoubleEntryLedger(initial_treasury=20)
    engine = PolicyEngine(signing_secret="truth-sec")
    executor = SandboxExecutor(engine, ledger)

    org = Organisation(id="org-truth-exec", mission="Truth Exec", treasury_balance=20, state=OrgState.EXECUTING)
    ceo = AgentRecord(id="agent-ceo", role=AgentRole.CEO, authority_ceiling=80, allowed_action_types=[ActionType.SIMULATED_ALLOCATION])
    org.agents["agent-ceo"] = ceo

    proposal = ActionProposal(
        id="prop-exec-overdraft",
        task_id="t-01",
        proposing_agent_id="agent-ceo",
        action_type=ActionType.SIMULATED_ALLOCATION,
        target="sandbox://market_index_fund",
        requested_credits=35,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Overdraft attempt"
    )

    # Adversary sets org.treasury_balance = 500 and evaluates without ledger reference
    org.treasury_balance = 500
    decision = engine.evaluate(proposal, org)
    assert decision.result == PolicyResult.APPROVED

    # When Executor runs, it MUST check the authoritative ledger and reject with InsufficientCreditsError
    with pytest.raises(InsufficientCreditsError) as exc:
        executor.execute(proposal, decision, org)
    assert "has 20 credits, cannot transfer 35" in str(exc.value)

def test_sqlite_repository_loads_authoritative_ledger_balance(tmp_path):
    db_file = str(tmp_path / "ledger_truth.db")
    db = Database(db_file)
    ledger = SqliteLedger(db, initial_treasury=100)
    repo = SqliteRepository(db)

    org = Organisation(id="org-sql-truth", mission="SQL Truth", treasury_balance=100, state=OrgState.EXECUTING)
    repo.save_organisation(org)

    # Spend 30 credits via ledger
    ledger.transfer(from_account=TREASURY, to_account=EXTERNAL_SINK, amount=30, memo="Spend 30")
    assert ledger.get_balance(TREASURY) == 70

    # Malicious actor tampers with SQLite organisations table directly
    with db.conn:
        db.conn.execute("UPDATE organisations SET treasury_balance = 999999 WHERE id = 'org-sql-truth'")

    # When loaded with authoritative ledger reference, balance is 70, NOT 999999
    loaded_org = repo.load_organisation("org-sql-truth", ledger=ledger)
    assert loaded_org is not None
    assert loaded_org.treasury_balance == 70

def test_conservation_of_credits_across_transfers(tmp_path):
    db_file = str(tmp_path / "conservation.db")
    db = Database(db_file)
    ledger = SqliteLedger(db, initial_treasury=200)

    assert ledger.verify_conservation() is True

    ledger.transfer(TREASURY, "agent-01", 50, "Allocation to agent-01")
    ledger.transfer("agent-01", "agent-02", 20, "Delegation to agent-02")
    ledger.transfer("agent-02", EXTERNAL_SINK, 15, "API consumption fee")

    assert ledger.get_balance(TREASURY) == 150
    assert ledger.get_balance("agent-01") == 30
    assert ledger.get_balance("agent-02") == 5
    assert ledger.get_balance(EXTERNAL_SINK) == 15

    # Conservation must strictly hold
    assert ledger.verify_conservation() is True
