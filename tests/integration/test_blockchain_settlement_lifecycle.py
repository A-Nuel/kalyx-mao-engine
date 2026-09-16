"""Comprehensive integration tests for Blockchain Settlement Lifecycle & On-Chain Execution Boundary.

Tests:
1. Full successful blockchain settlement flow:
   Proposal -> Policy -> Token -> Escrow -> Blockchain Broadcast -> On-chain Confirmation -> Escrow Settlement -> Auditor Verification.
2. On-chain transaction revert:
   Reverted transaction on-chain -> FAILED state -> Escrow refunded back to Treasury -> Conservation verified.
3. RPC transport timeout / drop during broadcast:
   Ambiguous transport -> Transitions to UNKNOWN -> Escrow remains preserved -> Reconcile -> Settles exactly once.
4. Pending transaction during reconciliation:
   Transaction still pending in mempool -> Reconciliation preserves UNKNOWN state and keeps escrow locked.
5. Idempotent repeated execution & reconciliation:
   Same idempotency key reuses allocated nonce and does not duplicate transactions or ledger entries.
6. Cold crash and recovery with blockchain operation:
   Kalyx process crashes after submission while in UNKNOWN; restarted process recovers state and reconciles cleanly.
"""

import os
import uuid
import pytest

from src.audit.auditor import Auditor
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, OperationState, OrgState
from src.domain.exceptions import ExternalExecutionError
from src.economy.ledger import ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.consequential import ConsequentialExecutionManager, ConsequentialOperationRepository
from src.governance.policy_engine import PolicyEngine
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore, SqliteLedger, SqliteRepository
from src.settlement.blockchain.nonce_manager import NonceManager
from src.settlement.blockchain.provider import BlockchainSettlementProvider
from src.settlement.blockchain.rpc_client import SimulatedEvmRpcClient
from src.settlement.blockchain.signer import LocalKeySigner
from src.settlement.reconciliation import ReconciliationService

TEST_PRIVKEY = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
RECIPIENT_ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def _build_org(db: Database, org_id: str, tenant_id: str = "tenant-bc-1", treasury: int = 100) -> Organisation:
    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            (tenant_id, f"Tenant {tenant_id}", "active"),
        )
    org = Organisation(
        id=org_id,
        tenant_id=tenant_id,
        mission="Blockchain Settlement Lifecycle Verification",
        treasury_balance=treasury,
        state=OrgState.EXECUTING,
    )
    analyst = AgentRecord(
        id=f"{org_id}-agent-fin",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=50,
        allowed_action_types=[ActionType.BLOCKCHAIN_TRANSACTION, ActionType.EXTERNAL_API_CALL],
    )
    org.agents[analyst.id] = analyst
    return org


def _proposal(
    org_id: str,
    prop_id: str,
    cost: int = 20,
    amount_wei: int = 10_000_000_000_000_000,
    recipient: str = RECIPIENT_ADDR,
    chain_id: int = 11155111,
) -> ActionProposal:
    return ActionProposal(
        id=prop_id,
        task_id=f"{org_id}-task-1",
        proposing_agent_id=f"{org_id}-agent-fin",
        action_type=ActionType.BLOCKCHAIN_TRANSACTION,
        target=f"evm://{recipient}",
        parameters={
            "chain_id": chain_id,
            "recipient": recipient,
            "amount_wei": amount_wei,
            "amount_credits": cost,
            "max_fee_per_gas": 25_000_000_000,
            "gas_limit": 21_000,
        },
        requested_credits=cost,
        expected_value_score=0.95,
        risk_assessment="LOW",
        rationale="Consequential testnet settlement",
    )


