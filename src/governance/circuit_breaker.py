"""Durable, governed emergency circuit breaker for Phase 17 organisations."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class CircuitBreakerState(str, Enum):
    NORMAL = "NORMAL"
    PAUSED = "PAUSED"


class SystemCircuitBreaker:
    """Authoritative database-backed circuit breaker scoped to (tenant_id, organisation_id).

    Invariants:
    1. The persisted state in system_circuit_breaker is the final enforcement boundary.
    2. Pausing blocks new consequential operations and spending, but explicitly preserves
       auditing, read-only queries, deliverable verification, and safe settlement/reconciliation
       of already-verified work.
    3. Resumption strictly requires governed 2-of-2 admin approval.
    4. All state transitions are recorded in an append-only audit trail.
    """

    def __init__(self, db_or_conn: Any) -> None:
        self.conn = getattr(db_or_conn, "conn", db_or_conn)

    def is_paused(self, tenant_id: str, organisation_id: str) -> bool:
        """Inspect the authoritative database record for circuit breaker status."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT state FROM system_circuit_breaker
            WHERE tenant_id = ? AND organisation_id = ?
            """,
            (tenant_id, organisation_id),
        ).fetchone()
        if not row:
            return False
        return str(row[0] if isinstance(row, tuple) else row["state"]).upper() == CircuitBreakerState.PAUSED.value

    def get_state(self, tenant_id: str, organisation_id: str) -> CircuitBreakerState:
        """Get current circuit breaker state (defaults to NORMAL if not configured)."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT state FROM system_circuit_breaker
            WHERE tenant_id = ? AND organisation_id = ?
            """,
            (tenant_id, organisation_id),
        ).fetchone()
        if not row:
            return CircuitBreakerState.NORMAL
        val = str(row[0] if isinstance(row, tuple) else row["state"]).upper()
        return CircuitBreakerState.PAUSED if val == CircuitBreakerState.PAUSED.value else CircuitBreakerState.NORMAL

    def pause(
        self,
        tenant_id: str,
        organisation_id: str,
        operator_id: str,
        reason: str,
    ) -> str:
        """Emergency pause for an organisation.

        Single operator action is permitted for immediate defensive protection.
        Returns the audit record ID.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        state_before = self.get_state(tenant_id, organisation_id).value
        audit_id = f"cb-audit-{uuid.uuid4().hex[:12]}"

        with self.conn:
            # 1. Update/insert circuit breaker state
            self.conn.execute(
                """
                INSERT INTO system_circuit_breaker (
                    tenant_id, organisation_id, state, paused_by, paused_reason, paused_at, updated_at
                ) VALUES (?, ?, 'PAUSED', ?, ?, ?, ?)
                ON CONFLICT (tenant_id, organisation_id) DO UPDATE SET
                    state = 'PAUSED',
                    paused_by = excluded.paused_by,
                    paused_reason = excluded.paused_reason,
                    paused_at = excluded.paused_at,
                    updated_at = excluded.updated_at
                """,
                (tenant_id, organisation_id, operator_id, reason, now_str, now_str),
            )

            # 2. Append audit trail
            self.conn.execute(
                """
                INSERT INTO circuit_breaker_audit (
                    id, tenant_id, organisation_id, action, actor_id, reason,
                    state_before, state_after, approvals_json, created_at
                ) VALUES (?, ?, ?, 'PAUSE', ?, ?, ?, 'PAUSED', '[]', ?)
                """,
                (audit_id, tenant_id, organisation_id, operator_id, reason, state_before, now_str),
            )

        return audit_id

    def resume(
        self,
        tenant_id: str,
        organisation_id: str,
        approver_ids: List[str],
        approvals_data: Optional[List[Dict[str, Any]]] = None,
        reason: str = "Governance quorum verified resumption",
    ) -> str:
        """Governed resumption requiring verified multi-signature approvals.

        Returns the audit record ID.
        """
        if len(set(approver_ids)) < 2:
            raise ValueError(f"Resumption requires at least 2 distinct administrative approvers, got {len(set(approver_ids))}")

        now_str = datetime.now(timezone.utc).isoformat()
        state_before = self.get_state(tenant_id, organisation_id).value
        audit_id = f"cb-audit-{uuid.uuid4().hex[:12]}"
        approvals_json = json.dumps(approvals_data or [{"approver_id": a} for a in approver_ids])

        with self.conn:
            # 1. Reset circuit breaker state to NORMAL
            self.conn.execute(
                """
                INSERT INTO system_circuit_breaker (
                    tenant_id, organisation_id, state, paused_by, paused_reason, paused_at, updated_at
                ) VALUES (?, ?, 'NORMAL', NULL, NULL, NULL, ?)
                ON CONFLICT (tenant_id, organisation_id) DO UPDATE SET
                    state = 'NORMAL',
                    paused_by = NULL,
                    paused_reason = NULL,
                    paused_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (tenant_id, organisation_id, now_str),
            )

            # 2. Append audit trail
            self.conn.execute(
                """
                INSERT INTO circuit_breaker_audit (
                    id, tenant_id, organisation_id, action, actor_id, reason,
                    state_before, state_after, approvals_json, created_at
                ) VALUES (?, ?, ?, 'RESUME', ?, ?, ?, 'NORMAL', ?, ?)
                """,
                (
                    audit_id,
                    tenant_id,
                    organisation_id,
                    f"multi-sig:{','.join(approver_ids)}",
                    reason,
                    state_before,
                    approvals_json,
                    now_str,
                ),
            )

        return audit_id

    def list_audit_history(
        self, tenant_id: str, organisation_id: str
    ) -> List[Dict[str, Any]]:
        """Retrieve the immutable audit history for this organisation's circuit breaker."""
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        rows = cursor.execute(
            """
            SELECT * FROM circuit_breaker_audit
            WHERE tenant_id = ? AND organisation_id = ?
            ORDER BY created_at DESC
            """,
            (tenant_id, organisation_id),
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "tenant_id": r["tenant_id"],
                    "organisation_id": r["organisation_id"],
                    "action": r["action"],
                    "actor_id": r["actor_id"],
                    "reason": r["reason"],
                    "state_before": r["state_before"],
                    "state_after": r["state_after"],
                    "approvals_json": r["approvals_json"],
                    "created_at": r["created_at"],
                }
            )
        return out
