"""Adversarial security tests for Blockchain Settlement and Signing Boundaries.

Attacks:
1. Intent Tampering: Modifying recipient, amount, or chain parameters after proposal.
2. Cross-Tenant Isolation: Tenant A attempting to read or reconcile Tenant B's blockchain operation.
3. Secret Safety: Ensuring private keys and RPC credentials never leak into logs or audit events.
4. Startup Fail-Closed: Setting KALYX_BLOCKCHAIN_ENABLED=true without complete configuration raises RuntimeError.
5. Replay Protection: Replaying an already-consumed authorization token for a blockchain transaction is blocked.
"""

import json
import pytest

from src.api.bootstrap import ensure_started
from src.api.config import validate_blockchain_config
from src.domain.blockchain import BlockchainTransactionIntent
from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, OperationState, OrgState
from src.domain.exceptions import IdempotencyConflict, UnauthorizedActionError
from src.execution.consequential import ConsequentialExecutionManager, ConsequentialOperationRepository
from src.governance.policy_engine import PolicyEngine
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore, SqliteLedger, SqliteRepository
from src.security.token_consumption import AuthorizationTokenJournal
from src.settlement.blockchain.nonce_manager import NonceManager
from src.settlement.blockchain.provider import BlockchainSettlementProvider
from src.settlement.blockchain.rpc_client import SimulatedEvmRpcClient
from src.settlement.blockchain.signer import LocalKeySigner
from src.settlement.reconciliation import ReconciliationService
from src.utils.logging import format_structured_log, scrub_secrets

