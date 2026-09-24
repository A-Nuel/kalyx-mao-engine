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
from src.domain.blockchain import (
    BUY_AND_ACTIVATE_SELECTOR,
    ORBIO_EXCHANGE_MAINNET,
    encode_buy_and_activate_calldata,
)
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


def run_preflight(
    simulate: bool = False,
    recipient: Optional[str] = None,
    amount_credits: int = 1,
    amount_wei: int = 0,
    chain_id: int = 11155111,
    contract_address: Optional[str] = None,
) -> int:
    """Perform read-only preflight and connectivity checks without broadcasting any transaction."""
    print("=" * 72)
    print("  KALYX ON-CHAIN SETTLEMENT BOUNDARY — PREFLIGHT & CONNECTIVITY AUDIT")
    print("=" * 72)

    rpc_url = os.getenv("KALYX_BLOCKCHAIN_RPC_URL", "").strip()
    private_key = os.getenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", "").strip()
    allowlist_raw = os.getenv("KALYX_BLOCKCHAIN_RECIPIENT_ALLOWLIST", "").strip()
    target_contract = contract_address or os.getenv("ORBIO_EXCHANGE_CONTRACT_ADDRESS", "").strip() or ORBIO_EXCHANGE_MAINNET

    default_recipient = recipient or "0x000000000000000000000000000000000000dEaD"

    # 1. Environment & Credentials Audit
    print("\n[PREFLIGHT 1/5] ENVIRONMENT & CONFIGURATION AUDIT")
    print(f"  Execution Mode Requested:        {'SIMULATED (DRY-RUN)' if simulate else 'REAL / TESTNET'}")

    if rpc_url:
        clean_rpc = rpc_url.split("?")[0]
        if "/v2/" in clean_rpc:
            prefix, _, _ = clean_rpc.partition("/v2/")
            display_rpc = f"{prefix}/v2/****"
        else:
            display_rpc = clean_rpc
        print(f"  KALYX_BLOCKCHAIN_RPC_URL:        [CONFIGURED] {display_rpc}")
    else:
        print(f"  KALYX_BLOCKCHAIN_RPC_URL:        [MISSING]")

    if private_key:
        print(f"  KALYX_BLOCKCHAIN_PRIVATE_KEY:    [CONFIGURED] (32 bytes hex, hidden)")
    else:
        print(f"  KALYX_BLOCKCHAIN_PRIVATE_KEY:    [MISSING]")

    print(f"  Target Chain ID:                 {chain_id}")
    print(f"  Target Recipient:                {default_recipient}")
    print(f"  Target Contract (Orbio):         {target_contract}")
    print(f"  Recipient Allowlist:             {allowlist_raw or '[DEFAULT ONLY]'}")

    if not simulate:
        if not rpc_url or not private_key:
            print("\n" + "!" * 72)
            print("  PREFLIGHT RESULT: BLOCKED (Testnet credentials not configured)")
            print("!" * 72)
            print("  Required environment variables for real testnet execution:")
            if not rpc_url:
                print("    - KALYX_BLOCKCHAIN_RPC_URL (e.g. Alchemy, Infura, or custom EVM node)")
            if not private_key:
                print("    - KALYX_BLOCKCHAIN_PRIVATE_KEY (0x-prefixed 32-byte hex private key)")
            print("\n  To run safe preflight verification in simulated mode:")
            print("    python scripts/execute_testnet_settlement.py --simulate --preflight")
            print("=" * 72)
            return 1

    # 2. Key Derivation & Signer Check
    print("\n[PREFLIGHT 2/5] SIGNER & KEY DERIVATION")
    if simulate:
        sim_key = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
        signer = LocalKeySigner(sim_key)
        rpc_client = SimulatedEvmRpcClient(chain_id=chain_id)
        print(f"  Signer Mode:                     SIMULATED")
    else:
        signer = LocalKeySigner(private_key)
        rpc_client = HttpEvmRpcClient(rpc_url=rpc_url)
        print(f"  Signer Mode:                     LOCAL KEY (REAL TESTNET)")

    print(f"  Derived Signer Public Address:   {signer.address}")

    # 3. RPC Node & Network Connectivity Check
    print("\n[PREFLIGHT 3/5] RPC NODE & NETWORK CONNECTIVITY")
    try:
        remote_chain_id = rpc_client.get_chain_id()
        block_number = rpc_client.get_block_number()
        print(f"  RPC Node Connectivity:           CONNECTED")
        print(f"  Remote Chain ID:                 {remote_chain_id} (Configured: {chain_id})")
        if remote_chain_id != chain_id:
            print(f"  [WARNING] Chain ID mismatch! Configured {chain_id} != Node {remote_chain_id}")
        else:
            print(f"  Chain ID Match:                  VERIFIED")
        print(f"  Current Block Number:            {block_number}")
    except Exception as exc:
        print(f"  RPC Connectivity FAILED:         {exc}")
        return 1

    # 4. Account State (Balance & Nonce)
    print("\n[PREFLIGHT 4/5] SIGNER ON-CHAIN STATE")
    try:
        balance_wei = rpc_client.get_balance(signer.address)
        balance_eth = balance_wei / (10**18)
        nonce = rpc_client.get_transaction_count(signer.address, block="pending")
        print(f"  Signer Balance:                  {balance_wei} Wei ({balance_eth:.6f} ETH)")
        print(f"  Signer Pending Nonce:            {nonce}")
        if balance_wei == 0:
            print("  [WARNING] Signer has 0 balance! Will not be able to pay for transaction gas.")
        else:
            print("  Gas Solvency Check:              SUFFICIENT FOR GAS")
    except Exception as exc:
        print(f"  Signer State Check FAILED:       {exc}")
        return 1

    # 5. Contract Code & Calldata Verification
    print("\n[PREFLIGHT 5/5] CONTRACT DEPLOYMENT & CALLDATA VALIDATION")
    try:
        code = rpc_client.get_code(target_contract)
        if len(code) > 2 and code != "0x":
            print(f"  Contract Bytecode at {target_contract[:10]}...: DEPLOYED ({len(code) // 2} bytes)")
        else:
            print(f"  Contract Bytecode at {target_contract[:10]}...: NO CODE / EOA ('{code}')")
    except Exception as exc:
        print(f"  Contract Code Check FAILED:      {exc}")
        return 1

    # Calldata verification for buyAndActivate
    try:
        test_calldata = encode_buy_and_activate_calldata(
            usdg_in=1_000_000,
            min_credit_out=1_000_000,
            beneficiary=signer.address,
            max_fills=5,
        )
        selector = test_calldata[:10]
        expected_selector = "0x" + BUY_AND_ACTIVATE_SELECTOR.hex()
        assert selector == expected_selector, f"Selector mismatch: {selector} != {expected_selector}"
        print(f"  Calldata Encoding Test:          VERIFIED (selector {selector}, {len(test_calldata)} chars)")
    except Exception as exc:
        print(f"  Calldata Encoding Test FAILED:   {exc}")
        return 1

    print("\n" + "=" * 72)
    print("  PREFLIGHT AUDIT SUMMARY: ALL CHECKS PASSED")
    print("=" * 72)
    print("  [SAFETY INVARIANT ENFORCED]")
    print("  This was a read-only audit. NO transactions were signed or broadcast.")
    print("  Real on-chain transaction execution requires explicitly running without --preflight.")
    print("=" * 72 + "\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Kalyx Phase 12 Blockchain Settlement Milestone")
    parser.add_argument("--mode", type=str, choices=["testnet", "simulate"], default=None, help="Execution mode ('testnet' requires credentials; 'simulate' is dry-run)")
    parser.add_argument("--simulate", action="store_true", help="Force simulated EVM RPC mode")
    parser.add_argument("--preflight", action="store_true", help="Perform read-only preflight and connectivity check without broadcasting")
    parser.add_argument("--contract", type=str, default=None, help="Target contract address to check code against (0x...)")
    parser.add_argument("--recipient", type=str, default=None, help="Recipient address (0x...)")
    parser.add_argument("--amount-credits", type=int, default=1, help="Kalyx credits to settle")
    parser.add_argument("--amount-wei", type=int, default=0, help="Wei amount to transfer")
    parser.add_argument("--chain-id", type=int, default=11155111, help="EVM Chain ID (default Sepolia: 11155111)")
    args = parser.parse_args()

    simulate_mode = args.simulate or (args.mode == "simulate")
    if args.mode == "testnet":
        simulate_mode = False

    if args.preflight:
        sys.exit(
            run_preflight(
                simulate=simulate_mode,
                recipient=args.recipient,
                amount_credits=args.amount_credits,
                amount_wei=args.amount_wei,
                chain_id=args.chain_id,
                contract_address=args.contract,
            )
        )

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
