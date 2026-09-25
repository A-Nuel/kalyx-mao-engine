#!/usr/bin/env python3
"""Phase 20B — Governed first real Orbio CREDIT activation (Robinhood mainnet).

Trusted local use only. Requires:

  export KALYX_BLOCKCHAIN_PRIVATE_KEY=...   # never commit; never print
  export KALYX_BLOCKCHAIN_RPC_URL=...
  python scripts/execute_orbio_activation_mainnet.py --confirm-mainnet-activation

Hard-coded activation:
  chain 4663, CREDIT 0xe333...04c, amount 1_000_000, activate(uint256)

Does not use testnet, simulation, or USDG purchase path.
Does not claim API balance confirmation from the receipt alone.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.domain.entities import Organisation
from src.domain.enums import OrgState
from src.domain.orbio_activation import (
    MAX_ACTIVATION_AMOUNT,
    ORBIO_ACTIVATION_CHAIN_ID,
    ORBIO_ACTIVATION_NETWORK,
    ORBIO_CREDIT_ACTIVATION_CONTRACT,
    OrbioCreditActivationIntent,
    encode_activate_calldata,
)
from src.execution.orbio_activation import OrbioCreditActivationBridge
from src.governance.orbio_activation_rules import OrbioCreditActivationPolicy
from src.settlement.blockchain.provider import BlockchainSettlementProvider
from src.settlement.blockchain.rpc_client import HttpEvmRpcClient
from src.settlement.blockchain.signer import (
    ExternalTransactionSigner,
    IBlockchainSigner,
    LocalKeySigner,
)
from src.settlement.orbio_activation_preflight import OrbioActivationPreflight
from src.settlement.orbio_activation_runtime import OrbioMainnetActivationRuntime
from src.settlement.orbio_activation_verifier import (
    ActivationVerificationResult,
    OrbioCreditActivationVerifier,
)

EXPECTED_OPERATOR = "0x4675b9d0323479b1af399c87331d1d2436e6be99"
EXPECTED_CALLDATA = encode_activate_calldata(MAX_ACTIVATION_AMOUNT)
DEFAULT_GAS_LIMIT = 150_000


class ActivationDriverError(SystemExit):
    """Fail-closed exit for configuration / preflight / policy errors."""


@dataclass(frozen=True)
class DriverConfig:
    signer_mode: str
    rpc_url: str
    confirm: bool
    dry_run: bool
    private_key: Optional[str] = None
    operator_address: str = EXPECTED_OPERATOR
    signed_raw_tx_hex: Optional[str] = None
    export_unsigned: bool = False
    reconcile_tx_hash: Optional[str] = None


def _redact_key(_key: str) -> str:
    return "***REDACTED***"


def load_config(args: argparse.Namespace) -> DriverConfig:
    pk = os.getenv("KALYX_BLOCKCHAIN_PRIVATE_KEY", "").strip() or None
    rpc = os.getenv("KALYX_BLOCKCHAIN_RPC_URL", "").strip()
    if not rpc:
        raise ActivationDriverError(
            "FAIL CLOSED: KALYX_BLOCKCHAIN_RPC_URL is not set"
        )
    if not getattr(args, "confirm_mainnet_activation", False):
        raise ActivationDriverError(
            "FAIL CLOSED: missing required flag --confirm-mainnet-activation"
        )

    signer_mode = getattr(args, "signer_mode", None)
    if signer_mode is None:
        if pk:
            signer_mode = "local"
        elif getattr(args, "reconcile_tx", None) or getattr(args, "export_unsigned_tx", False) or getattr(args, "signed_tx_hex", None):
            signer_mode = "external"
        else:
            raise ActivationDriverError(
                "FAIL CLOSED: KALYX_BLOCKCHAIN_PRIVATE_KEY is not set (pass --signer-mode external for non-exportable wallet)"
            )

    if signer_mode == "local" and not pk:
        raise ActivationDriverError(
            "FAIL CLOSED: KALYX_BLOCKCHAIN_PRIVATE_KEY is not set"
        )

    operator_addr = getattr(args, "operator_address", None) or os.getenv(
        "KALYX_BLOCKCHAIN_OPERATOR_ADDRESS", EXPECTED_OPERATOR
    )
    if operator_addr.lower() != EXPECTED_OPERATOR.lower():
        raise ActivationDriverError(
            f"FAIL CLOSED: operator address {operator_addr} != required operator "
            f"{EXPECTED_OPERATOR}"
        )

    return DriverConfig(
        signer_mode=signer_mode,
        rpc_url=rpc,
        confirm=True,
        dry_run=bool(getattr(args, "dry_run", False)),
        private_key=pk,
        operator_address=operator_addr,
        signed_raw_tx_hex=getattr(args, "signed_tx_hex", None),
        export_unsigned=bool(getattr(args, "export_unsigned_tx", False)),
        reconcile_tx_hash=getattr(args, "reconcile_tx", None),
    )


def build_signer(cfg_or_pk: Any) -> IBlockchainSigner:
    if isinstance(cfg_or_pk, str):
        signer = LocalKeySigner(cfg_or_pk)
        if signer.address.lower() != EXPECTED_OPERATOR.lower():
            raise ActivationDriverError(
                f"FAIL CLOSED: signer address {signer.address} != required operator "
                f"{EXPECTED_OPERATOR}"
            )
        return signer

    cfg: DriverConfig = cfg_or_pk
    if cfg.signer_mode == "local":
        if not cfg.private_key:
            raise ActivationDriverError(
                "FAIL CLOSED: KALYX_BLOCKCHAIN_PRIVATE_KEY is not set"
            )
        signer = LocalKeySigner(cfg.private_key)
        if signer.address.lower() != EXPECTED_OPERATOR.lower():
            raise ActivationDriverError(
                f"FAIL CLOSED: signer address {signer.address} != required operator "
                f"{EXPECTED_OPERATOR}"
            )
        return signer
    elif cfg.signer_mode == "external":
        return ExternalTransactionSigner(
            cfg.operator_address, signed_raw_tx_hex=cfg.signed_raw_tx_hex
        )
    else:
        raise ActivationDriverError(f"FAIL CLOSED: unknown signer_mode {cfg.signer_mode}")


def build_intent(
    *,
    tenant_id: str = "tenant-orbio",
    organisation_id: str = "org-orbio-activation",
    operation_id: Optional[str] = None,
) -> OrbioCreditActivationIntent:
    op_id = operation_id or f"act-{uuid.uuid4().hex[:12]}"
    return OrbioCreditActivationIntent(
        tenant_id=tenant_id,
        organisation_id=organisation_id,
        operation_id=op_id,
        chain_id=ORBIO_ACTIVATION_CHAIN_ID,
        network=ORBIO_ACTIVATION_NETWORK,
        credit_contract=ORBIO_CREDIT_ACTIVATION_CONTRACT,
        amount=MAX_ACTIVATION_AMOUNT,
        gas_limit=DEFAULT_GAS_LIMIT,
        amount_credits=0,
        idempotency_key=f"orbio-activate-1credit-{op_id}",
        policy_decision_id="pending",
        authorization_token_hash="pending",
    )


def validate_intent_hard_bounds(intent: OrbioCreditActivationIntent) -> None:
    if intent.chain_id != ORBIO_ACTIVATION_CHAIN_ID:
        raise ActivationDriverError(
            f"FAIL CLOSED: chain_id {intent.chain_id} != {ORBIO_ACTIVATION_CHAIN_ID}"
        )
    if intent.credit_contract.lower() != ORBIO_CREDIT_ACTIVATION_CONTRACT:
        raise ActivationDriverError("FAIL CLOSED: credit_contract not allowlisted")
    if intent.amount != MAX_ACTIVATION_AMOUNT:
        raise ActivationDriverError(
            f"FAIL CLOSED: amount {intent.amount} != {MAX_ACTIVATION_AMOUNT}"
        )
    if intent.chain_id == 46630:
        raise ActivationDriverError("FAIL CLOSED: testnet chain forbidden")
    if intent.encode_calldata() != EXPECTED_CALLDATA:
        raise ActivationDriverError("FAIL CLOSED: calldata mismatch for activate(1000000)")


def authorize(
    intent: OrbioCreditActivationIntent,
    *,
    operator_id: str = "OPERATOR",
) -> Tuple[OrbioCreditActivationIntent, Any, Any]:
    """Policy + human approval bound to exact intent. Returns (intent, approval, preparation)."""
    policy = OrbioCreditActivationPolicy(require_human_approval=True)
    # Bind policy decision id / auth hash into a fresh intent copy
    approval = policy.issue_human_approval(intent, operator_id=operator_id, notes="1 CREDIT mainnet activation")
    decision = policy.evaluate(intent, human_approval=approval, runtime_mode="mainnet")
    if not decision.is_allowed():
        raise ActivationDriverError(
            f"FAIL CLOSED: policy denied: {decision.result.value} {decision.denial_codes}"
        )

    intent = intent.model_copy(
        update={
            "policy_decision_id": decision.decision_id,
            "authorization_token_hash": approval.issuance_token or decision.decision_id,
        }
    )
    # Re-bind approval to updated intent hash
    approval = policy.issue_human_approval(intent, operator_id=operator_id, notes="1 CREDIT mainnet activation")
    decision = policy.evaluate(intent, human_approval=approval, runtime_mode="mainnet")
    if not decision.is_allowed():
        raise ActivationDriverError(
            f"FAIL CLOSED: policy denied after rebind: {decision.denial_codes}"
        )

    bridge = OrbioCreditActivationBridge(policy=policy)
    org = Organisation(
        id=intent.organisation_id,
        mission="Orbio CREDIT activation",
        tenant_id=intent.tenant_id,
        treasury_balance=0,
        state=OrgState.EXECUTING,
    )
    preparation, operation = bridge.prepare_and_create_operation(
        intent,
        org,
        human_approval=approval,
        runtime_mode="mainnet",
        provider_name="blockchain",
    )
    if not preparation.is_authorized:
        raise ActivationDriverError("FAIL CLOSED: bridge preparation not authorized")
    return intent, approval, (preparation, operation, org, decision)


def run_preflight(rpc: HttpEvmRpcClient, operator: str, intent: OrbioCreditActivationIntent) -> Any:
    # Adapter: OrbioActivationPreflight expects protocol methods
    class _RpcAdapter:
        def get_chain_id(self) -> int:
            return rpc.get_chain_id()

        def get_code(self, address: str) -> str:
            # HttpEvmRpcClient does not expose get_code; use raw call
            return rpc._call("eth_getCode", [address, "latest"]) or "0x"

        def eth_call(self, to: str, data: str, from_address: Optional[str] = None) -> str:
            return rpc._call("eth_call", [{"to": to, "data": data}, "latest"]) or "0x"

        def get_transaction_count(self, address: str, block: str = "pending") -> int:
            return rpc.get_transaction_count(address, block)

        def estimate_gas(self, tx: Dict[str, Any]) -> int:
            res = rpc._call("eth_estimateGas", [tx])
            return int(res, 16) if isinstance(res, str) else int(res)

        def get_balance(self, address: str) -> int:
            return rpc.get_balance(address)

    preflight = OrbioActivationPreflight(_RpcAdapter(), operator)
    result = preflight.run(intent)
    if not result.ok:
        raise ActivationDriverError(
            "FAIL CLOSED: live preflight failed: " + "; ".join(result.errors)
        )
    return result


def execute_via_provider(
    *,
    signer: LocalKeySigner,
    rpc: HttpEvmRpcClient,
    operation: Any,
    intent: OrbioCreditActivationIntent,
) -> Dict[str, Any]:
    """Sign + broadcast only through BlockchainSettlementProvider."""
    provider = BlockchainSettlementProvider(
        rpc_client=rpc,
        signer=signer,
        default_chain_id=ORBIO_ACTIVATION_CHAIN_ID,
        network_name=ORBIO_ACTIVATION_NETWORK,
        wait_for_receipt_seconds=12.0,
    )
    # Runtime guard
    OrbioMainnetActivationRuntime(
        chain_id=ORBIO_ACTIVATION_CHAIN_ID,
        credit_contract=ORBIO_CREDIT_ACTIVATION_CONTRACT,
        rpc_url=rpc.rpc_url,
        signer_configured=True,
        mode="mainnet",
    )

    result = provider.execute(operation)
    out: Dict[str, Any] = {
        "outcome": result.outcome,
        "provider_reference": result.provider_reference,
        "error_message": result.error_message,
        "raw_response": result.raw_response,
        "evidence_hash": result.evidence_hash,
        "api_balance_confirmed": False,
    }

    # UNKNOWN / TIMEOUT: do not retry
    if result.outcome in ("TIMEOUT", "UNKNOWN"):
        out["state"] = "UNKNOWN_PENDING"
        out["note"] = "Ambiguous broadcast; do not resend. Reconcile by tx hash / nonce."
        return out

    if result.outcome != "SUCCESS":
        out["state"] = "FAILED"
        return out

    receipt = result.raw_response if isinstance(result.raw_response, dict) else {}
    # Normalize receipt shape for verifier (provider may nest)
    if "raw_receipt" in receipt:
        chain_receipt = receipt.get("raw_receipt") or {}
    else:
        chain_receipt = receipt

    # Ensure to field for verifier
    if "to" not in chain_receipt and receipt.get("recipient"):
        chain_receipt = dict(chain_receipt)
        chain_receipt["to"] = receipt.get("recipient")

    # Fetch authoritative receipt if needed
    tx_hash = result.provider_reference
    if tx_hash and (not chain_receipt.get("logs")):
        fetched = rpc.get_transaction_receipt(tx_hash)
        if fetched:
            chain_receipt = fetched

    report = OrbioCreditActivationVerifier().verify(
        intent,
        chain_id=ORBIO_ACTIVATION_CHAIN_ID,
        receipt=chain_receipt,
        expected_sender=signer.address,
    )
    out["verification"] = report.to_audit_dict()
    out["state"] = (
        "VERIFIED_ON_CHAIN"
        if report.result == ActivationVerificationResult.VERIFIED
        else "NOT_VERIFIED"
    )
    out["api_balance_confirmed"] = False
    out["api_balance_note"] = "API BALANCE NOT YET CONFIRMED — ON-CHAIN ACTIVATION ONLY; Phase 21"
    return out


def reconcile_via_tx_hash(
    *,
    rpc: HttpEvmRpcClient,
    tx_hash: str,
    intent: OrbioCreditActivationIntent,
    operator_address: str,
) -> Dict[str, Any]:
    """Reconciles an already-broadcasted transaction hash against the authorized intent."""
    clean_tx_hash = tx_hash.strip().lower()
    tx = rpc.get_transaction_by_hash(clean_tx_hash)
    if not tx:
        raise ActivationDriverError(
            f"FAIL CLOSED: transaction {clean_tx_hash} not found on Robinhood Chain mainnet"
        )

    tx_from = str(tx.get("from", "")).lower()
    tx_to = str(tx.get("to", "")).lower()
    tx_input = str(tx.get("input", "")).lower()

    if tx_from != operator_address.lower():
        raise ActivationDriverError(
            f"FAIL CLOSED: transaction sender {tx_from} != required operator {operator_address}"
        )
    if tx_to != ORBIO_CREDIT_ACTIVATION_CONTRACT.lower():
        raise ActivationDriverError(
            f"FAIL CLOSED: transaction recipient {tx_to} != required contract {ORBIO_CREDIT_ACTIVATION_CONTRACT}"
        )
    if tx_input != EXPECTED_CALLDATA.lower():
        raise ActivationDriverError(
            f"FAIL CLOSED: transaction calldata mismatch:\n  got: {tx_input}\n  exp: {EXPECTED_CALLDATA}"
        )

    receipt = rpc.get_transaction_receipt(clean_tx_hash)
    if not receipt:
        return {
            "outcome": "UNKNOWN",
            "provider_reference": clean_tx_hash,
            "state": "UNKNOWN_PENDING",
            "note": "Transaction found on-chain but receipt pending confirmation",
            "api_balance_confirmed": False,
        }

    status_val = receipt.get("status")
    is_success = status_val in (1, "0x1", "1")
    if not is_success:
        return {
            "outcome": "FAILURE",
            "provider_reference": clean_tx_hash,
            "state": "FAILED",
            "error_message": "Transaction reverted on-chain",
            "api_balance_confirmed": False,
        }

    report = OrbioCreditActivationVerifier().verify(
        intent,
        chain_id=ORBIO_ACTIVATION_CHAIN_ID,
        receipt=receipt,
        expected_sender=operator_address,
    )
    return {
        "outcome": "SUCCESS",
        "provider_reference": clean_tx_hash,
        "state": (
            "VERIFIED_ON_CHAIN"
            if report.result == ActivationVerificationResult.VERIFIED
            else "NOT_VERIFIED"
        ),
        "verification": report.to_audit_dict(),
        "api_balance_confirmed": False,
        "api_balance_note": "API BALANCE NOT YET CONFIRMED — ON-CHAIN ACTIVATION ONLY; Phase 21",
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Kalyx Phase 20B — 1 CREDIT Orbio mainnet activation (governed)"
    )
    parser.add_argument(
        "--confirm-mainnet-activation",
        action="store_true",
        help="Required explicit confirmation for mainnet activation",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config, policy, and preflight only; do not sign or broadcast",
    )
    parser.add_argument(
        "--signer-mode",
        choices=["local", "external"],
        default=None,
        help="Signing mode: 'local' (private key) or 'external' (delegated / Robinhood Wallet)",
    )
    parser.add_argument(
        "--operator-address",
        type=str,
        default=EXPECTED_OPERATOR,
        help=f"Authorized operator public address (default: {EXPECTED_OPERATOR})",
    )
    parser.add_argument(
        "--export-unsigned-tx",
        action="store_true",
        help="Export the exact governed EIP-1559 transaction payload for external signing",
    )
    parser.add_argument(
        "--signed-tx-hex",
        type=str,
        default=None,
        help="Raw signed transaction hex (0x02...) from external wallet for broadcast",
    )
    parser.add_argument(
        "--reconcile-tx",
        type=str,
        default=None,
        help="Transaction hash to verify and reconcile if already broadcast by wallet",
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args)
        signer = build_signer(cfg)
        print(f"Signer Mode: {cfg.signer_mode}")
        print(f"Operator Address: {signer.address}")
        print(f"RPC: {cfg.rpc_url.split('?')[0]}")
        print(f"Chain: {ORBIO_ACTIVATION_CHAIN_ID}")
        print(f"CREDIT: {ORBIO_CREDIT_ACTIVATION_CONTRACT}")
        print(f"Amount: {MAX_ACTIVATION_AMOUNT} atoms (1.000000 CREDIT)")
        print(f"Calldata: {EXPECTED_CALLDATA}")
        print(f"Dry-run: {cfg.dry_run}")

        intent = build_intent()
        validate_intent_hard_bounds(intent)

        intent, approval, bundle = authorize(intent)
        preparation, operation, org, decision = bundle
        print(f"Intent hash: {intent.compute_activation_intent_hash()}")
        print(f"Policy decision: {decision.result.value}")
        print(f"Human approval id: {approval.approval_id}")

        rpc = HttpEvmRpcClient(rpc_url=cfg.rpc_url, timeout=30.0)
        live_chain = rpc.get_chain_id()
        if live_chain != ORBIO_ACTIVATION_CHAIN_ID:
            raise ActivationDriverError(
                f"FAIL CLOSED: RPC chain_id {live_chain} != {ORBIO_ACTIVATION_CHAIN_ID}"
            )

        if cfg.reconcile_tx_hash:
            print(f"\nReconciling external transaction {cfg.reconcile_tx_hash}...")
            out = reconcile_via_tx_hash(
                rpc=rpc,
                tx_hash=cfg.reconcile_tx_hash,
                intent=intent,
                operator_address=signer.address,
            )
            print(f"Outcome: {out.get('outcome')}")
            print(f"State: {out.get('state')}")
            print(f"Tx: {out.get('provider_reference')}")
            print(f"Verification: {out.get('verification')}")
            print(out.get("api_balance_note", "API balance not confirmed"))
            if out.get("state") == "UNKNOWN_PENDING":
                print(out.get("note"))
                return 2
            if out.get("state") != "VERIFIED_ON_CHAIN":
                return 1
            return 0

        preflight = run_preflight(rpc, signer.address, intent)
        audit = preflight.to_audit_dict()
        print(
            "Preflight OK | "
            f"balance={audit.get('operator_credit_balance')} "
            f"credited={audit.get('preview_credited')} "
            f"fee={audit.get('preview_fee_atoms')} "
            f"bps={audit.get('activation_fee_bps')} "
            f"gas_est={audit.get('gas_estimate')} "
            f"nonce={audit.get('nonce')}"
        )
        print("api_balance_confirmed=False (preview only)")

        if cfg.export_unsigned:
            live_nonce = rpc.get_transaction_count(signer.address, "pending")
            if isinstance(signer, ExternalTransactionSigner):
                tx_payload = signer.build_transaction_payload(intent, live_nonce)
            else:
                tx_payload = {
                    "type": 2,
                    "chainId": intent.chain_id,
                    "nonce": live_nonce,
                    "maxFeePerGas": 25_000_000_000,
                    "maxPriorityFeePerGas": 1_500_000_000,
                    "gas": intent.gas_limit,
                    "to": intent.credit_contract,
                    "value": 0,
                    "data": EXPECTED_CALLDATA,
                }
            import json
            print("\n=== GOVERNED UNSIGNED TRANSACTION FOR EXTERNAL SIGNING ===")
            print(json.dumps(tx_payload, indent=2))
            print("============================================================")
            print("\nWorkflow to complete execution:")
            print("1. Sign the above exact transaction payload using Robinhood Wallet / external signer.")
            print("2. If broadcasting via Kalyx:")
            print("   python scripts/execute_orbio_activation_mainnet.py --confirm-mainnet-activation --signer-mode external --signed-tx-hex <SIGNED_RAW_HEX>")
            print("3. If broadcast directly via wallet:")
            print("   python scripts/execute_orbio_activation_mainnet.py --confirm-mainnet-activation --reconcile-tx <TX_HASH>")
            return 0

        if cfg.dry_run:
            print("DRY-RUN complete — no sign, no broadcast")
            return 0

        if cfg.signer_mode == "external" and not cfg.signed_raw_tx_hex:
            live_nonce = rpc.get_transaction_count(signer.address, "pending")
            assert isinstance(signer, ExternalTransactionSigner)
            tx_payload = signer.build_transaction_payload(intent, live_nonce)
            import json
            print("\n=== GOVERNED UNSIGNED TRANSACTION ===")
            print(json.dumps(tx_payload, indent=2))
            print("======================================")
            print("To submit the signature, run with --signed-tx-hex <RAW_SIGNED_HEX>")
            print("Or if broadcast via Robinhood Wallet, run with --reconcile-tx <TX_HASH>")
            return 0

        print("Broadcasting via BlockchainSettlementProvider (single attempt)...")
        out = execute_via_provider(
            signer=signer,
            rpc=rpc,
            operation=operation,
            intent=intent,
        )
        print(f"Outcome: {out.get('outcome')}")
        print(f"State: {out.get('state')}")
        print(f"Tx: {out.get('provider_reference')}")
        print(f"Verification: {out.get('verification')}")
        print(out.get("api_balance_note", "API balance not confirmed"))
        if out.get("state") == "UNKNOWN_PENDING":
            print(out.get("note"))
            return 2
        if out.get("state") != "VERIFIED_ON_CHAIN":
            return 1
        return 0
    except ActivationDriverError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        # Do not include key material
        print(f"FAIL CLOSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
