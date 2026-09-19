"""Domain entities for Phase 17 Inter-DAO B2B Marketplace and Escrow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class MarketplaceOrderStatus(str, Enum):
    OPEN = "OPEN"
    CLAIMED = "CLAIMED"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class EscrowStatus(str, Enum):
    HELD = "HELD"
    RELEASED = "RELEASED"
    REFUNDED = "REFUNDED"


@dataclass
class MarketplaceOrder:
    tenant_id: str
    organisation_id: str
    order_id: str
    title: str
    description: str
    specification_hash: str
    required_capability: str
    bounty_amount: int
    bounty_asset: str = "USDG"
    sla_timeout_seconds: int = 3600
    status: MarketplaceOrderStatus = MarketplaceOrderStatus.OPEN
    claimed_by_tenant_id: Optional[str] = None
    claimed_by_org_id: Optional[str] = None
    claimed_by_agent_id: Optional[str] = None
    work_order_id: Optional[str] = None
    deliverable_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    claimed_at: Optional[str] = None
    completed_at: Optional[str] = None

    @classmethod
    def create(
        cls,
        tenant_id: str,
        organisation_id: str,
        order_id: str,
        title: str,
        description: str,
        required_capability: str,
        bounty_amount: int,
        bounty_asset: str = "USDG",
        sla_timeout_seconds: int = 3600,
        specification_payload: Optional[Dict[str, Any]] = None,
    ) -> MarketplaceOrder:
        if bounty_amount <= 0:
            raise ValueError("bounty_amount must be positive")
        spec = json.dumps(specification_payload or {"title": title, "description": description}, sort_keys=True)
        spec_hash = hashlib.sha256(spec.encode("utf-8")).hexdigest()
        return cls(
            tenant_id=tenant_id,
            organisation_id=organisation_id,
            order_id=order_id,
            title=title,
            description=description,
            specification_hash=spec_hash,
            required_capability=required_capability,
            bounty_amount=bounty_amount,
            bounty_asset=bounty_asset,
            sla_timeout_seconds=sla_timeout_seconds,
            status=MarketplaceOrderStatus.OPEN,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "order_id": self.order_id,
            "title": self.title,
            "description": self.description,
            "specification_hash": self.specification_hash,
            "required_capability": self.required_capability,
            "bounty_amount": self.bounty_amount,
            "bounty_asset": self.bounty_asset,
            "sla_timeout_seconds": self.sla_timeout_seconds,
            "status": self.status.value if isinstance(self.status, MarketplaceOrderStatus) else self.status,
            "claimed_by_tenant_id": self.claimed_by_tenant_id,
            "claimed_by_org_id": self.claimed_by_org_id,
            "claimed_by_agent_id": self.claimed_by_agent_id,
            "work_order_id": self.work_order_id,
            "deliverable_id": self.deliverable_id,
            "created_at": self.created_at,
            "claimed_at": self.claimed_at,
            "completed_at": self.completed_at,
        }


@dataclass
class EscrowAgreement:
    tenant_id: str
    organisation_id: str
    escrow_id: str
    order_id: str
    client_tenant_id: str
    client_org_id: str
    bounty_amount: int
    bounty_asset: str = "USDG"
    provider_tenant_id: Optional[str] = None
    provider_org_id: Optional[str] = None
    status: EscrowStatus = EscrowStatus.HELD
    client_ledger_tx_id: Optional[str] = None
    provider_ledger_tx_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    released_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "escrow_id": self.escrow_id,
            "order_id": self.order_id,
            "client_tenant_id": self.client_tenant_id,
            "client_org_id": self.client_org_id,
            "provider_tenant_id": self.provider_tenant_id,
            "provider_org_id": self.provider_org_id,
            "bounty_amount": self.bounty_amount,
            "bounty_asset": self.bounty_asset,
            "status": self.status.value if isinstance(self.status, EscrowStatus) else self.status,
            "client_ledger_tx_id": self.client_ledger_tx_id,
            "provider_ledger_tx_id": self.provider_ledger_tx_id,
            "created_at": self.created_at,
            "released_at": self.released_at,
        }


@dataclass(frozen=True)
class PublicMarketplaceOrder:
    """Sanitized public marketplace order projection for cross-tenant discovery.

    Exposes ONLY non-sensitive order specifications. Strips all private tenant data,
    escrow ids, claiming agent/org ids, and execution telemetry.
    """
    order_id: str
    title: str
    description: str
    required_capability: str
    bounty_amount: int
    bounty_asset: str = "USDG"
    sla_timeout_seconds: int = 3600
    status: MarketplaceOrderStatus = MarketplaceOrderStatus.OPEN
    created_at: str = ""
    provenance: str = "UNEXECUTED"

    @classmethod
    def from_order(cls, order: MarketplaceOrder, provenance: str = "UNEXECUTED") -> PublicMarketplaceOrder:
        return cls(
            order_id=order.order_id,
            title=order.title,
            description=order.description,
            required_capability=order.required_capability,
            bounty_amount=order.bounty_amount,
            bounty_asset=order.bounty_asset,
            sla_timeout_seconds=order.sla_timeout_seconds,
            status=order.status,
            created_at=order.created_at,
            provenance=provenance,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "title": self.title,
            "description": self.description,
            "required_capability": self.required_capability,
            "bounty_amount": self.bounty_amount,
            "bounty_asset": self.bounty_asset,
            "sla_timeout_seconds": self.sla_timeout_seconds,
            "status": self.status.value if isinstance(self.status, MarketplaceOrderStatus) else self.status,
            "created_at": self.created_at,
            "provenance": self.provenance,
        }
