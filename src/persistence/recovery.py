"""Diagnostics and scanner for Phase 17 crash/restart recovery."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from src.domain.marketplace import EscrowAgreement, EscrowStatus, MarketplaceOrder, MarketplaceOrderStatus


class Phase17RecoveryScanner:
    """Read-only diagnostic scanner for Phase 17 state across process/database restart.

    Fail-Closed Invariants:
    1. UNKNOWN operations are NEVER automatically promoted to SUCCEEDED.
    2. Dangling escrows are NEVER automatically refunded without operator/policy review.
    3. Stale claims are NEVER automatically cancelled without timeout verification.
    4. Unverified revenue events NEVER unlock mission funding.
    """

    def __init__(self, db_or_conn: Any) -> None:
        self.conn = getattr(db_or_conn, "conn", db_or_conn)

    def scan_dangling_escrows(self) -> List[Dict[str, Any]]:
        """Find escrows that are HELD but whose corresponding order is not OPEN or CLAIMED."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        rows = cursor.execute(
            """
            SELECT e.tenant_id, e.organisation_id, e.escrow_id, e.order_id, e.bounty_amount,
                   e.status as escrow_status, o.status as order_status
            FROM marketplace_escrows e
            LEFT JOIN marketplace_orders o
              ON e.tenant_id = o.tenant_id
             AND e.organisation_id = o.organisation_id
             AND e.order_id = o.order_id
            WHERE e.status = 'HELD'
              AND (o.status IS NULL OR o.status NOT IN ('OPEN', 'CLAIMED'))
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def scan_stale_claims(self, timeout_seconds: int = 3600) -> List[Dict[str, Any]]:
        """Find orders that are CLAIMED but have not been completed."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        rows = cursor.execute(
            """
            SELECT tenant_id, organisation_id, order_id, claimed_by_tenant_id,
                   claimed_by_org_id, claimed_by_agent_id, claimed_at
            FROM marketplace_orders
            WHERE status = 'CLAIMED'
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def scan_unknown_consequential_operations(self) -> List[Dict[str, Any]]:
        """Find consequential operations in UNKNOWN state.

        UNKNOWN operations MUST NOT be auto-resolved to SUCCEEDED.
        They require independent evidence or manual operator reconciliation.
        """
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        rows = cursor.execute(
            """
            SELECT id, tenant_id, organisation_id, action_type, target, amount, state, error_message, created_at
            FROM consequential_operations
            WHERE state = 'unknown'
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def scan_unreconciled_revenue(self) -> List[Dict[str, Any]]:
        """Find completed orders that have no corresponding revenue_events entry."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        rows = cursor.execute(
            """
            SELECT o.tenant_id, o.organisation_id, o.order_id, o.work_order_id, o.bounty_amount, o.completed_at
            FROM marketplace_orders o
            LEFT JOIN revenue_events r
              ON o.work_order_id = r.work_order_id
            WHERE o.status = 'COMPLETED'
              AND r.revenue_event_id IS NULL
            """
        ).fetchall()
        return [dict(r) for r in rows]
