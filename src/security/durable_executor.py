"""Hardened external executor facade.

For non-idempotent external work, Kalyx sends a stable Idempotency-Key. The
provider must honor it; arbitrary HTTP servers cannot provide exactly-once
semantics without provider cooperation.
"""

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Tuple

import httpx

from src.domain.entities import ActionProposal, ExecutionReceipt
from src.execution.executor import ControlledExternalExecutor
from src.security.idempotency import SQLiteIdempotencyJournal
from src.domain.events import canonical_json
from src.domain.exceptions import ExternalExecutionError


class DurableControlledExternalExecutor(ControlledExternalExecutor):
    """ControlledExternalExecutor with durable operation journaling."""

    def __init__(self, *args, db_conn=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.journal = SQLiteIdempotencyJournal(db_conn) if db_conn is not None else None

    def _operation_key(self, proposal: ActionProposal, org) -> str:
        return f"{org.id}:{proposal.id}"

    def _fingerprint(self, proposal: ActionProposal, org) -> str:
        return hashlib.sha256(canonical_json({
            "org_id": org.id,
            "proposal_id": proposal.id,
            "action_type": proposal.action_type.value,
            "target": proposal.target,
            "parameters": proposal.parameters,
            "requested_credits": proposal.requested_credits,
        }).encode("utf-8")).hexdigest()

    def execute(self, proposal, decision, org) -> ExecutionReceipt:
        if self.journal is None:
            return super().execute(proposal, decision, org)
        operation_key = self._operation_key(proposal, org)
        fingerprint = self._fingerprint(proposal, org)
        existing = self.journal.get(operation_key)
        if existing is not None and existing[2] == "started":
            raise ExternalExecutionError("Operation is unresolved; reconcile the external provider before retrying")
        prior_receipt_id = self.journal.begin(operation_key, fingerprint)
        if prior_receipt_id:
            row = self.ledger.db.conn.execute("SELECT * FROM execution_receipts WHERE id = ?", (prior_receipt_id,)).fetchone()
            if row is None:
                raise ExternalExecutionError("Idempotency journal references a missing execution receipt")
            from src.domain.enums import ActionType
            return ExecutionReceipt(
                id=row["id"], proposal_id=row["proposal_id"], authorization_token=row["authorization_token"],
                action_type=ActionType(row["action_type"]), target=row["target"], http_status=row["http_status"],
                raw_response_hash=row["raw_response_hash"], raw_output=json.loads(row["raw_output"]),
                cost_credits=row["cost_credits"], executed_at=datetime.fromisoformat(row["executed_at"]),
            )
        try:
            receipt = super().execute(proposal, decision, org)
            self.journal.succeed(operation_key, fingerprint, receipt.id)
            return receipt
        except Exception:
            self.journal.fail(operation_key, fingerprint)
            raise

    def _dispatch(self, proposal: ActionProposal, http_method: str = "GET") -> Tuple[int, Dict[str, Any]]:
        if self.mock_handler or proposal.target.startswith("api://") or proposal.target.startswith("sandbox://"):
            return super()._dispatch(proposal, http_method=http_method)
        try:
            headers = {"Idempotency-Key": proposal.id} if http_method == "POST" else {}
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False, headers=headers) as client:
                if http_method == "GET":
                    resp = client.get(proposal.target, params=proposal.parameters)
                elif http_method == "POST":
                    resp = client.post(proposal.target, json=proposal.parameters)
                else:
                    raise ExternalExecutionError(f"Unsupported HTTP method '{http_method}'")
                if resp.is_redirect:
                    raise ExternalExecutionError("Redirects are not permitted by the durable executor")
                if len(resp.content) > self.max_payload_bytes:
                    raise ExternalExecutionError("External response exceeds configured payload limit")
                try:
                    data = resp.json()
                except Exception:
                    data = {"raw_text": resp.text[:2000]}
                return resp.status_code, data
        except httpx.TimeoutException as exc:
            raise ExternalExecutionError(f"External call timed out after {self.timeout_seconds}s") from exc
        except httpx.RequestError as exc:
            raise ExternalExecutionError(f"External connection failure: {exc}") from exc
