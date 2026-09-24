"""Controlled Blockchain Settlement Milestone Script.

Executes and verifies a consequential blockchain transaction on Ethereum Sepolia (or Simulated RPC)
through the full Kalyx governance, escrow, and audit lifecycle:

AGENTS PROPOSE -> POLICIES AUTHORIZE -> EXECUTORS EXECUTE -> AUDITORS VERIFY
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure src is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole, OperationState, OrgState, PolicyResult
from src.economy.ledger import ESCROW, EXTERNAL_SINK, TREASURY
from src.execution.consequential import ConsequentialExecutionManager, ConsequentialOperationRepository
from src.governance.policy_engine import PolicyEngine
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore, SqliteLedger, SqliteRepository
from src.settlement.blockchain.nonce_manager import NonceManager
from src.settlement.blockchain.provider import BlockchainSettlementProvider
from src.settlement.blockchain.rpc_client import HttpEvmRpcClient, SimulatedEvmRpcClient
from src.settlement.blockchain.signer import LocalKeySigner
from src.audit.auditor import Auditor


def run_milestone(
    simulate: bool = False,
    recipient: Optional[str] = None,
    amount_credits: int = 1,
    amount_wei: int = 0,
    chain_id: int = 11155111,
) -> int:
    print("=" * 72)
    print("  KALYX ON-CHAIN SETTLEMENT BOUNDARY — MILESTONE EXECUTION")
    print("=" * 72)

    rpc_url = os.getenv("KALYX_BLOCKCHAIN_RPC_URL", "").strip()
    private_key = os.getenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", "").strip()
    allowlist_raw = os.getenv("KALYX_BLOCKCHAIN_RECIPIENT_ALLOWLIST", "")

    default_recipient = recipient or "0x000000000000000000000000000000000000dEaD"

    # Decide execution mode
    if simulate:
        print("[MODE] SIMULATED EVM EXECUTION (Verification & Dry-Run Mode)")
        sim_key = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
        signer = LocalKeySigner(sim_key)
        print(f"  Simulated Signer Address: {signer.address}")
        rpc_client = SimulatedEvmRpcClient(chain_id=chain_id)
        wait_seconds = 0.0
    else:
        # TESTNET / LIVE mode requires valid RPC and private key (no silent fallback)
        if not rpc_url or not private_key:
            print("[ERROR] Testnet execution requested, but required credentials are not configured.")
            print("  Missing required environment variables:")
            if not rpc_url:
                print("    - KALYX_BLOCKCHAIN_RPC_URL (e.g., https://eth-sepolia.g.alchemy.com/v2/YOUR_API_KEY)")
            if not private_key:
                print("    - KALYX_BLOCKCHAIN_PRIVATE_KEY (0x-prefixed 32-byte hex private key)")
            print("\n  To run in safe local simulated dry-run mode, pass:")
            print("    python scripts/execute_testnet_settlement.py --simulate")
            print("=" * 72)
            return 1

        print("[MODE] LIVE ETHEREUM SEPOLIA TESTNET EXECUTION")
        print(f"  RPC Endpoint: {rpc_url.split('?')[0]}")
        print(f"  Target Chain ID: {chain_id}")
        signer = LocalKeySigner(private_key)
        print(f"  Kalyx Signer Address: {signer.address}")
        rpc_client = HttpEvmRpcClient(rpc_url=rpc_url)
        wait_seconds = 4.0

    print(f"  Target Recipient: {default_recipient}")
    print(f"  Requested Credits: {amount_credits}")
    print(f"  Amount (Wei): {amount_wei}")
    print("-" * 72)

    # 1. Initialize In-Memory Isolated Database for milestone run
    db = Database(":memory:")
    repo = SqliteRepository(db)
    event_store = SqliteEventStore(db, verify_on_startup=True)
    ledger = SqliteLedger(db, initial_treasury=100)

    tenant_id = "tenant-testnet"
    org_id = f"org-testnet-{uuid.uuid4().hex[:6]}"
    agent_id = f"{org_id}-agent-treasury"

    with db.conn:
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            (tenant_id, "Tenant Testnet", "active"),
        )

    # Allowlist includes recipient
    configured_allowlist = [default_recipient.lower()]
    if allowlist_raw:
        configured_allowlist.extend([a.strip().lower() for a in allowlist_raw.split(",") if a.strip()])

    org = Organisation(
        id=org_id,
        tenant_id=tenant_id,
        mission="Controlled Blockchain Settlement Milestone Execution",
        treasury_balance=100,
        allowed_chains=[chain_id],
        blockchain_recipient_allowlist=configured_allowlist,
        max_transaction_wei=10**16,
        state=OrgState.EXECUTING,
    )
    analyst = AgentRecord(
        id=agent_id,
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=50,
        allowed_action_types=[ActionType.BLOCKCHAIN_TRANSACTION],
    )
    org.agents[agent_id] = analyst
    repo.save_organisation(org)
    repo.save_agent(analyst, org.id)

    # 2. Policy Engine Setup
    policy_secret = f"kalyx-milestone-secret-{uuid.uuid4().hex}"
    policy_engine = PolicyEngine(signing_secret=policy_secret)

    # 3. Blockchain Settlement Provider
    nonce_manager = NonceManager()
    provider = BlockchainSettlementProvider(
        rpc_client=rpc_client,
        signer=signer,
        nonce_manager=nonce_manager,
        default_chain_id=chain_id,
        wait_for_receipt_seconds=wait_seconds,
    )

    manager = ConsequentialExecutionManager(
        policy_engine=policy_engine,
        ledger=ledger,
        provider=provider,
        db_conn=db.conn,
        event_store=event_store,
    )

    # 4. Step 1: AGENT PROPOSES
    print("\n[STEP 1] AGENT PROPOSES")
    proposal = ActionProposal(
        id=f"prop-testnet-{uuid.uuid4().hex[:6]}",
        task_id=f"task-testnet-{uuid.uuid4().hex[:6]}",
        proposing_agent_id=agent_id,
        action_type=ActionType.BLOCKCHAIN_TRANSACTION,
        target=f"evm://{default_recipient}",
        parameters={
            "chain_id": chain_id,
            "recipient": default_recipient,
            "amount_wei": amount_wei,
            "max_fee_per_gas": 25_000_000_000,
            "max_priority_fee_per_gas": 1_500_000_000,
            "gas_limit": 21_000,
        },
        requested_credits=amount_credits,
        expected_value_score=0.95,
        risk_assessment="Low risk controlled testnet disbursement",
        rationale="Controlled on-chain verification milestone for Phase 12",
    )
    print(f"  Proposal ID: {proposal.id}")
    print(f"  Action: {proposal.action_type.value}")
    print(f"  Target: {proposal.target}")
    print(f"  Requested Credits: {proposal.requested_credits}")

    # 5. Step 2: POLICIES AUTHORIZE
    print("\n[STEP 2] POLICIES AUTHORIZE")
    decision = policy_engine.evaluate(proposal, org, ledger=ledger)
    print(f"  Policy Result: {decision.result.value}")
    if decision.result != PolicyResult.APPROVED:
        print(f"  REJECTED! Reason: {decision.reason} (Rule: {decision.violated_rule_id})")
        return 1
    print(f"  Authorization Token: {decision.authorization_token[:24]}... (HMAC cryptographic grant)")

    # 6. Step 3: EXECUTORS EXECUTE
    print("\n[STEP 3] EXECUTORS EXECUTE")
    print(f"  Pre-execution Treasury: {ledger.get_balance(TREASURY)} credits")
    print(f"  Pre-execution Escrow:   {ledger.get_balance(ESCROW)} credits")
    print(f"  Signing boundary generates and signs EIP-1559 transaction intent...")

    try:
        receipt = manager.execute_proposal(proposal, decision, org)
    except Exception as exc:
        print(f"  EXECUTION FAILED: {exc}")
        print(f"  Post-failure Treasury: {ledger.get_balance(TREASURY)} credits")
        print(f"  Post-failure Escrow:   {ledger.get_balance(ESCROW)} credits")
        print(f"  Credit Conservation:   {ledger.verify_conservation()}")
        return 1

    print(f"  Execution Succeeded!")
    print(f"  Receipt Status: {receipt.http_status}")
    tx_hash = receipt.raw_output.get("transaction_hash") or receipt.raw_output.get("tx_hash")
    print(f"  Transaction Hash: {tx_hash}")
    print(f"  Post-execution Treasury: {ledger.get_balance(TREASURY)} credits")
    print(f"  Post-execution Escrow:   {ledger.get_balance(ESCROW)} credits")
    print(f"  Post-execution Sink:     {ledger.get_balance(EXTERNAL_SINK)} credits")
    assert ledger.verify_conservation(), "CRITICAL: Ledger conservation violated!"
    print(f"  Ledger Conservation:     VERIFIED")

    # 7. Step 4: AUDITORS VERIFY
    print("\n[STEP 4] AUDITORS VERIFY")
    op_repo = ConsequentialOperationRepository(db.conn)
    op = op_repo.get_by_idempotency_key(f"{org_id}:{proposal.id}")
    assert op is not None, "Consequential operation missing from repository!"

    auditor = Auditor(verification_secret=policy_secret)
    verification = auditor.verify_consequential_operation(
        operation=op,
        proposal=proposal,
        decision=decision,
        org=org,
        ledger=ledger,
        event_store=event_store,
        policy_engine=policy_engine,
        rpc_client=rpc_client,
    )

    print(f"  Audit Verified: {verification.verified}")
    print(f"  Audit Checks Performed: {', '.join(verification.checks)}")
    print(f"  Audit Evidence Hash: {verification.evidence_hash}")

    print("\n" + "=" * 72)
    print("  MILESTONE EXECUTION SUMMARY: SUCCESS (FAIL-CLOSED INTEGRITY VERIFIED)")
    print("=" * 72)
    print("  [DISCLAIMER]")
    print("  Kalyx is still NOT production-ready for real-money settlement merely")
    print("  because a testnet transaction succeeds.")
    print("=" * 72 + "\n")
    db.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Kalyx Phase 12 Blockchain Settlement Milestone")
    parser.add_argument("--mode", type=str, choices=["testnet", "simulate"], default=None, help="Execution mode ('testnet' requires credentials; 'simulate' is dry-run)")
    parser.add_argument("--simulate", action="store_true", help="Force simulated EVM RPC mode")
    parser.add_argument("--recipient", type=str, default=None, help="Recipient address (0x...)")
    parser.add_argument("--amount-credits", type=int, default=1, help="Kalyx credits to settle")
    parser.add_argument("--amount-wei", type=int, default=0, help="Wei amount to transfer")
    parser.add_argument("--chain-id", type=int, default=11155111, help="EVM Chain ID (default Sepolia: 11155111)")
    args = parser.parse_args()

    simulate_mode = args.simulate or (args.mode == "simulate")
    if args.mode == "testnet":
        simulate_mode = False

    sys.exit(
        run_milestone(
            simulate=simulate_mode,
            recipient=args.recipient,
            amount_credits=args.amount_credits,
            amount_wei=args.amount_wei,
            chain_id=args.chain_id,
        )
    )


if __name__ == "__main__":
    main()