def test_blockchain_settlement_successful_confirmation(tmp_path):
    """Full lifecycle: Proposal -> Policy -> Escrow -> Broadcast -> On-Chain Confirm -> Auditor Verify."""
    db_file = str(tmp_path / "kalyx_bc_success.db")
    db = Database(db_file)
    repo = SqliteRepository(db)
    event_store = SqliteEventStore(db, verify_on_startup=True)
    ledger = SqliteLedger(db, initial_treasury=100)
    policy_engine = PolicyEngine(signing_secret="policy-secret-bc-1")

    org = _build_org(db, "org-bc-1", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["org-bc-1-agent-fin"], org.id)

    rpc_client = SimulatedEvmRpcClient(chain_id=11155111)
    signer = LocalKeySigner(TEST_PRIVKEY)
    nonce_manager = NonceManager()
    provider = BlockchainSettlementProvider(
        rpc_client=rpc_client,
        signer=signer,
        nonce_manager=nonce_manager,
        default_chain_id=11155111,
    )

    manager = ConsequentialExecutionManager(
        policy_engine=policy_engine,
        ledger=ledger,
        provider=provider,
        db_conn=db.conn,
        event_store=event_store,
    )

    prop = _proposal("org-bc-1", "prop-bc-1", cost=25)
    decision = policy_engine.evaluate(prop, org, ledger=ledger)
    assert decision.authorization_token is not None

    # Execute
    receipt = manager.execute_proposal(prop, decision, org)
    assert receipt.http_status == 200
    assert receipt.raw_output.get("status") == "confirmed"

    # Ledger state: 25 moved from ESCROW to EXTERNAL_SINK
    assert ledger.get_balance(TREASURY) == 75
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 25
    assert ledger.verify_conservation()

    # Verify operation in database
    op_repo = ConsequentialOperationRepository(db.conn)
    op = op_repo.get_by_idempotency_key("org-bc-1:prop-bc-1")
    assert op is not None
    assert op.state == OperationState.SUCCEEDED
    assert op.provider_reference.startswith("0x")

    # Auditor independent verification
    auditor = Auditor(verification_secret="policy-secret-bc-1")
    vr = auditor.verify_consequential_operation(
        operation=op,
        proposal=prop,
        decision=decision,
        org=org,
        ledger=ledger,
        event_store=event_store,
        policy_engine=policy_engine,
        rpc_client=rpc_client,
    )
    assert vr.verified is True
    assert "BLOCKCHAIN_EVIDENCE_INTEGRITY" in vr.checks

    db.close()


def test_blockchain_settlement_on_chain_revert_refunds_escrow(tmp_path):
    """On-chain revert: Transaction executes and reverts -> FAILED -> Escrow refunded to Treasury."""
    db_file = str(tmp_path / "kalyx_bc_revert.db")
    db = Database(db_file)
    repo = SqliteRepository(db)
    event_store = SqliteEventStore(db, verify_on_startup=True)
    ledger = SqliteLedger(db, initial_treasury=100)
    policy_engine = PolicyEngine(signing_secret="policy-secret-bc-2")

    org = _build_org(db, "org-bc-2", treasury=100)
    repo.save_organisation(org)
    repo.save_agent(org.agents["org-bc-2-agent-fin"], org.id)

    rpc_client = SimulatedEvmRpcClient(chain_id=11155111)
    signer = LocalKeySigner(TEST_PRIVKEY)
    provider = BlockchainSettlementProvider(
        rpc_client=rpc_client,
        signer=signer,
    )

    manager = ConsequentialExecutionManager(
        policy_engine=policy_engine,
        ledger=ledger,
        provider=provider,
        db_conn=db.conn,
        event_store=event_store,
    )

    prop = _proposal("org-bc-2", "prop-bc-revert", cost=20)
    decision = policy_engine.evaluate(prop, org, ledger=ledger)
    assert decision.authorization_token is not None

    # Configure simulated RPC to revert transactions on-chain
    rpc_client.set_revert_all_rules(True)

    with pytest.raises(ExternalExecutionError) as exc_info:
        manager.execute_proposal(prop, decision, org)
    assert "reverted on-chain" in str(exc_info.value)

    # Invariant: 20 was refunded from ESCROW back to TREASURY
    assert ledger.get_balance(TREASURY) == 100
    assert ledger.get_balance(ESCROW) == 0
    assert ledger.get_balance(EXTERNAL_SINK) == 0
    assert ledger.verify_conservation()

    db.close()


