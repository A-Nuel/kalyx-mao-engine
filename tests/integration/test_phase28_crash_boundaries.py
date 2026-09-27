"""Phase 28 crash-boundary regression tests."""

import hashlib

from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, OperationState, PolicyResult
from src.domain.exceptions import ExternalExecutionError
from src.economy.ledger import ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.consequential import ConsequentialExecutionManager, ConsequentialOperationRepository
from src.governance.policy_engine import PolicyEngine
from src.persistence.database import Database
from src.persistence.repositories import SqliteLedger, SqliteRepository
from src.settlement.reconciliation import ReconciliationService
from src.settlement.simulated_provider import SimulatedConsequentialProvider


def _org(org_id: str, treasury: int = 100) -> Organisation:
    organisation = Organisation(
        id=org_id,
        tenant_id="tenant-demo",
        mission="Crash boundary test",
        treasury_balance=treasury,
    )
    organisation.agents["agent-fin"] = AgentRecord(
        id="agent-fin",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=100,
        allowed_action_types=[ActionType.EXTERNAL_API_CALL],
    )
    return organisation


def _proposal(proposal_id: str, amount: int = 25) -> ActionProposal:
    return ActionProposal(
        id=proposal_id,
        task_id="task-crash-boundary",
        proposing_agent_id="agent-fin",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="sandbox://crash-boundary",
        parameters={"kind": "crash-boundary"},
        requested_credits=amount,
        expected_value_score=0.9,
        risk_assessment="controlled-test",
        rationale="Exercise a durable crash boundary",
    )


def test_restart_recovers_if_process_dies_after_escrow_transfer(tmp_path):
    db_path = str(tmp_path / "escrow-crash.db")
    secret = "phase28-escrow-crash-secret"
    provider = SimulatedConsequentialProvider()

    db_a = Database(db_path)
    repo_a = SqliteRepository(db_a)
    ledger_a = SqliteLedger(db_a, initial_treasury=100)
    policy_a = PolicyEngine(signing_secret=secret)
    org_a = _org("org-escrow-crash")
    repo_a.save_organisation(org_a)
    repo_a.save_agent(org_a.agents["agent-fin"], org_a.id)

    manager_a = ConsequentialExecutionManager(
        policy_engine=policy_a,
        ledger=ledger_a,
        provider=provider,
        db_conn=db_a.conn,
    )
    proposal = _proposal("proposal-escrow-crash")
    decision = policy_a.evaluate(proposal, org_a, ledger=ledger_a)
    operation = manager_a.create_operation(proposal, decision, org_a)
    manager_a.authorize_operation(operation, proposal, decision, org_a)

    # Simulate the exact crash window: the ledger transfer commits, but the
    # operation state has not yet advanced from AUTHORIZED to ESCROWED.
    escrow_tx = f"res-{hashlib.sha256(operation.id.encode('utf-8')).hexdigest()[:16]}"
    ledger_a.transfer(
        from_account=TREASURY,
        to_account=ESCROW,
        amount=operation.amount,
        memo=f"Escrow lock for consequential operation {operation.id}",
        transaction_id=escrow_tx,
    )
    assert operation.state == OperationState.AUTHORIZED
    assert ledger_a.get_balance(TREASURY) == 75
    assert ledger_a.get_balance(ESCROW) == 25
    db_a.close()

    # Restart. execute_proposal must recognize the durable escrow transaction
    # and continue instead of attempting a duplicate transfer.
    db_b = Database(db_path)
    repo_b = SqliteRepository(db_b)
    ledger_b = SqliteLedger(db_b, initial_treasury=0)
    org_b = repo_b.load_organisation(org_a.id, ledger=ledger_b)
    assert org_b is not None

    manager_b = ConsequentialExecutionManager(
        policy_engine=PolicyEngine(signing_secret=secret),
        ledger=ledger_b,
        provider=provider,
        db_conn=db_b.conn,
    )
    recovered = manager_b.execute_proposal(proposal, decision, org_b)

    assert recovered is not None
    assert ledger_b.get_balance(TREASURY) == 75
    assert ledger_b.get_balance(ESCROW) == 0
    assert ledger_b.get_balance(EXTERNAL_SINK) == 25
    assert ledger_b.verify_conservation()
    db_b.close()


def test_reconciliation_does_not_double_settle_after_settlement_crash(tmp_path):
    db_path = str(tmp_path / "settlement-crash.db")
    secret = "phase28-settlement-crash-secret"
    provider = SimulatedConsequentialProvider()

    db = Database(db_path)
    repo = SqliteRepository(db)
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret=secret)
    org = _org("org-settlement-crash")
    repo.save_organisation(org)
    repo.save_agent(org.agents["agent-fin"], org.id)

    manager = ConsequentialExecutionManager(
        policy_engine=policy,
        ledger=ledger,
        provider=provider,
        db_conn=db.conn,
    )
    proposal = _proposal("proposal-settlement-crash")
    decision = policy.evaluate(proposal, org, ledger=ledger)
    operation = manager.create_operation(proposal, decision, org)
    manager.authorize_operation(operation, proposal, decision, org)
    manager.escrow_operation(operation, org)

    # Persist SUBMITTED before the simulated external success.
    operation.transition_to(OperationState.SUBMITTED)
    manager.repo.save(operation)

    provider.record_external_execution(
        idempotency_key=operation.idempotency_key,
        operation_id=operation.id,
        amount=operation.amount,
        target=operation.target,
        outcome=__import__("src.domain.enums", fromlist=["ProviderOutcome"]).ProviderOutcome.SUCCESS,
    )

    settlement_tx = f"tx-{hashlib.sha256((decision.authorization_token or operation.id).encode('utf-8')).hexdigest()[:16]}"
    ledger.transfer(
        from_account=ESCROW,
        to_account=EXTERNAL_SINK,
        amount=operation.amount,
        memo=f"Settlement commit for consequential operation {operation.id}",
        transaction_id=settlement_tx,
    )
    db.close()

    # Restart with the durable operation still SUBMITTED. Reconciliation must
    # observe the provider success but must not move the credits a second time.
    db_b = Database(db_path)
    repo_b = ConsequentialOperationRepository(db_b.conn)
    ledger_b = SqliteLedger(db_b, initial_treasury=0)
    repo_org_b = SqliteRepository(db_b)
    org_b = repo_org_b.load_organisation(org.id, ledger=ledger_b)
    assert org_b is not None
    recovered = repo_b.get(operation.id)
    assert recovered is not None
    assert recovered.state == OperationState.SUBMITTED

    reconciliation = ReconciliationService(
        repo=repo_b,
        ledger=ledger_b,
        provider=provider,
    )
    resolved = reconciliation.reconcile_operation(recovered.id, org_b)

    assert resolved.state == OperationState.RECONCILED
    assert ledger_b.get_balance(TREASURY) == 75
    assert ledger_b.get_balance(ESCROW) == 0
    assert ledger_b.get_balance(EXTERNAL_SINK) == 25
    assert ledger_b.verify_conservation()
    db_b.close()
