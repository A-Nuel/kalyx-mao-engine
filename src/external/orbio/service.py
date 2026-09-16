"""Governed external economy service.

All Orbio operations go through authorization + tenant isolation.
Agents cannot obtain the MCP/gateway client.
Orbio telemetry never directly mutates Kalyx performance.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

from src.external.models import (
    ExternalBalance,
    ExternalKeyStatus,
    ExternalProviderOutcome,
    InferenceReceipt,
    InferenceRequest,
    KeyLifecycleIntent,
    KeyLifecycleOperation,
    KeyLifecycleReceipt,
)
from src.external.orbio.adapter import OrbioAdapter


class EventAppender(Protocol):
    def append_event(
        self,
        actor_id: str,
        event_type: str,
        entity_id: str,
        payload: Dict[str, Any],
        tenant_id: Optional[str] = None,
        organisation_id: Optional[str] = None,
    ) -> Any:
        ...


@dataclass
class ExternalKeyRecord:
    """Persisted non-secret key metadata only."""
    key_id: str
    tenant_id: str
    organisation_id: str
    status: str
    account_id: Optional[str]
    evidence_hash: Optional[str]
    created_at: str
    revoked_at: Optional[str] = None
    rate_limit_json: Optional[str] = None


class ExternalKeyRepository:
    """SQLite-compatible repository for external key metadata (no secrets)."""

    def __init__(self, conn: Any):
        self.conn = conn
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS external_keys (
                key_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                organisation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                account_id TEXT,
                evidence_hash TEXT,
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                rate_limit_json TEXT,
                PRIMARY KEY (tenant_id, organisation_id, key_id)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_external_keys_tenant_org ON external_keys(tenant_id, organisation_id)"
        )
        self.conn.commit()

    def save(self, record: ExternalKeyRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO external_keys (
                key_id, tenant_id, organisation_id, status, account_id,
                evidence_hash, created_at, revoked_at, rate_limit_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, organisation_id, key_id) DO UPDATE SET
                status=excluded.status,
                account_id=excluded.account_id,
                evidence_hash=excluded.evidence_hash,
                revoked_at=excluded.revoked_at,
                rate_limit_json=excluded.rate_limit_json
            """,
            (
                record.key_id,
                record.tenant_id,
                record.organisation_id,
                record.status,
                record.account_id,
                record.evidence_hash,
                record.created_at,
                record.revoked_at,
                record.rate_limit_json,
            ),
        )
        self.conn.commit()

    def list_keys(self, tenant_id: str, organisation_id: str) -> List[ExternalKeyRecord]:
        rows = self.conn.execute(
            "SELECT * FROM external_keys WHERE tenant_id = ? AND organisation_id = ? ORDER BY created_at",
            (tenant_id, organisation_id),
        ).fetchall()
        return [self._row(r) for r in rows]

    def get_key(self, tenant_id: str, organisation_id: str, key_id: str) -> Optional[ExternalKeyRecord]:
        row = self.conn.execute(
            "SELECT * FROM external_keys WHERE tenant_id = ? AND organisation_id = ? AND key_id = ?",
            (tenant_id, organisation_id, key_id),
        ).fetchone()
        return self._row(row) if row else None

    def _row(self, row: Any) -> ExternalKeyRecord:
        return ExternalKeyRecord(
            key_id=row["key_id"],
            tenant_id=row["tenant_id"],
            organisation_id=row["organisation_id"],
            status=row["status"],
            account_id=row["account_id"],
            evidence_hash=row["evidence_hash"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
            rate_limit_json=row["rate_limit_json"],
        )


class ExternalEconomyService:
    """Orchestrates authorized Orbio operations without exposing credentials to agents."""

    def __init__(
        self,
        provider: OrbioAdapter,
        key_repo: Optional[ExternalKeyRepository] = None,
        event_store: Optional[EventAppender] = None,
    ):
        self.provider = provider
        self.key_repo = key_repo
        self.event_store = event_store

    def _audit(self, actor_id: str, event_type: str, entity_id: str, payload: Dict[str, Any], tenant_id: str, organisation_id: str) -> None:
        if self.event_store is None:
            return
        # Defensive: never allow secret-like keys in audit payload
        safe = {k: v for k, v in payload.items() if k.lower() not in {"api_key", "secret", "one_time_secret", "key", "authorization"}}
        try:
            self.event_store.append_event(
                actor_id=actor_id,
                event_type=event_type,
                entity_id=entity_id,
                payload=safe,
                tenant_id=tenant_id,
                organisation_id=organisation_id,
            )
        except TypeError:
            self.event_store.append_event(actor_id=actor_id, event_type=event_type, entity_id=entity_id, payload=safe)

    def get_balance(self, *, tenant_id: str, organisation_id: str, actor_id: str = "OPERATOR") -> ExternalBalance:
        bal = self.provider.get_balance(tenant_id=tenant_id, organisation_id=organisation_id)
        self._audit(
            actor_id,
            "ORBIO_BALANCE_READ",
            organisation_id,
            {"available": bal.available, "used": bal.used, "currency": bal.currency, "evidence_hash": bal.evidence_hash()},
            tenant_id,
            organisation_id,
        )
        return bal

    def list_keys(self, *, tenant_id: str, organisation_id: str) -> List[ExternalKeyRecord]:
        if self.key_repo is None:
            return []
        return self.key_repo.list_keys(tenant_id, organisation_id)

    def create_key(
        self,
        *,
        tenant_id: str,
        organisation_id: str,
        actor_principal_id: str,
        authorization_token: str,
        mission_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> KeyLifecycleReceipt:
        if not authorization_token:
            raise PermissionError("Key creation requires a Kalyx authorization token")
        intent = KeyLifecycleIntent(
            tenant_id=tenant_id,
            organisation_id=organisation_id,
            operation=KeyLifecycleOperation.CREATE,
            actor_principal_id=actor_principal_id,
            mission_id=mission_id,
            agent_id=agent_id,
            authorization_token=authorization_token,
            idempotency_key=str(uuid.uuid4()),
        )
        receipt = self.provider.execute_key_lifecycle(intent)
        if receipt.outcome == ExternalProviderOutcome.SUCCESS and receipt.key_id and self.key_repo:
            now = datetime.now(timezone.utc).isoformat()
            self.key_repo.save(
                ExternalKeyRecord(
                    key_id=receipt.key_id,
                    tenant_id=tenant_id,
                    organisation_id=organisation_id,
                    status="active",
                    account_id=None,
                    evidence_hash=receipt.evidence_hash,
                    created_at=now,
                )
            )
        self._audit(
            actor_principal_id,
            "ORBIO_KEY_CREATED",
            receipt.key_id or organisation_id,
            receipt.audit_safe_dict(),
            tenant_id,
            organisation_id,
        )
        return receipt

    def revoke_key(
        self,
        *,
        tenant_id: str,
        organisation_id: str,
        key_id: str,
        actor_principal_id: str,
        authorization_token: str,
    ) -> KeyLifecycleReceipt:
        if not authorization_token:
            raise PermissionError("Key revocation requires a Kalyx authorization token")
        # Tenant isolation: key must belong to this tenant/org if tracked
        if self.key_repo:
            existing = self.key_repo.get_key(tenant_id, organisation_id, key_id)
            if existing is None:
                # Deny cross-tenant probing of unknown keys for this org
                raise PermissionError("Key not found for this organisation")
        intent = KeyLifecycleIntent(
            tenant_id=tenant_id,
            organisation_id=organisation_id,
            operation=KeyLifecycleOperation.REVOKE,
            actor_principal_id=actor_principal_id,
            key_id=key_id,
            authorization_token=authorization_token,
            idempotency_key=str(uuid.uuid4()),
        )
        receipt = self.provider.execute_key_lifecycle(intent)
        if receipt.outcome in {ExternalProviderOutcome.SUCCESS, ExternalProviderOutcome.KEY_REVOKED} and self.key_repo:
            now = datetime.now(timezone.utc).isoformat()
            rec = self.key_repo.get_key(tenant_id, organisation_id, key_id)
            if rec:
                self.key_repo.save(
                    ExternalKeyRecord(
                        key_id=key_id,
                        tenant_id=tenant_id,
                        organisation_id=organisation_id,
                        status="revoked",
                        account_id=rec.account_id,
                        evidence_hash=receipt.evidence_hash,
                        created_at=rec.created_at,
                        revoked_at=now,
                    )
                )
        self._audit(
            actor_principal_id,
            "ORBIO_KEY_REVOKED",
            key_id,
            receipt.audit_safe_dict(),
            tenant_id,
            organisation_id,
        )
        return receipt

    def run_authorized_inference(self, request: InferenceRequest) -> InferenceReceipt:
        if not request.authorization_token:
            raise PermissionError("Inference requires a Kalyx authorization token")
        receipt = self.provider.run_inference(request)
        self._audit(
            request.agent_id or "ORCHESTRATOR",
            "ORBIO_INFERENCE_RESULT",
            request.idempotency_key,
            receipt.audit_safe_dict(),
            request.tenant_id,
            request.organisation_id,
        )
        return receipt


def redact_secrets(obj: Any) -> Any:
    """Recursively redact secret-like fields from structures before logging."""
    SECRET_KEYS = {"api_key", "secret", "one_time_secret", "authorization", "authorization_token", "password", "private_key"}
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k.lower() in SECRET_KEYS or "secret" in k.lower() or k.lower().endswith("_key") and k.lower() not in {"key_id", "idempotency_key"}:
                out[k] = "***REDACTED***"
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(obj, list):
        return [redact_secrets(x) for x in obj]
    return obj
