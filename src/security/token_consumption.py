"""Durable single-use authorization token consumption.

Process-memory sets are insufficient across restarts and workers.
Token nonce (bound into the signed claims) is the durable unique key.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


class TokenAlreadyConsumed(RuntimeError):
    """Raised when an authorization token nonce has already been used."""


class AuthorizationTokenJournal:
    """Backend-neutral durable consume-once journal for authorization tokens."""

    def __init__(self, conn: Any):
        self.conn = conn
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        # SQLite path: create if missing. Postgres path relies on migrations,
        # but CREATE IF NOT EXISTS is harmless there too.
        try:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS authorization_token_consumptions (
                    nonce TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    proposal_content_hash TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    policy_version_hash TEXT NOT NULL,
                    token_fingerprint TEXT NOT NULL,
                    consumed_at TEXT NOT NULL
                )"""
            )
            try:
                self.conn.commit()
            except Exception:
                pass
        except Exception:
            # Postgres may already have the table from migrations.
            pass

    def is_consumed(self, nonce: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM authorization_token_consumptions WHERE nonce = ?",
            (nonce,),
        ).fetchone()
        return row is not None

    def consume(
        self,
        *,
        nonce: str,
        org_id: str,
        proposal_id: str,
        proposal_content_hash: str,
        decision_id: str,
        policy_version_hash: str,
        token_fingerprint: str,
    ) -> None:
        if not nonce:
            raise ValueError("nonce is required")
        now = datetime.utcnow().isoformat()
        try:
            self.conn.execute(
                """INSERT INTO authorization_token_consumptions
                   (nonce, org_id, proposal_id, proposal_content_hash, decision_id,
                    policy_version_hash, token_fingerprint, consumed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    nonce,
                    org_id,
                    proposal_id,
                    proposal_content_hash,
                    decision_id,
                    policy_version_hash,
                    token_fingerprint,
                    now,
                ),
            )
            try:
                self.conn.commit()
            except Exception:
                pass
        except Exception as exc:
            # Unique violation => already consumed
            msg = str(exc).lower()
            if "unique" in msg or "duplicate" in msg or "constraint" in msg:
                raise TokenAlreadyConsumed(f"Authorization token nonce '{nonce}' already consumed") from exc
            # Re-check for race
            if self.is_consumed(nonce):
                raise TokenAlreadyConsumed(f"Authorization token nonce '{nonce}' already consumed") from exc
            raise