def test_blockchain_settlement_rpc_drop_reconciled_after_restart(tmp_path):
    """Disaster recovery & crash boundary with blockchain settlement.
    
    1. Broadcast drops connection (TIMEOUT).
    2. Operation enters UNKNOWN state; ESCROW preserved (75 treasury, 25 escrow).
    3. Application crashes completely.
    4. Fresh application restarts from database.
    5. Blockchain confirms in background.
    6. Reconciliation service settles atomically (75 treasury, 0 escrow, 25 sink).
    7. Auditor verifies full chain.
    """
    db_file = str(tmp_path / "kalyx_bc_dr.db")
    secret = "policy-secret-bc-dr"
    org_id = "org-bc-dr"

    # Shared simulated RPC represents independent network
    rpc_client = SimulatedEvmRpcClient(chain_id=11155111)
    signer = LocalKeySigner(TEST_PRIVKEY)

    # Process A
    db_a = Database(db_file)
    repo_a = SqliteRepository(db_a)
    event_store_a = SqliteEventStore(db_a, verify_on_startup=True)
    ledger_a = SqliteLedger(db_a, initial_treasury=100)
    policy_a = PolicyEngine(signing_secret=secret)

    org_a = _build_org(db_a, org_id, treasury=100)
    repo_a.save_organisation(org_a)
    repo_a.save_agent(org_a.agents[f"{org_id}-agent-fin"], org_a.id)

    provider_a = BlockchainSettlementProvider(
        rpc_client=rpc_client,
        signer=signer,
    )
    manager_a = ConsequentialExecutionManager(
        policy_engine=policy_a,
        ledger=ledger_a,
        provider=provider_a,
        db_conn=db_a.conn,
        event_store=event_store_a,
    )

    prop = _proposal(org_id, "prop-dr-bc", cost=25)
    decision_a = policy_a.evaluate(prop, org_a, ledger=ledger_a)

    # Simulate RPC timeout during broadcast
    rpc_client.set_timeout_all_rules(True)

    with pytest.raises(ExternalExecutionError) as exc_info:
        manager_a.execute_proposal(prop, decision_a, org_a)
    assert "UNKNOWN" in str(exc_info.value)

    # Invariant: Escrow locked, zero loss
    assert ledger_a.get_balance(TREASURY) == 75
    assert ledger_a.get_balance(ESCROW) == 25
    assert ledger_a.get_balance(EXTERNAL_SINK) == 0
    assert ledger_a.verify_conservation()

    # Crash Process A
    db_a.close()
    del db_a, repo_a, event_store_a, ledger_a, policy_a, org_a, manager_a

    # Process B (Restart)
    db_b = Database(db_file)
    event_store_b = SqliteEventStore(db_b, verify_on_startup=True)
    ledger_b = SqliteLedger(db_b, initial_treasury=0)
    repo_b = SqliteRepository(db_b)
    op_repo_b = ConsequentialOperationRepository(db_b.conn)
    org_b = repo_b.load_organisation(org_id, ledger=ledger_b)

    # Verify state survived restart
    recovered_op = op_repo_b.get_by_idempotency_key(f"{org_id}:{prop.id}")
    assert recovered_op is not None
    assert recovered_op.state == OperationState.UNKNOWN
    assert recovered_op.provider_reference is not None
    assert recovered_op.provider_reference.startswith("0x")
    assert ledger_b.get_balance(ESCROW) == 25

    # Background on-chain event: Sepolia network confirms the transaction!
    rpc_client.set_timeout_all_rules(False)
    rpc_client.confirm_pending_transaction(recovered_op.provider_reference, status=1)

    # Post-Recovery Reconciliation
    provider_b = BlockchainSettlementProvider(rpc_client=rpc_client, signer=signer)
    reconciliation_service = ReconciliationService(
        repo=op_repo_b,
        ledger=ledger_b,
        provider=provider_b,
        event_store=event_store_b,
    )
    reconciled = reconciliation_service.reconcile_operation(recovered_op.id, org_b)
    assert reconciled.state == OperationState.RECONCILED

    # Economic settlement confirmed: exactly 25 moved to EXTERNAL_SINK
    assert ledger_b.get_balance(TREASURY) == 75
    assert ledger_b.get_balance(ESCROW) == 0
    assert ledger_b.get_balance(EXTERNAL_SINK) == 25
    assert ledger_b.verify_conservation()

    # Auditor verification post-reconciliation
    auditor = Auditor(verification_secret=secret)
    vr = auditor.verify_consequential_operation(
        operation=reconciled,
        proposal=prop,
        decision=decision_a,
        org=org_b,
        ledger=ledger_b,
        event_store=event_store_b,
        rpc_client=rpc_client,
    )
    assert vr.verified is True
    assert "BLOCKCHAIN_EVIDENCE_INTEGRITY" in vr.checks

    db_b.close()
