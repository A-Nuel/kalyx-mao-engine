from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from src.domain.exceptions import InsufficientCreditsError
from src.domain.work_order import WorkDeliverable, WorkOrder
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider


class BaseWorkExecutor(ABC):
    """Abstract interface for executing economic work orders."""

    @abstractmethod
    def execute_work(
        self,
        work_order: WorkOrder,
        producer_agent_id: str,
        organisation_id: str,
        activated_api_key: Optional[str] = None,
    ) -> WorkDeliverable:
        pass


class SimulatedWorkExecutor(BaseWorkExecutor):
    """
    Offline deterministic work executor.
    
    Consumes Orbio CREDIT resources from an exchange provider or credit map,
    performs simulated task execution, and returns a verified WorkDeliverable
    with deterministic content and canonical SHA-256 hash.
    """

    def __init__(
        self,
        exchange_provider: Optional[SimulatedOrbioExchangeProvider] = None,
        credit_store: Optional[Dict[str, int]] = None,
    ):
        self.exchange_provider = exchange_provider
        self._credit_store = credit_store if credit_store is not None else {}

    def get_credit_balance(self, organisation_id: str) -> int:
        if self.exchange_provider is not None:
            return self.exchange_provider.get_credit_balance(organisation_id)
        return self._credit_store.get(organisation_id, 0)

    def deduct_credits(self, organisation_id: str, amount: int) -> None:
        if self.exchange_provider is not None:
            current = self.exchange_provider.get_credit_balance(organisation_id)
            if current < amount:
                raise InsufficientCreditsError(
                    f"Organisation '{organisation_id}' has {current} Orbio credits, but required {amount}"
                )
            self.exchange_provider._credit[organisation_id] = current - amount
        else:
            current = self._credit_store.get(organisation_id, 0)
            if current < amount:
                raise InsufficientCreditsError(
                    f"Organisation '{organisation_id}' has {current} Orbio credits, but required {amount}"
                )
            self._credit_store[organisation_id] = current - amount

    def execute_work(
        self,
        work_order: WorkOrder,
        producer_agent_id: str,
        organisation_id: str,
        activated_api_key: Optional[str] = None,
    ) -> WorkDeliverable:
        # 1. Resource Preflight: Check Orbio CREDIT quota
        required_credits = work_order.required_orbio_credits
        available_credits = self.get_credit_balance(organisation_id)
        if available_credits < required_credits:
            raise InsufficientCreditsError(
                f"Insufficient Orbio CREDIT: organisation '{organisation_id}' has {available_credits}, "
                f"work order '{work_order.work_order_id}' requires {required_credits}"
            )

        # 2. Deduct compute resources (non-refundable work consumption)
        self.deduct_credits(organisation_id, required_credits)

        # 3. Deterministic Deliverable Synthesis
        if work_order.deliverable_type == "SECURITY_AUDIT":
            content_payload = {
                "work_order_id": work_order.work_order_id,
                "deliverable_type": work_order.deliverable_type,
                "target": work_order.title,
                "findings": [
                    {
                        "finding_id": "VULN-001",
                        "severity": "INFORMATIONAL",
                        "description": "Validated safe call patterns; no unchecked calls detected.",
                    },
                    {
                        "finding_id": "VULN-002",
                        "severity": "LOW",
                        "description": "Visibility specifier explicitly defined on state mutating methods.",
                    },
                ],
                "audit_verdict": "PASSED_SECURE",
                "code_coverage_pct": 98.2,
                "summary": f"Security audit completed for {work_order.title}.",
            }
        elif work_order.deliverable_type == "MARKET_ANALYSIS":
            content_payload = {
                "work_order_id": work_order.work_order_id,
                "deliverable_type": work_order.deliverable_type,
                "target": work_order.title,
                "metrics": {
                    "liquidity_depth_usdg": 1500000,
                    "volume_24h_usdg": 340000,
                    "volatility_index": 0.12,
                },
                "recommendation": "BUY_WITHIN_BOUNDS",
                "summary": f"Market analysis completed for {work_order.title}.",
            }
        else:
            content_payload = {
                "work_order_id": work_order.work_order_id,
                "deliverable_type": work_order.deliverable_type,
                "target": work_order.title,
                "result": "Execution completed successfully.",
                "summary": f"Delivered work for {work_order.title}.",
            }

        telemetry = {
            "is_simulated": True,
            "executor": "SimulatedWorkExecutor",
            "endpoint": "simulated://local",
            "activated_api_key_used": activated_api_key or "simulated-key",
            "latency_ms": 42,
            "credits_deducted": required_credits,
        }

        # 4. Construct Deliverable
        return WorkDeliverable.create(
            work_order_id=work_order.work_order_id,
            producer_agent_id=producer_agent_id,
            content_payload=content_payload,
            orbio_credits_consumed=required_credits,
            execution_telemetry=telemetry,
        )
