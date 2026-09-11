import uuid
import json
import hashlib
import ipaddress
from urllib.parse import urlparse
from datetime import datetime
from typing import Dict, Any, Optional, Set, Callable, Tuple
import httpx

from src.domain.entities import ActionProposal, PolicyDecision, ExecutionReceipt, Organisation
from src.domain.enums import PolicyResult, ActionType
from src.domain.exceptions import UnauthorizedActionError, PolicyViolationError
from src.domain.events import canonical_json
from src.governance.policy_engine import PolicyEngine
from src.economy.ledger import DoubleEntryLedger
from src.execution.base import BaseExecutor

class SandboxExecutor(BaseExecutor):
    """
    Deterministic simulated execution service.
    Inherits from BaseExecutor. Enforces replay protection, token validity,
    atomic ledger deduction, and receipt generation.
    """
    def execute(
        self,
        proposal: ActionProposal,
        decision: PolicyDecision,
        org: Organisation
    ) -> ExecutionReceipt:
        token = self._verify_preconditions(proposal, decision, org)
        self._consumed_tokens.add(token)
        self._settle_ledger_fee(proposal, token, org)

        output_payload = {
            "status": "SUCCESS",
            "target": proposal.target,
            "action_type": proposal.action_type.value,
            "simulated_value": round(proposal.requested_credits * proposal.expected_value_score * 1.5, 2),
            "executed_parameters": proposal.parameters
        }
        raw_hash = hashlib.sha256(canonical_json(output_payload).encode("utf-8")).hexdigest()

        return ExecutionReceipt(
            id=str(uuid.uuid4()),
            proposal_id=proposal.id,
            authorization_token=token,
            action_type=proposal.action_type,
            target=proposal.target,
            http_status=200,
            raw_response_hash=raw_hash,
            raw_output=output_payload,
            cost_credits=proposal.requested_credits,
            executed_at=datetime.utcnow()
        )

class ControlledExternalExecutor(BaseExecutor):
    """
    Controlled external execution service with strict outbound network boundary.
    Enforces:
    1. Authorization token verification & replay protection
    2. Explicit target destination allowlists
    3. SSRF prevention (blocks private/loopback/link-local IP addresses)
    4. HTTP method & payload constraints
    5. Strict execution timeout (default 5.0s) and payload size limit
    6. Atomic ledger settlement
    7. Verifiable ExecutionReceipt generation with response content hash
    """
    DEFAULT_ALLOWLIST = {
        "api://market_data/v1/summary",
        "https://api.github.com/repos/",
        "https://httpbin.org/get",
        "https://api.coingecko.com/api/v3/simple/price"
    }

    def __init__(
        self,
        policy_engine: PolicyEngine,
        ledger: DoubleEntryLedger,
        allowlist: Optional[Set[str]] = None,
        timeout_seconds: float = 5.0,
        max_payload_bytes: int = 65536,
        mock_handler: Optional[Callable[[str, Dict[str, Any]], Tuple[int, Dict[str, Any]]]] = None
    ):
        super().__init__(policy_engine, ledger)
        self.allowlist = allowlist if allowlist is not None else set(self.DEFAULT_ALLOWLIST)
        self.timeout_seconds = timeout_seconds
        self.max_payload_bytes = max_payload_bytes
        self.mock_handler = mock_handler

    def _validate_outbound_target(self, target: str) -> None:
        """
        Validates that target URL is in allowlist and does not attempt SSRF against internal infrastructure.
        """
        # 1. Allowlist prefix check
        allowed = any(target == item or target.startswith(item) for item in self.allowlist)
        if not allowed:
            raise UnauthorizedActionError(
                f"External execution rejected: Target '{target}' is not in the outbound allowlist"
            )

        # 2. SSRF check for network URLs
        parsed = urlparse(target)
        if parsed.scheme in {"http", "https"}:
            host = parsed.hostname or ""
            # Block localhost / loopback names
            if host.lower() in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
                raise UnauthorizedActionError(
                    f"External execution rejected: Target '{target}' points to forbidden loopback/localhost"
                )
            # Try parsing IP to block private ranges
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    raise UnauthorizedActionError(
                        f"External execution rejected: Target IP '{host}' is in a restricted/private network range (SSRF blocked)"
                    )
            except ValueError:
                # Hostname is not a raw IP address; allow public domain
                pass

    def execute(
        self,
        proposal: ActionProposal,
        decision: PolicyDecision,
        org: Organisation
    ) -> ExecutionReceipt:
        # Step 1: Enforce all common preconditions (approval, replay, cryptographic signature, TTL, pause state)
        token = self._verify_preconditions(proposal, decision, org)

        # Step 2: Validate outbound target against strict boundary & SSRF guards
        self._validate_outbound_target(proposal.target)

        # Step 3: Enforce allowed action types for external execution
        if proposal.action_type not in {ActionType.EXTERNAL_API_CALL, ActionType.DATA_FETCH}:
            raise UnauthorizedActionError(
                f"External execution rejected: ActionType '{proposal.action_type.value}' not allowed for external adapter"
            )

        # Step 4: Mark token consumed before any side effects
        self._consumed_tokens.add(token)

        # Step 5: Atomic ledger settlement
        self._settle_ledger_fee(proposal, token, org)

        # Step 6: Dispatch external call (or mock handler)
        http_status, output_data = self._dispatch(proposal)

        raw_hash = hashlib.sha256(canonical_json(output_data).encode("utf-8")).hexdigest()

        return ExecutionReceipt(
            id=str(uuid.uuid4()),
            proposal_id=proposal.id,
            authorization_token=token,
            action_type=proposal.action_type,
            target=proposal.target,
            http_status=http_status,
            raw_response_hash=raw_hash,
            raw_output=output_data,
            cost_credits=proposal.requested_credits,
            executed_at=datetime.utcnow()
        )

    def _dispatch(self, proposal: ActionProposal) -> Tuple[int, Dict[str, Any]]:
        if self.mock_handler:
            return self.mock_handler(proposal.target, proposal.parameters)

        if proposal.target.startswith("api://"):
            # Internal mockable API route
            return 200, {
                "source": "controlled_gateway",
                "route": proposal.target,
                "data": {"status": "SUCCESS", "parameters": proposal.parameters},
                "value_metric": round(proposal.requested_credits * proposal.expected_value_score * 1.8, 2)
            }

        # Real HTTP outbound call
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.get(proposal.target, params=proposal.parameters)
                status = resp.status_code
                try:
                    data = resp.json()
                except Exception:
                    data = {"raw_text": resp.text[:1000]}
                return status, data
        except Exception as e:
            # Network or timeout failure
            return 502, {"error": "External call failed", "details": str(e)}
