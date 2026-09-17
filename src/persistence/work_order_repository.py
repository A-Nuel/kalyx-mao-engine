"""Durable multi-tenant persistence repository for Phase 16 work orders, deliverables, receipts, and revenue events."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.domain.enums import CurrencyAsset, DeliverableStatus, WorkOrderStatus
from src.domain.work_order import (
    RevenueEvent,
    WorkDeliverable,
    WorkDeliverableReceipt,
    WorkOrder,
)


class WorkOrderRepository:
    """Multi-tenant, organisation-scoped persistence for WorkOrders and economic settlement."""

    def __init__(self, db_or_conn: Any) -> None:
        self.conn = getattr(db_or_conn, "conn", db_or_conn)

    # ------------------------------------------------------------------
    # Work Orders
    # ------------------------------------------------------------------

    def save_work_order(self, work_order: WorkOrder) -> None:
        created_str = (
            work_order.created_at.isoformat()
            if isinstance(work_order.created_at, datetime)
            else str(work_order.created_at)
        )
        metadata_str = json.dumps(work_order.metadata or {})
        bounty_asset_val = (
            work_order.bounty_asset.value
            if isinstance(work_order.bounty_asset, CurrencyAsset)
            else str(work_order.bounty_asset)
        )
        status_val = (
            work_order.status.value
            if isinstance(work_order.status, WorkOrderStatus)
            else str(work_order.status)
        )

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO work_orders (
                    tenant_id, organisation_id, work_order_id, client_id,
                    title, description, deliverable_type, required_orbio_credits,
                    bounty_amount, bounty_asset, deadline_seconds, status,
                    created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, organisation_id, work_order_id) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    status = excluded.status,
                    metadata_json = excluded.metadata_json
                """,
                (
                    work_order.tenant_id,
                    work_order.organisation_id,
                    work_order.work_order_id,
                    work_order.client_id,
                    work_order.title,
                    work_order.description,
                    work_order.deliverable_type,
                    work_order.required_orbio_credits,
                    work_order.bounty_amount,
                    bounty_asset_val,
                    work_order.deadline_seconds,
                    status_val,
                    created_str,
                    metadata_str,
                ),
            )

    def get_work_order(
        self, tenant_id: str, organisation_id: str, work_order_id: str
    ) -> Optional[WorkOrder]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT * FROM work_orders
            WHERE tenant_id = ? AND organisation_id = ? AND work_order_id = ?
            """,
            (tenant_id, organisation_id, work_order_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_work_order(row)

    def list_work_orders(
        self,
        tenant_id: str,
        organisation_id: str,
        status: Optional[WorkOrderStatus] = None,
    ) -> List[WorkOrder]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        if status is not None:
            rows = cursor.execute(
                """
                SELECT * FROM work_orders
                WHERE tenant_id = ? AND organisation_id = ? AND status = ?
                ORDER BY created_at DESC
                """,
                (tenant_id, organisation_id, status.value),
            ).fetchall()
        else:
            rows = cursor.execute(
                """
                SELECT * FROM work_orders
                WHERE tenant_id = ? AND organisation_id = ?
                ORDER BY created_at DESC
                """,
                (tenant_id, organisation_id),
            ).fetchall()
        return [self._row_to_work_order(r) for r in rows]

    def _row_to_work_order(self, row: Any) -> WorkOrder:
        raw_created = row["created_at"]
        created_at = (
            datetime.fromisoformat(raw_created)
            if isinstance(raw_created, str)
            else raw_created
        )
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        return WorkOrder(
            tenant_id=row["tenant_id"],
            organisation_id=row["organisation_id"],
            work_order_id=row["work_order_id"],
            client_id=row["client_id"],
            title=row["title"],
            description=row["description"],
            deliverable_type=row["deliverable_type"],
            required_orbio_credits=int(row["required_orbio_credits"]),
            bounty_amount=int(row["bounty_amount"]),
            bounty_asset=CurrencyAsset(row["bounty_asset"]),
            deadline_seconds=int(row["deadline_seconds"]),
            status=WorkOrderStatus(row["status"]),
            created_at=created_at,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Deliverables
    # ------------------------------------------------------------------

    def save_deliverable(
        self, tenant_id: str, organisation_id: str, deliverable: WorkDeliverable
    ) -> None:
        created_str = (
            deliverable.created_at.isoformat()
            if isinstance(deliverable.created_at, datetime)
            else str(deliverable.created_at)
        )
        payload_str = json.dumps(deliverable.content_payload)
        telemetry_str = json.dumps(deliverable.execution_telemetry or {})

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO work_deliverables (
                    tenant_id, organisation_id, deliverable_id, work_order_id,
                    producer_agent_id, content_payload_json, content_hash,
                    orbio_credits_consumed, execution_telemetry_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, organisation_id, deliverable_id) DO UPDATE SET
                    content_payload_json = excluded.content_payload_json,
                    content_hash = excluded.content_hash,
                    execution_telemetry_json = excluded.execution_telemetry_json
                """,
                (
                    tenant_id,
                    organisation_id,
                    deliverable.deliverable_id,
                    deliverable.work_order_id,
                    deliverable.producer_agent_id,
                    payload_str,
                    deliverable.content_hash,
                    deliverable.orbio_credits_consumed,
                    telemetry_str,
                    created_str,
                ),
            )

    def get_deliverable(
        self, tenant_id: str, organisation_id: str, deliverable_id: str
    ) -> Optional[WorkDeliverable]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT * FROM work_deliverables
            WHERE tenant_id = ? AND organisation_id = ? AND deliverable_id = ?
            """,
            (tenant_id, organisation_id, deliverable_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_deliverable(row)

    def get_deliverable_by_work_order(
        self, tenant_id: str, organisation_id: str, work_order_id: str
    ) -> Optional[WorkDeliverable]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT * FROM work_deliverables
            WHERE tenant_id = ? AND organisation_id = ? AND work_order_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (tenant_id, organisation_id, work_order_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_deliverable(row)

    def _row_to_deliverable(self, row: Any) -> WorkDeliverable:
        raw_created = row["created_at"]
        created_at = (
            datetime.fromisoformat(raw_created)
            if isinstance(raw_created, str)
            else raw_created
        )
        return WorkDeliverable(
            deliverable_id=row["deliverable_id"],
            work_order_id=row["work_order_id"],
            producer_agent_id=row["producer_agent_id"],
            content_payload=json.loads(row["content_payload_json"]),
            content_hash=row["content_hash"],
            orbio_credits_consumed=int(row["orbio_credits_consumed"]),
            execution_telemetry=json.loads(row["execution_telemetry_json"])
            if row["execution_telemetry_json"]
            else {},
            created_at=created_at,
        )

    # ------------------------------------------------------------------
    # Receipts
    # ------------------------------------------------------------------

    def save_receipt(
        self,
        tenant_id: str,
        organisation_id: str,
        receipt: WorkDeliverableReceipt,
    ) -> None:
        verified_str = (
            receipt.verified_at.isoformat()
            if isinstance(receipt.verified_at, datetime)
            else str(receipt.verified_at)
        )
        status_val = (
            receipt.status.value
            if isinstance(receipt.status, DeliverableStatus)
            else str(receipt.status)
        )

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO work_deliverable_receipts (
                    tenant_id, organisation_id, receipt_id, work_order_id,
                    deliverable_id, content_hash, evidence_hash, verifier_identity,
                    status, verification_notes, verified_at, hmac_signature
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, organisation_id, receipt_id) DO UPDATE SET
                    status = excluded.status,
                    verification_notes = excluded.verification_notes,
                    hmac_signature = excluded.hmac_signature
                """,
                (
                    tenant_id,
                    organisation_id,
                    receipt.receipt_id,
                    receipt.work_order_id,
                    receipt.deliverable_id,
                    receipt.content_hash,
                    receipt.evidence_hash,
                    receipt.verifier_identity,
                    status_val,
                    receipt.verification_notes,
                    verified_str,
                    receipt.hmac_signature,
                ),
            )

    def get_receipt(
        self, tenant_id: str, organisation_id: str, receipt_id: str
    ) -> Optional[WorkDeliverableReceipt]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT * FROM work_deliverable_receipts
            WHERE tenant_id = ? AND organisation_id = ? AND receipt_id = ?
            """,
            (tenant_id, organisation_id, receipt_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_receipt(row)

    def get_receipt_for_work_order(
        self, tenant_id: str, organisation_id: str, work_order_id: str
    ) -> Optional[WorkDeliverableReceipt]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT * FROM work_deliverable_receipts
            WHERE tenant_id = ? AND organisation_id = ? AND work_order_id = ?
            ORDER BY verified_at DESC LIMIT 1
            """,
            (tenant_id, organisation_id, work_order_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_receipt(row)

    def _row_to_receipt(self, row: Any) -> WorkDeliverableReceipt:
        raw_verified = row["verified_at"]
        verified_at = (
            datetime.fromisoformat(raw_verified)
            if isinstance(raw_verified, str)
            else raw_verified
        )
        return WorkDeliverableReceipt(
            receipt_id=row["receipt_id"],
            work_order_id=row["work_order_id"],
            deliverable_id=row["deliverable_id"],
            content_hash=row["content_hash"],
            evidence_hash=row["evidence_hash"],
            verifier_identity=row["verifier_identity"],
            status=DeliverableStatus(row["status"]),
            verification_notes=row["verification_notes"] or "",
            verified_at=verified_at,
            hmac_signature=row["hmac_signature"],
        )

    # ------------------------------------------------------------------
    # Revenue Events
    # ------------------------------------------------------------------

    def save_revenue_event(
        self, tenant_id: str, organisation_id: str, event: RevenueEvent
    ) -> None:
        settled_str = (
            event.settled_at.isoformat()
            if isinstance(event.settled_at, datetime)
            else str(event.settled_at)
        )

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO revenue_events (
                    tenant_id, organisation_id, revenue_event_id, work_order_id,
                    gross_revenue_usdg, direct_expense_usdg, net_surplus_usdg,
                    orbio_credits_consumed, allocated_to_mission_budget,
                    allocated_to_reserve, settled_at, ledger_tx_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, organisation_id, revenue_event_id) DO NOTHING
                """,
                (
                    tenant_id,
                    organisation_id,
                    event.revenue_event_id,
                    event.work_order_id,
                    event.gross_revenue_usdg,
                    event.direct_expense_usdg,
                    event.net_surplus_usdg,
                    event.orbio_credits_consumed,
                    event.allocated_to_mission_budget,
                    event.allocated_to_reserve,
                    settled_str,
                    event.ledger_tx_id,
                ),
            )

    def list_revenue_events(
        self, tenant_id: str, organisation_id: str
    ) -> List[RevenueEvent]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        rows = cursor.execute(
            """
            SELECT * FROM revenue_events
            WHERE tenant_id = ? AND organisation_id = ?
            ORDER BY settled_at DESC
            """,
            (tenant_id, organisation_id),
        ).fetchall()
        return [self._row_to_revenue_event(r) for r in rows]

    def _row_to_revenue_event(self, row: Any) -> RevenueEvent:
        raw_settled = row["settled_at"]
        settled_at = (
            datetime.fromisoformat(raw_settled)
            if isinstance(raw_settled, str)
            else raw_settled
        )
        return RevenueEvent(
            revenue_event_id=row["revenue_event_id"],
            work_order_id=row["work_order_id"],
            gross_revenue_usdg=int(row["gross_revenue_usdg"]),
            direct_expense_usdg=int(row["direct_expense_usdg"]),
            net_surplus_usdg=int(row["net_surplus_usdg"]),
            orbio_credits_consumed=int(row["orbio_credits_consumed"]),
            allocated_to_mission_budget=int(row["allocated_to_mission_budget"]),
            allocated_to_reserve=int(row["allocated_to_reserve"]),
            settled_at=settled_at,
            ledger_tx_id=row["ledger_tx_id"],
        )

    # ------------------------------------------------------------------
    # Mission Lineage
    # ------------------------------------------------------------------

    def record_mission_lineage(
        self,
        tenant_id: str,
        organisation_id: str,
        mission_id: str,
        parent_mission_id: Optional[str],
        funding_source: str,
        funding_amount_usdg: int,
    ) -> None:
        now_str = datetime.utcnow().isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO mission_lineage (
                    tenant_id, organisation_id, mission_id, parent_mission_id,
                    funding_source, funding_amount_usdg, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, organisation_id, mission_id) DO UPDATE SET
                    parent_mission_id = excluded.parent_mission_id,
                    funding_source = excluded.funding_source,
                    funding_amount_usdg = excluded.funding_amount_usdg
                """,
                (
                    tenant_id,
                    organisation_id,
                    mission_id,
                    parent_mission_id,
                    funding_source,
                    funding_amount_usdg,
                    now_str,
                ),
            )

    def get_mission_lineage(
        self, tenant_id: str, organisation_id: str, mission_id: str
    ) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT * FROM mission_lineage
            WHERE tenant_id = ? AND organisation_id = ? AND mission_id = ?
            """,
            (tenant_id, organisation_id, mission_id),
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def list_mission_lineage(
        self, tenant_id: str, organisation_id: str
    ) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        rows = cursor.execute(
            """
            SELECT * FROM mission_lineage
            WHERE tenant_id = ? AND organisation_id = ?
            ORDER BY created_at ASC
            """,
            (tenant_id, organisation_id),
        ).fetchall()
        return [dict(r) for r in rows]