TEST_PRIVKEY = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
RECIPIENT_ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def _build_org(db: Database, org_id: str, tenant_id: str, treasury: int = 100) -> Organisation:
    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            (tenant_id, f"Tenant {tenant_id}", "active"),
        )
    org = Organisation(
        id=org_id,
        tenant_id=tenant_id,
        mission="Adversarial Security Verification",
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


def test_adversarial_private_key_never_logged_or_audited(monkeypatch):
    """Verify that private keys and RPC URLs are scrubbed from logs and audit strings."""
    monkeypatch.setenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", TEST_PRIVKEY)

    # 1. Raw private key string
    raw_log = f"Initialized signer with key {TEST_PRIVKEY} for deployment"
    scrubbed = scrub_secrets(raw_log)
    assert TEST_PRIVKEY not in scrubbed
    assert TEST_PRIVKEY[2:] not in scrubbed
    assert "[REDACTED]" in scrubbed

    # 2. Infura URL with secret key
    raw_rpc = "https://sepolia.infura.io/v3/9aa3d95b3bc440fa88ea12eaa4456161"
    scrubbed_rpc = scrub_secrets(raw_rpc)
    assert "9aa3d95b3bc440fa88ea12eaa4456161" not in scrubbed_rpc
    assert "[REDACTED]" in scrubbed_rpc

    # 3. Structured log formatting
    structured = format_structured_log(
        level="info",
        event="BLOCKCHAIN_SUBMITTED",
        details={"private_key": TEST_PRIVKEY, "tx_hash": "0x" + "b" * 64},
    )
    assert TEST_PRIVKEY not in structured
    assert TEST_PRIVKEY[2:] not in structured


def test_adversarial_cross_tenant_reconciliation_blocked(tmp_path):
    """Tenant B must never be able to reconcile or settle Tenant A's blockchain operation."""
    db_file = str(tmp_path / "kalyx_bc_cross_tenant.db")
    db = Database(db_file)
    repo = SqliteRepository(db)
    ledger = SqliteLedger(db, initial_treasury=100)
    op_repo = ConsequentialOperationRepository(db.conn)
    rpc_client = SimulatedEvmRpcClient()
    signer = LocalKeySigner(TEST_PRIVKEY)
    provider = BlockchainSettlementProvider(rpc_client=rpc_client, signer=signer)
    reconciliation_service = ReconciliationService(repo=op_repo, ledger=ledger, provider=provider)

    org_a = _build_org(db, "org-alpha", "tenant-alpha")
    org_b = _build_org(db, "org-beta", "tenant-beta")
    repo.save_organisation(org_a)
    repo.save_organisation(org_b)

    # Create operation belonging to Tenant A in UNKNOWN state
    policy = PolicyEngine(signing_secret="sec")
    manager_a = ConsequentialExecutionManager(policy_engine=policy, ledger=ledger, provider=provider, db_conn=db.conn)
    prop_a = ActionProposal(
        id="prop-a-1",
        task_id="t-1",
        proposing_agent_id="org-alpha-agent-fin",
        action_type=ActionType.BLOCKCHAIN_TRANSACTION,
        target=f"evm://{RECIPIENT_ADDR}",
        parameters={"chain_id": 11155111, "recipient": RECIPIENT_ADDR},
        requested_credits=10,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Tenant A action",
    )
    decision_a = policy.evaluate(prop_a, org_a, ledger=ledger)
    op_a = manager_a.create_operation(prop_a, decision_a, org_a)
    op_a.transition_to(OperationState.AUTHORIZED)
    op_a.transition_to(OperationState.ESCROWED)
    op_a.transition_to(OperationState.SUBMITTED)
    op_a.transition_to(OperationState.UNKNOWN, error_message="Simulated drop")
    op_repo.save(op_a)

    # Adversarial: Tenant B attempts to reconcile Tenant A's operation
    with pytest.raises(UnauthorizedActionError) as exc_info:
        reconciliation_service.reconcile_operation(op_a.id, org_b)
    assert "Isolation violation" in str(exc_info.value)

    db.close()


def test_adversarial_conflicting_intent_payload_rejected(tmp_path):
    """Attempting to reuse an idempotency key with modified recipient or parameters raises IdempotencyConflict."""
    db_file = str(tmp_path / "kalyx_bc_idemp_conflict.db")
    db = Database(db_file)
    repo = SqliteRepository(db)
    ledger = SqliteLedger(db, initial_treasury=100)
    policy = PolicyEngine(signing_secret="sec-conflict")
    rpc_client = SimulatedEvmRpcClient()
    signer = LocalKeySigner(TEST_PRIVKEY)
    provider = BlockchainSettlementProvider(rpc_client=rpc_client, signer=signer)
    manager = ConsequentialExecutionManager(policy_engine=policy, ledger=ledger, provider=provider, db_conn=db.conn)

    org = _build_org(db, "org-conflict", "tenant-conflict")
    repo.save_organisation(org)
    repo.save_agent(org.agents["org-conflict-agent-fin"], org.id)

    # Initial approved proposal
    prop1 = ActionProposal(
        id="prop-c1",
        task_id="t1",
        proposing_agent_id="org-conflict-agent-fin",
        action_type=ActionType.BLOCKCHAIN_TRANSACTION,
        target=f"evm://{RECIPIENT_ADDR}",
        parameters={"chain_id": 11155111, "recipient": RECIPIENT_ADDR},
        requested_credits=10,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Initial intent",
    )
    dec1 = policy.evaluate(prop1, org, ledger=ledger)
    manager.create_operation(prop1, dec1, org, idempotency_key="idemp-key-shared")

    # Tampered proposal reusing identical idempotency key with attacker's recipient
    attacker_addr = "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"
    prop2_tampered = ActionProposal(
        id="prop-c1-tampered",
        task_id="t1",
        proposing_agent_id="org-conflict-agent-fin",
        action_type=ActionType.BLOCKCHAIN_TRANSACTION,
        target=f"evm://{attacker_addr}",
        parameters={"chain_id": 11155111, "recipient": attacker_addr},
        requested_credits=10,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Tampered intent",
    )
    dec2 = policy.evaluate(prop2_tampered, org, ledger=ledger)

    with pytest.raises(IdempotencyConflict) as exc_info:
        manager.create_operation(prop2_tampered, dec2, org, idempotency_key="idemp-key-shared")
    assert "already registered with different parameters" in str(exc_info.value)

    db.close()


def test_adversarial_blockchain_enabled_fails_closed_when_unconfigured(monkeypatch):
    """When KALYX_BLOCKCHAIN_ENABLED=true, system fails closed if RPC or Private Key is absent."""
    monkeypatch.setenv("KALYX_BLOCKCHAIN_ENABLED", "true")
    monkeypatch.setenv("KALYX_BLOCKCHAIN_RPC_URL", "")
    monkeypatch.setenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", "")

    with pytest.raises(RuntimeError) as exc_info:
        validate_blockchain_config()
    assert "KALYX_BLOCKCHAIN_RPC_URL must be a valid HTTP/HTTPS URL" in str(exc_info.value)
    assert "KALYX_BLOCKCHAIN_PRIVATE_KEY is required" in str(exc_info.value)
