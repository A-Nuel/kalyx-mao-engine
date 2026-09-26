"""Phase 21F — durable, authority-bound human execution approvals."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Tuple


@dataclass(frozen=True)
class ExecutionApproval:
    approval_id: str
    tenant_id: str
    organisation_id: str
    principal_id: str
    authority_id: str
    intent_hash: str
    policy_decision_id: str
    policy_decision_hash: str
    issued_at: float
    expires_at: float
    signature: str

    def canonical_string(self) -> str:
        return (
            f"{self.approval_id}:{self.tenant_id}:{self.organisation_id}:"
            f"{self.principal_id}:{self.authority_id}:{self.intent_hash}:"
            f"{self.policy_decision_id}:{self.policy_decision_hash}:"
            f"{self.issued_at:.4f}:{self.expires_at:.4f}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "principal_id": self.principal_id,
            "authority_id": self.authority_id,
            "intent_hash": self.intent_hash,
            "policy_decision_id": self.policy_decision_id,
            "policy_decision_hash": self.policy_decision_hash,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "signature": self.signature,
        }


class ExecutionApprovalManager:
    """Issue and durably consume approvals bound to exact execution authority."""

    def __init__(self, db_or_conn: Any, secret_key: str) -> None:
        if not secret_key:
            raise ValueError("secret_key is required")
        self.conn = getattr(db_or_conn, "conn", db_or_conn)
        self.secret_key = secret_key.encode("utf-8")

    @staticmethod
    def decision_hash(decision: Any) -> str:
        if isinstance(decision, str):
            raw = decision
        elif hasattr(decision, "to_audit_dict"):
            raw = json.dumps(decision.to_audit_dict(), sort_keys=True, separators=(",", ":"))
        elif hasattr(decision, "model_dump"):
            raw = json.dumps(decision.model_dump(), sort_keys=True, default=str, separators=(",", ":"))
        else:
            raw = json.dumps(decision, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def issue(
        self,
        *,
        tenant_id: str,
        organisation_id: str,
        principal_id: str,
        authority_id: str,
        intent_hash: str,
        policy_decision_id: str,
        policy_decision: Any,
        ttl_seconds: float = 900.0,
        current_time: Optional[float] = None,
    ) -> ExecutionApproval:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = time.time() if current_time is None else current_time
        decision_hash = self.decision_hash(policy_decision)
        approval_id = f"exec-appr-{uuid.uuid4().hex[:16]}"
        approval = ExecutionApproval(
            approval_id=approval_id,
            tenant_id=tenant_id,
            organisation_id=organisation_id,
            principal_id=principal_id,
            authority_id=authority_id,
            intent_hash=intent_hash,
            policy_decision_id=policy_decision_id,
            policy_decision_hash=decision_hash,
            issued_at=now,
            expires_at=now + ttl_seconds,
            signature="",
        )
        signature = hmac.new(
            self.secret_key,
            approval.canonical_string().encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        approval = ExecutionApproval(**{**approval.to_dict(), "signature": signature})
        with self.conn:
            self.conn.execute(
                """INSERT INTO execution_approvals (
                    approval_id, tenant_id, organisation_id, principal_id,
                    authority_id, intent_hash, policy_decision_id,
                    policy_decision_hash, issued_at, expires_at, signature, consumed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    approval.approval_id, approval.tenant_id, approval.organisation_id,
                    approval.principal_id, approval.authority_id, approval.intent_hash,
                    approval.policy_decision_id, approval.policy_decision_hash,
                    approval.issued_at, approval.expires_at, approval.signature,
                ),
            )
        return approval

    def verify_and_consume(
        self,
        approval: ExecutionApproval,
        *,
        tenant_id: str,
        organisation_id: str,
        principal_id: str,
        authority_id: str,
        intent_hash: str,
        policy_decision_id: str,
        policy_decision: Any,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        now = time.time() if current_time is None else current_time
        if approval.tenant_id != tenant_id or approval.organisation_id != organisation_id:
            return False, "approval scope mismatch"
        if approval.principal_id != principal_id:
            return False, "approval principal mismatch"
        if approval.authority_id != authority_id:
            return False, "approval execution authority mismatch"
        if approval.intent_hash != intent_hash:
            return False, "approval intent hash mismatch"
        if approval.policy_decision_id != policy_decision_id:
            return False, "approval policy decision mismatch"
        if now > approval.expires_at:
            return False, "approval expired"

        expected_hash = self.decision_hash(policy_decision)
        if approval.policy_decision_hash != expected_hash:
            return False, "approval policy decision hash mismatch"

        expected_signature = hmac.new(
            self.secret_key,
            approval.canonical_string().encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(approval.signature, expected_signature):
            return False, "approval signature invalid"

        row = self.conn.execute(
            "SELECT consumed FROM execution_approvals WHERE approval_id = ?",
            (approval.approval_id,),
        ).fetchone()
        if row is None:
            return False, "approval is not durable"
        if row[0] if isinstance(row, tuple) else row["consumed"]:
            return False, "approval already consumed"

        with self.conn:
            updated = self.conn.execute(
                "UPDATE execution_approvals SET consumed = 1 WHERE approval_id = ? AND consumed = 0",
                (approval.approval_id,),
            )
            if updated.rowcount != 1:
                return False, "approval replay detected"
        return True, None
