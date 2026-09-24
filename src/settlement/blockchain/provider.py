"""Authoritative Blockchain Settlement Provider for the Consequential Execution Boundary.

Adheres strictly to the existing ConsequentialProviderAdapter protocol:
- Executes already-authorized transaction intents.
- Translates EVM on-chain outcomes into normalized ProviderExecutionResult.
- Preserves UNKNOWN on timeout or pending confirmation (escrow safely locked).
- Provides independent on-chain status querying for reconciliation.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, Optional

from src.domain.blockchain import BlockchainReceiptEvidence, BlockchainTransactionIntent
from src.domain.entities import ConsequentialOperation
from src.domain.enums import ProviderOutcome
from src.domain.events import canonical_json
from src.domain.exceptions import UnauthorizedActionError
from src.settlement.adapter import ConsequentialProviderAdapter, ProviderExecutionResult, ProviderStatusResult
from src.settlement.blockchain.nonce_manager import NonceManager
from src.settlement.blockchain.rpc_client import IEvmRpcClient
from src.settlement.blockchain.signer import IBlockchainSigner


class BlockchainSettlementProvider(ConsequentialProviderAdapter):
    """EVM blockchain settlement provider implementation."""

    name = "blockchain"

    def __init__(
        self,
        rpc_client: IEvmRpcClient,
        signer: IBlockchainSigner,
        nonce_manager: Optional[NonceManager] = None,
        default_chain_id: int = 11155111,
        network_name: str = "sepolia",
        wait_for_receipt_seconds: float = 0.0,
    ):
        self.rpc_client = rpc_client
        self.signer = signer
        self.nonce_manager = nonce_manager or NonceManager()
        self.default_chain_id = default_chain_id
        self.network_name = network_name
        self.wait_for_receipt_seconds = wait_for_receipt_seconds
        # Map operation_id or idempotency_key -> tx_hash for reconciliation lookup
        self._op_tx_hashes: Dict[str, str] = {}

    def prepare(self, operation: ConsequentialOperation) -> bool:
        if operation.amount < 0:
            return False
        return True

    def _build_intent(self, operation: ConsequentialOperation) -> BlockchainTransactionIntent:
        """Constructs and validates the typed BlockchainTransactionIntent from operation parameters."""
        params = operation.parameters if isinstance(operation.parameters, dict) else {}

        recipient = params.get("recipient")
        if not recipient and operation.target:
            recipient = (
                operation.target.replace("evm://", "")
                .replace("blockchain://", "")
                .replace("sepolia://", "")
            )

        if not recipient:
            raise ValueError(f"Blockchain operation '{operation.id}' missing required recipient address")

        intent = BlockchainTransactionIntent(
            tenant_id=operation.tenant_id,
            organisation_id=operation.organisation_id,
            mission_id=params.get("mission_id"),
            operation_id=operation.id,
            chain_id=int(params.get("chain_id", self.default_chain_id)),
            network=params.get("network", self.network_name),
            recipient=recipient,
            amount_wei=int(params.get("amount_wei", 0)),
            amount_credits=operation.amount,
            asset=params.get("asset", "ETH"),
            token_contract=params.get("token_contract"),
            max_fee_per_gas=int(params.get("max_fee_per_gas", 25_000_000_000)),
            max_priority_fee_per_gas=int(params.get("max_priority_fee_per_gas", 1_500_000_000)),
            gas_limit=int(params.get("gas_limit", 21_000)),
            data_payload=params.get("data_payload", "0x"),
            idempotency_key=operation.idempotency_key,
            policy_decision_id=operation.decision_id,
            authorization_token_hash=hashlib.sha256(
                params.get("authorization_token", operation.id).encode("utf-8")
            ).hexdigest(),
        )
        return intent

    def execute(self, operation: ConsequentialOperation) -> ProviderExecutionResult:
        """Sign and broadcast the authorized blockchain transaction."""
        intent = self._build_intent(operation)
        intent_hash = intent.compute_intent_hash()

        # Allocate or fetch idempotent nonce
        nonce = self.nonce_manager.get_or_allocate_nonce(
            sender=self.signer.address,
            chain_id=intent.chain_id,
            operation_id=operation.id,
            idempotency_key=operation.idempotency_key,
            on_chain_nonce_fetcher=lambda: self.rpc_client.get_transaction_count(self.signer.address, "pending"),
        )

        # Cryptographic signing strictly at the execution boundary
        raw_tx_bytes, tx_hash = self.signer.sign_transaction(intent, nonce)
        self._op_tx_hashes[operation.id] = tx_hash
        self._op_tx_hashes[operation.idempotency_key] = tx_hash

        # Broadcast via RPC
        try:
            broadcast_tx_hash = self.rpc_client.send_raw_transaction(raw_tx_bytes)
        except Exception as exc:
            # Transport timeout or network connection drop during broadcast is ambiguous
            # The transaction hash was derived from the signed intent and may have reached mempools!
            return ProviderExecutionResult(
                provider_name=self.name,
                operation_id=operation.id,
                outcome=ProviderOutcome.TIMEOUT.value,
                provider_reference=tx_hash,
                raw_response={"error": str(exc), "tx_hash": tx_hash, "intent_hash": intent_hash},
                error_message=f"Transport error during broadcast: {exc}",
                evidence_hash=None,
            )

        # Transaction submitted successfully
        tx_ref = broadcast_tx_hash or tx_hash

        # Check receipt with polling if wait_for_receipt_seconds is specified
        receipt = None
        deadline = time.time() + self.wait_for_receipt_seconds
        while True:
            try:
                receipt = self.rpc_client.get_transaction_receipt(tx_ref)
                if receipt is not None:
                    break
            except Exception:
                receipt = None
            if time.time() >= deadline:
                break
            time.sleep(min(2.0, max(0.1, deadline - time.time())))

        if receipt is None:
            # Transaction is pending on-chain or confirmation not yet available: PRESERVE UNKNOWN
            return ProviderExecutionResult(
                provider_name=self.name,
                operation_id=operation.id,
                outcome=ProviderOutcome.TIMEOUT.value,
                provider_reference=tx_ref,
                raw_response={
                    "status": "pending",
                    "tx_hash": tx_ref,
                    "sender": self.signer.address,
                    "recipient": intent.recipient,
                    "intent_hash": intent_hash,
                },
                error_message="Transaction submitted to mempool; pending on-chain confirmation",
                evidence_hash=None,
            )

        # Parse receipt status
        raw_status = receipt.get("status")
        is_success = raw_status in (1, "0x1", "1")

        if is_success:
            block_num = (
                int(receipt["blockNumber"], 16)
                if isinstance(receipt.get("blockNumber"), str)
                else receipt.get("blockNumber")
            )
            gas_used = (
                int(receipt["gasUsed"], 16)
                if isinstance(receipt.get("gasUsed"), str)
                else receipt.get("gasUsed")
            )
            eff_price = (
                int(receipt["effectiveGasPrice"], 16)
                if isinstance(receipt.get("effectiveGasPrice"), str)
                else receipt.get("effectiveGasPrice")
            )
            fee = (gas_used * eff_price) if (gas_used is not None and eff_price is not None) else None

            evidence = BlockchainReceiptEvidence(
                provider="evm",
                network=intent.network,
                chain_id=intent.chain_id,
                transaction_hash=tx_ref,
                status="confirmed",
                block_number=block_num,
                block_hash=receipt.get("blockHash"),
                gas_used=gas_used,
                effective_gas_price=eff_price,
                network_fee_wei=fee,
                sender=self.signer.address,
                recipient=intent.recipient,
                amount_wei=intent.amount_wei,
                intent_hash=intent_hash,
                raw_receipt=receipt,
            )
            evidence_hash = evidence.compute_evidence_hash()
            raw_resp = evidence.model_dump(mode="json")
            is_sim = "simulated" in self.rpc_client.__class__.__name__.lower()
            raw_resp["is_simulated"] = is_sim
            raw_resp["explorer_url"] = (
                f"https://sepolia.etherscan.io/tx/{tx_ref}"
                if not is_sim and intent.chain_id == 11155111
                else (f"https://explorer.testnet.chain.robinhood.com/tx/{tx_ref}" if not is_sim and intent.chain_id == 46630 else None)
            )
            return ProviderExecutionResult(
                provider_name=self.name,
                operation_id=operation.id,
                outcome=ProviderOutcome.SUCCESS.value,
                provider_reference=tx_ref,
                raw_response=raw_resp,
                error_message=None,
                evidence_hash=evidence_hash,
            )
        else:
            return ProviderExecutionResult(
                provider_name=self.name,
                operation_id=operation.id,
                outcome=ProviderOutcome.FAILURE.value,
                provider_reference=tx_ref,
                raw_response={"status": "reverted", "tx_hash": tx_ref, "raw_receipt": receipt},
                error_message="Transaction reverted on-chain (receipt status: 0)",
                evidence_hash=hashlib.sha256(canonical_json(receipt).encode("utf-8")).hexdigest(),
            )

    def status(
        self,
        operation_id: str,
        idempotency_key: Optional[str] = None,
        provider_reference: Optional[str] = None,
        **kwargs: Any,
    ) -> ProviderStatusResult:
        """Query independent authoritative on-chain transaction receipt for reconciliation."""
        tx_hash = (
            provider_reference
            or self._op_tx_hashes.get(operation_id)
            or (self._op_tx_hashes.get(idempotency_key) if idempotency_key else None)
        )
        if not tx_hash and operation_id.startswith("0x"):
            tx_hash = operation_id

        if not tx_hash:
            return ProviderStatusResult(
                provider_name=self.name,
                operation_id=operation_id,
                outcome=ProviderOutcome.TIMEOUT.value,
                provider_reference=None,
                raw_status={"error": "No transaction hash recorded for operation"},
                error_message="No transaction hash recorded for operation",
            )

        try:
            receipt = self.rpc_client.get_transaction_receipt(tx_hash)
        except Exception as exc:
            return ProviderStatusResult(
                provider_name=self.name,
                operation_id=operation_id,
                outcome=ProviderOutcome.TIMEOUT.value,
                provider_reference=tx_hash,
                raw_status={"error": str(exc), "tx_hash": tx_hash},
                error_message=f"RPC error during status check: {exc}",
            )

        if receipt is None:
            # Check if still in mempool
            tx_data = self.rpc_client.get_transaction_by_hash(tx_hash)
            if tx_data:
                return ProviderStatusResult(
                    provider_name=self.name,
                    operation_id=operation_id,
                    outcome=ProviderOutcome.TIMEOUT.value,
                    provider_reference=tx_hash,
                    raw_status={"status": "pending_in_mempool", "tx_hash": tx_hash},
                    error_message="Transaction is pending confirmation in mempool",
                )
            # Not in receipt and not in mempool
            return ProviderStatusResult(
                provider_name=self.name,
                operation_id=operation_id,
                outcome=ProviderOutcome.TIMEOUT.value,
                provider_reference=tx_hash,
                raw_status={"status": "unknown_on_chain", "tx_hash": tx_hash},
                error_message="Transaction status unknown on-chain",
            )

        raw_status = receipt.get("status")
        is_success = raw_status in (1, "0x1", "1")

        if is_success:
            evidence_hash = hashlib.sha256(canonical_json(receipt).encode("utf-8")).hexdigest()
            return ProviderStatusResult(
                provider_name=self.name,
                operation_id=operation_id,
                outcome=ProviderOutcome.SUCCESS.value,
                provider_reference=tx_hash,
                raw_status={"status": "confirmed", "tx_hash": tx_hash, "receipt": receipt},
                evidence_hash=evidence_hash,
            )
        else:
            evidence_hash = hashlib.sha256(canonical_json(receipt).encode("utf-8")).hexdigest()
            return ProviderStatusResult(
                provider_name=self.name,
                operation_id=operation_id,
                outcome=ProviderOutcome.FAILURE.value,
                provider_reference=tx_hash,
                raw_status={"status": "reverted", "tx_hash": tx_hash, "receipt": receipt},
                error_message="Transaction reverted on-chain",
                evidence_hash=evidence_hash,
            )

    def get_transaction_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        return self.rpc_client.get_transaction_receipt(tx_hash)

    def get_balance(self, address: str) -> int:
        return self.rpc_client.get_balance(address)

    def get_network_identity(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "network": self.network_name,
            "chain_id": self.default_chain_id,
            "signer_address": self.signer.address,
        }
