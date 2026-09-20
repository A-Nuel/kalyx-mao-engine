"""Durable multi-tenant persistence repository for Phase 17 Marketplace Orders, Escrows, and Dynamic Capabilities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.domain.capability import CapabilityGrant, CapabilityGrantStatus
from src.domain.exceptions import IdempotencyConflict


def _iso_or_none(value: Any) -> Optional[str]:
    """Normalize a possibly-None timestamp field read from either backend
    back to the ISO string every dataclass in this file declares it as.
    SQLite always returns these as str already; Postgres TIMESTAMPTZ
    columns come back as real datetime objects via psycopg, which these
    dataclasses were never updated to accept -- calling
    datetime.fromisoformat() later on one of those (e.g.
    CapabilityGrant.is_valid()) raised exactly
    "TypeError: fromisoformat: argument must be str" in production."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value

from src.domain.marketplace import (
    EscrowAgreement,
    EscrowStatus,
    MarketplaceOrder,
    MarketplaceOrderStatus,
    PublicMarketplaceOrder,
)


class MarketplaceRepository:
    """Repository for managing B2B marketplace orders, escrows, and dynamic capability grants."""

    def __init__(self, db_or_conn: Any) -> None:
        self.conn = getattr(db_or_conn, "conn", db_or_conn)

    # ------------------------------------------------------------------
    # Marketplace Orders
    # ------------------------------------------------------------------

    def create_order(self, order: MarketplaceOrder) -> MarketplaceOrder:
        """Create a marketplace order with strict canonical hash idempotency.

        1. If order doesn't exist: INSERT and return order (NEW).
        2. If order exists with identical specification_hash: return existing order (DUPLICATE).
        3. If order exists with different specification_hash: raise IdempotencyConflict (CONFLICT).
        """
        existing = self.get_order(order.tenant_id, order.organisation_id, order.order_id)
        if existing is not None:
            if existing.specification_hash == order.specification_hash:
                return existing
            raise IdempotencyConflict(
                f"Marketplace order '{order.order_id}' already exists with conflicting specification hash: "
                f"existing '{existing.specification_hash}' vs proposed '{order.specification_hash}'"
            )

        created_str = (
            order.created_at.isoformat()
            if isinstance(order.created_at, datetime)
            else str(order.created_at)
        )
        status_val = (
            order.status.value
            if isinstance(order.status, MarketplaceOrderStatus)
            else str(order.status)
        )

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO marketplace_orders (
                    tenant_id, organisation_id, order_id, title, description,
                    specification_hash, required_capability, bounty_amount,
                    bounty_asset, sla_timeout_seconds, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.tenant_id,
                    order.organisation_id,
                    order.order_id,
                    order.title,
                    order.description,
                    order.specification_hash,
                    order.required_capability,
                    order.bounty_amount,
                    order.bounty_asset,
                    order.sla_timeout_seconds,
                    status_val,
                    created_str,
                ),
            )
        return order

    def get_order(
        self, tenant_id: str, organisation_id: str, order_id: str
    ) -> Optional[MarketplaceOrder]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT * FROM marketplace_orders
            WHERE tenant_id = ? AND organisation_id = ? AND order_id = ?
            """,
            (tenant_id, organisation_id, order_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_order(row)

    def get_order_by_id(self, order_id: str) -> Optional[MarketplaceOrder]:
        """Fetch an order by order_id across organisations for discovery fulfillment."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT * FROM marketplace_orders
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_order(row)

    def list_public_orders(
        self, status: Optional[str] = "OPEN"
    ) -> List[PublicMarketplaceOrder]:
        """Lists public marketplace orders across organisations as sanitized PublicMarketplaceOrder projections.

        Cross-tenant discovery is supported while ensuring zero private tenant/escrow/claimant leakage.
        """
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        if status is not None:
            rows = cursor.execute(
                """
                SELECT * FROM marketplace_orders
                WHERE status = ?
                ORDER BY created_at DESC
                """,
                (status,),
            ).fetchall()
        else:
            rows = cursor.execute(
                """
                SELECT * FROM marketplace_orders
                ORDER BY created_at DESC
                """
            ).fetchall()
        projections = []
        for row in rows:
            order = self._row_to_order(row)
            provenance = "UNEXECUTED"
            if order.deliverable_id:
                telemetry_row = cursor.execute(
                    """
                    SELECT execution_telemetry_json
                    FROM work_deliverables
                    WHERE deliverable_id = ?
                      AND tenant_id = ?
                      AND organisation_id = ?
                    """,
                    (
                        order.deliverable_id,
                        order.claimed_by_tenant_id,
                        order.claimed_by_org_id,
                    ),
                ).fetchone()
                if telemetry_row:
                    try:
                        telemetry = json.loads(telemetry_row["execution_telemetry_json"])
                        provenance = str(telemetry.get("provenance") or ("SIMULATED" if telemetry.get("is_simulated") else "LIVE"))
                    except (TypeError, ValueError):
                        provenance = "UNKNOWN"
            projections.append(PublicMarketplaceOrder.from_order(order, provenance=provenance))
        return projections

    def claim_order(
        self,
        tenant_id: str,
        organisation_id: str,
        order_id: str,
        claimed_by_tenant_id: str,
        claimed_by_org_id: str,
        claimed_by_agent_id: str,
        work_order_id: Optional[str] = None,
    ) -> bool:
        """Atomically claim an open order. Returns True if claimed, False if already taken."""
        claimed_at_str = datetime.now(timezone.utc).isoformat()
        with self.conn:
            cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
            cur = cursor.execute(
                """
                UPDATE marketplace_orders
                SET status = 'CLAIMED',
                    claimed_by_tenant_id = ?,
                    claimed_by_org_id = ?,
                    claimed_by_agent_id = ?,
                    work_order_id = ?,
                    claimed_at = ?
                WHERE tenant_id = ? AND organisation_id = ? AND order_id = ? AND status = 'OPEN'
                """,
                (
                    claimed_by_tenant_id,
                    claimed_by_org_id,
                    claimed_by_agent_id,
                    work_order_id,
                    claimed_at_str,
                    tenant_id,
                    organisation_id,
                    order_id,
                ),
            )
            return cur.rowcount > 0

    def update_order_status(
        self,
        tenant_id: str,
        organisation_id: str,
        order_id: str,
        status: MarketplaceOrderStatus,
        deliverable_id: Optional[str] = None,
    ) -> None:
        status_val = status.value if isinstance(status, MarketplaceOrderStatus) else str(status)
        completed_at_str = (
            datetime.now(timezone.utc).isoformat()
            if status == MarketplaceOrderStatus.COMPLETED
            else None
        )
        with self.conn:
            self.conn.execute(
                """
                UPDATE marketplace_orders
                SET status = ?,
                    deliverable_id = COALESCE(?, deliverable_id),
                    completed_at = COALESCE(?, completed_at)
                WHERE tenant_id = ? AND organisation_id = ? AND order_id = ?
                """,
                (
                    status_val,
                    deliverable_id,
                    completed_at_str,
                    tenant_id,
                    organisation_id,
                    order_id,
                ),
            )

    # ------------------------------------------------------------------
    # Marketplace Escrows
    # ------------------------------------------------------------------

    def save_escrow(self, escrow: EscrowAgreement) -> EscrowAgreement:
        """Persist escrow agreement with strict idempotency and conflict detection."""
        existing = self.get_escrow(escrow.tenant_id, escrow.organisation_id, escrow.escrow_id)
        if existing is not None:
            if existing.order_id == escrow.order_id and existing.bounty_amount == escrow.bounty_amount:
                # Identical canonical terms are a true no-op. Never let a replay mutate
                # lifecycle or settlement fields under an existing escrow identity.
                return existing
            raise IdempotencyConflict(
                f"Escrow '{escrow.escrow_id}' already exists with conflicting parameters: "
                f"order_id '{existing.order_id}' vs '{escrow.order_id}', "
                f"bounty '{existing.bounty_amount}' vs '{escrow.bounty_amount}'"
            )

        created_str = (
            escrow.created_at.isoformat()
            if isinstance(escrow.created_at, datetime)
            else str(escrow.created_at)
        )
        status_val = (
            escrow.status.value
            if isinstance(escrow.status, EscrowStatus)
            else str(escrow.status)
        )
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO marketplace_escrows (
                    tenant_id, organisation_id, escrow_id, order_id,
                    client_tenant_id, client_org_id, provider_tenant_id,
                    provider_org_id, bounty_amount, bounty_asset, status,
                    client_ledger_tx_id, provider_ledger_tx_id, created_at, released_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    escrow.tenant_id,
                    escrow.organisation_id,
                    escrow.escrow_id,
                    escrow.order_id,
                    escrow.client_tenant_id,
                    escrow.client_org_id,
                    escrow.provider_tenant_id,
                    escrow.provider_org_id,
                    escrow.bounty_amount,
                    escrow.bounty_asset,
                    status_val,
                    escrow.client_ledger_tx_id,
                    escrow.provider_ledger_tx_id,
                    created_str,
                    escrow.released_at,
                ),
            )
        return escrow

    def get_escrow(
        self, tenant_id: str, organisation_id: str, escrow_id: str
    ) -> Optional[EscrowAgreement]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT * FROM marketplace_escrows
            WHERE tenant_id = ? AND organisation_id = ? AND escrow_id = ?
            """,
            (tenant_id, organisation_id, escrow_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_escrow(row)

    def get_escrow_by_order(
        self, order_id: str, tenant_id: str, organisation_id: str
    ) -> Optional[EscrowAgreement]:
        """Resolve an escrow by client scope or an authenticated provider claim scope.

        The client creates and owns the escrow, while the provider performs settlement.
        A provider therefore cannot resolve the escrow by its own tenant/org unless the
        marketplace order explicitly records that provider as the current claimant.
        """
        if not tenant_id or not organisation_id:
            raise ValueError("tenant_id and organisation_id are required for scoped escrow lookup")
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT e.*
            FROM marketplace_escrows AS e
            JOIN marketplace_orders AS o
              ON o.tenant_id = e.client_tenant_id
             AND o.organisation_id = e.client_org_id
             AND o.order_id = e.order_id
            WHERE e.order_id = ?
              AND (
                    (e.tenant_id = ? AND e.organisation_id = ?)
                 OR (o.claimed_by_tenant_id = ? AND o.claimed_by_org_id = ?)
              )
            """,
            (order_id, tenant_id, organisation_id, tenant_id, organisation_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_escrow(row)

    def update_escrow_status(
        self,
        tenant_id: str,
        organisation_id: str,
        escrow_id: str,
        status: EscrowStatus,
        provider_tenant_id: Optional[str] = None,
        provider_org_id: Optional[str] = None,
        provider_ledger_tx_id: Optional[str] = None,
    ) -> None:
        status_val = status.value if isinstance(status, EscrowStatus) else str(status)
        released_at_str = (
            datetime.now(timezone.utc).isoformat()
            if status in (EscrowStatus.RELEASED, EscrowStatus.REFUNDED)
            else None
        )
        with self.conn:
            self.conn.execute(
                """
                UPDATE marketplace_escrows
                SET status = ?,
                    provider_tenant_id = COALESCE(?, provider_tenant_id),
                    provider_org_id = COALESCE(?, provider_org_id),
                    provider_ledger_tx_id = COALESCE(?, provider_ledger_tx_id),
                    released_at = COALESCE(?, released_at)
                WHERE tenant_id = ? AND organisation_id = ? AND escrow_id = ?
                """,
                (
                    status_val,
                    provider_tenant_id,
                    provider_org_id,
                    provider_ledger_tx_id,
                    released_at_str,
                    tenant_id,
                    organisation_id,
                    escrow_id,
                ),
            )

    # ------------------------------------------------------------------
    # Agent Capability Grants
    # ------------------------------------------------------------------

    def save_capability_grant(self, grant: CapabilityGrant) -> None:
        granted_str = (
            grant.granted_at.isoformat()
            if isinstance(grant.granted_at, datetime)
            else str(grant.granted_at)
        )
        expires_str = (
            grant.expires_at.isoformat()
            if isinstance(grant.expires_at, datetime)
            else str(grant.expires_at)
        )
        status_val = (
            grant.status.value
            if isinstance(grant.status, CapabilityGrantStatus)
            else str(grant.status)
        )
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO agent_capability_grants (
                    tenant_id, organisation_id, grant_id, agent_id,
                    capability_name, permission_level, trigger_performance_score,
                    granted_by_policy_id, status, granted_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, organisation_id, grant_id) DO UPDATE SET
                    status = excluded.status,
                    expires_at = excluded.expires_at
                """,
                (
                    grant.tenant_id,
                    grant.organisation_id,
                    grant.grant_id,
                    grant.agent_id,
                    grant.capability_name,
                    grant.permission_level,
                    grant.trigger_performance_score,
                    grant.granted_by_policy_id,
                    status_val,
                    granted_str,
                    expires_str,
                ),
            )

    def get_capability_grant(
        self, tenant_id: str, organisation_id: str, grant_id: str
    ) -> Optional[CapabilityGrant]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT * FROM agent_capability_grants
            WHERE tenant_id = ? AND organisation_id = ? AND grant_id = ?
            """,
            (tenant_id, organisation_id, grant_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_grant(row)

    def list_agent_grants(
        self, tenant_id: str, organisation_id: str, agent_id: str, active_only: bool = True
    ) -> List[CapabilityGrant]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        if active_only:
            rows = cursor.execute(
                """
                SELECT * FROM agent_capability_grants
                WHERE tenant_id = ? AND organisation_id = ? AND agent_id = ? AND status = 'ACTIVE'
                ORDER BY granted_at DESC
                """,
                (tenant_id, organisation_id, agent_id),
            ).fetchall()
        else:
            rows = cursor.execute(
                """
                SELECT * FROM agent_capability_grants
                WHERE tenant_id = ? AND organisation_id = ? AND agent_id = ?
                ORDER BY granted_at DESC
                """,
                (tenant_id, organisation_id, agent_id),
            ).fetchall()
        return [self._row_to_grant(r) for r in rows]

    def has_capability(
        self, tenant_id: str, organisation_id: str, agent_id: str, capability_name: str
    ) -> bool:
        """Returns True if agent has an active, non-expired capability grant."""
        grants = self.list_agent_grants(tenant_id, organisation_id, agent_id, active_only=True)
        for g in grants:
            if g.capability_name == capability_name and g.is_valid():
                return True
        return False

    def revoke_capability(
        self,
        tenant_id: str,
        organisation_id: str,
        agent_id: str,
        capability_name: Optional[str] = None,
        capability: Optional[str] = None,
    ) -> int:
        """Revokes all active grants of capability_name for an agent."""
        cap = capability_name or capability
        if not cap:
            raise ValueError("capability_name or capability is required")
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE agent_capability_grants
                SET status = 'REVOKED'
                WHERE tenant_id = ? AND organisation_id = ? AND agent_id = ? AND capability_name = ? AND status = 'ACTIVE'
                """,
                (tenant_id, organisation_id, agent_id, cap),
            )
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Row Mappers
    # ------------------------------------------------------------------

    def _row_to_order(self, row: Any) -> MarketplaceOrder:
        return MarketplaceOrder(
            tenant_id=row["tenant_id"],
            organisation_id=row["organisation_id"],
            order_id=row["order_id"],
            title=row["title"],
            description=row["description"],
            specification_hash=row["specification_hash"],
            required_capability=row["required_capability"],
            bounty_amount=int(row["bounty_amount"]),
            bounty_asset=row["bounty_asset"],
            sla_timeout_seconds=int(row["sla_timeout_seconds"]),
            status=MarketplaceOrderStatus(row["status"]),
            claimed_by_tenant_id=row["claimed_by_tenant_id"],
            claimed_by_org_id=row["claimed_by_org_id"],
            claimed_by_agent_id=row["claimed_by_agent_id"],
            work_order_id=row["work_order_id"],
            deliverable_id=row["deliverable_id"],
            created_at=row["created_at"],
            claimed_at=_iso_or_none(row["claimed_at"]),
            completed_at=_iso_or_none(row["completed_at"]),
        )

    def _row_to_escrow(self, row: Any) -> EscrowAgreement:
        return EscrowAgreement(
            tenant_id=row["tenant_id"],
            organisation_id=row["organisation_id"],
            escrow_id=row["escrow_id"],
            order_id=row["order_id"],
            client_tenant_id=row["client_tenant_id"],
            client_org_id=row["client_org_id"],
            bounty_amount=int(row["bounty_amount"]),
            bounty_asset=row["bounty_asset"],
            provider_tenant_id=row["provider_tenant_id"],
            provider_org_id=row["provider_org_id"],
            status=EscrowStatus(row["status"]),
            client_ledger_tx_id=row["client_ledger_tx_id"],
            provider_ledger_tx_id=row["provider_ledger_tx_id"],
            created_at=row["created_at"],
            released_at=_iso_or_none(row["released_at"]),
        )

    def _row_to_grant(self, row: Any) -> CapabilityGrant:
        return CapabilityGrant(
            tenant_id=row["tenant_id"],
            organisation_id=row["organisation_id"],
            grant_id=row["grant_id"],
            agent_id=row["agent_id"],
            capability_name=row["capability_name"],
            permission_level=row["permission_level"],
            trigger_performance_score=float(row["trigger_performance_score"]),
            granted_by_policy_id=row["granted_by_policy_id"],
            status=CapabilityGrantStatus(row["status"]),
            granted_at=row["granted_at"],
            expires_at=_iso_or_none(row["expires_at"]),
        )
