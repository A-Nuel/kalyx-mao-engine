"""Multi-signature administrative governance for sensitive operations and circuit-breaker overrides."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class AdminApproval:
    """Cryptographically signed administrative approval record."""
    approval_id: str
    tenant_id: str
    action_type: str
    target_id: str
    payload_hash: str
    approver_id: str
    expires_at: float
    signature: str

    def canonical_string(self) -> str:
        return f"{self.approval_id}:{self.tenant_id}:{self.action_type}:{self.target_id}:{self.payload_hash}:{self.approver_id}:{self.expires_at:.4f}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "tenant_id": self.tenant_id,
            "action_type": self.action_type,
            "target_id": self.target_id,
            "payload_hash": self.payload_hash,
            "approver_id": self.approver_id,
            "expires_at": self.expires_at,
            "signature": self.signature,
        }


class AdminGovernanceManager:
    """Manages multi-signature approval generation, verification, and consumption.

    Enforces:
    1. Cryptographic HMAC-SHA256 verification using system secret.
    2. Strict payload hash binding (approval cannot be re-applied to different action).
    3. Expiration enforcement (rejection if expires_at < current_time).
    4. Replay protection (consumed approvals cannot be re-used).
    5. Cross-tenant isolation (tenant_id mismatch rejected).
    6. Quorum integrity (requires distinct authorized approvers; no single-agent self-approval).
    """

    def __init__(self, db_or_conn: Any, secret_key: str) -> None:
        if not secret_key:
            raise ValueError("secret_key is required for AdminGovernanceManager")
        self.conn = getattr(db_or_conn, "conn", db_or_conn)
        self.secret_key = secret_key

    @staticmethod
    def compute_payload_hash(payload: Any) -> str:
        """Compute deterministic SHA-256 hash of a payload dictionary or object."""
        if isinstance(payload, str):
            canonical = payload
        else:
            canonical = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def create_approval(
        self,
        tenant_id: str,
        action_type: str,
        target_id: str,
        payload: Any,
        approver_id: str,
        ttl_seconds: float = 900.0,
    ) -> AdminApproval:
        """Create and sign an administrative approval record."""
        approval_id = f"appr-{uuid.uuid4().hex[:12]}"
        payload_hash = self.compute_payload_hash(payload)
        expires_at = time.time() + ttl_seconds

        msg = f"{approval_id}:{tenant_id}:{action_type}:{target_id}:{payload_hash}:{approver_id}:{expires_at:.4f}"
        sig = hmac.new(
            self.secret_key.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return AdminApproval(
            approval_id=approval_id,
            tenant_id=tenant_id,
            action_type=action_type,
            target_id=target_id,
            payload_hash=payload_hash,
            approver_id=approver_id,
            expires_at=expires_at,
            signature=sig,
        )

    def verify_single_approval(
        self,
        approval: AdminApproval,
        expected_tenant_id: str,
        expected_action_type: str,
        expected_target_id: str,
        actual_payload: Any,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Verify an individual approval's validity."""
        now = current_time if current_time is not None else time.time()

        # 1. Tenant boundary check
        if approval.tenant_id != expected_tenant_id:
            return False, f"Approval tenant_id '{approval.tenant_id}' does not match expected '{expected_tenant_id}'"

        # 2. Action type check
        if approval.action_type != expected_action_type:
            return False, f"Approval action_type '{approval.action_type}' does not match expected '{expected_action_type}'"

        # 3. Target ID check
        if approval.target_id != expected_target_id:
            return False, f"Approval target_id '{approval.target_id}' does not match expected '{expected_target_id}'"

        # 4. Expiry check
        if approval.expires_at < now:
            return False, f"Approval '{approval.approval_id}' has expired (expired at {approval.expires_at}, now {now})"

        # 5. Payload hash binding check
        expected_hash = self.compute_payload_hash(actual_payload)
        if approval.payload_hash != expected_hash:
            return False, f"Payload hash mismatch: approval bound to {approval.payload_hash} vs actual {expected_hash}"

        # 6. Cryptographic signature check
        expected_msg = approval.canonical_string()
        expected_sig = hmac.new(
            self.secret_key.encode("utf-8"),
            expected_msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(approval.signature, expected_sig):
            return False, "Cryptographic signature verification failed (forged or tampered approval)"

        # 7. Replay / consumption check in database
        cursor = self.conn.cursor() if hasattr(self.conn, "cursor") else self.conn
        row = cursor.execute(
            """
            SELECT consumed FROM admin_approvals
            WHERE approval_id = ?
            """,
            (approval.approval_id,),
        ).fetchone()
        if row and (row[0] if isinstance(row, tuple) else row["consumed"]) == 1:
            return False, f"Approval '{approval.approval_id}' has already been consumed (replay attempt)"

        return True, None

    def verify_and_consume_quorum(
        self,
        approvals: List[AdminApproval],
        required_approvals: int,
        expected_tenant_id: str,
        expected_action_type: str,
        expected_target_id: str,
        actual_payload: Any,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Verify that a set of approvals satisfies strict quorum and mark them consumed."""
        if len(approvals) < required_approvals:
            return False, f"Insufficient approvals: required {required_approvals}, provided {len(approvals)}"

        # Verify distinct approvers (anti-self-approval)
        approvers = [a.approver_id for a in approvals]
        if len(set(approvers)) < len(approvals):
            return False, f"Duplicate approver detected: approvers must be unique distinct identities ({approvers})"

        now = current_time if current_time is not None else time.time()
        for app in approvals:
            valid, reason = self.verify_single_approval(
                approval=app,
                expected_tenant_id=expected_tenant_id,
                expected_action_type=expected_action_type,
                expected_target_id=expected_target_id,
                actual_payload=actual_payload,
                current_time=now,
            )
            if not valid:
                return False, reason

        # Atomically record and mark consumed in database
        now_str = datetime.now(timezone.utc).isoformat()
        with self.conn:
            for app in approvals:
                self.conn.execute(
                    """
                    INSERT INTO admin_approvals (
                        approval_id, tenant_id, action_type, target_id, payload_hash,
                        approver_id, expires_at, signature, consumed, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT (approval_id) DO UPDATE SET consumed = 1
                    """,
                    (
                        app.approval_id,
                        app.tenant_id,
                        app.action_type,
                        app.target_id,
                        app.payload_hash,
                        app.approver_id,
                        app.expires_at,
                        app.signature,
                        now_str,
                    ),
                )

        return True, None
