import uuid
import json
import hashlib
import ipaddress
import socket
from urllib.parse import urlparse, urljoin
from datetime import datetime
from typing import Dict, Any, Optional, Set, Callable, Tuple, List
import httpx

from src.domain.entities import ActionProposal, PolicyDecision, ExecutionReceipt, Organisation
from src.domain.enums import PolicyResult, ActionType
from src.domain.exceptions import UnauthorizedActionError, PolicyViolationError, ExternalExecutionError
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
    1. Authorization token verification & strict replay protection
    2. Explicit target destination allowlists
    3. Comprehensive SSRF prevention (real DNS resolution of all IPv4/IPv6 addresses)
    4. HTTP redirect inspection (blocks redirects to private/loopback infrastructure)
    5. Allowed HTTP method enforcement (GET for DATA_FETCH, GET/POST for EXTERNAL_API_CALL)
    6. Strict execution timeout and payload size limits (request and response)
    7. Escrow reservation & atomic commit / rollback semantics:
       AUTHORIZE -> RESERVE -> EXECUTE -> VERIFY -> COMMIT (or ROLLBACK on failure)
    8. Verifiable ExecutionReceipt generation with response content hash
    """
    DEFAULT_ALLOWLIST = {
        "api://market_data/v1/summary",
        "sandbox://market_index_fund",
        "sandbox://verified_bonds",
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
        mock_handler: Optional[Callable[[str, Dict[str, Any]], Tuple[int, Dict[str, Any]]]] = None,
        allow_redirects: bool = False
    ):
        super().__init__(policy_engine, ledger)
        self.allowlist = allowlist if allowlist is not None else set(self.DEFAULT_ALLOWLIST)
        self.timeout_seconds = timeout_seconds
        self.max_payload_bytes = max_payload_bytes
        self.mock_handler = mock_handler
        self.allow_redirects = allow_redirects

    def _resolve_and_validate_host(self, host: str) -> List[str]:
        """
        Performs DNS resolution on host and validates ALL returned IPv4 and IPv6 addresses.
        Rejects private, loopback, link-local, multicast, and reserved addresses.
        """
        # Check raw IP directly first
        cleaned_host = host.strip("[]")
        try:
            ip = ipaddress.ip_address(cleaned_host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                raise UnauthorizedActionError(
                    f"SSRF blocked: Direct IP '{host}' is in a restricted/private network range"
                )
            return [str(ip)]
        except ValueError:
            pass

        # Host is a domain name -> resolve via DNS
        try:
            addr_info = socket.getaddrinfo(host, None)
        except socket.gaierror as e:
            raise UnauthorizedActionError(f"SSRF check failed: DNS resolution error for '{host}': {e}")

        resolved_ips: List[str] = []
        for family, socktype, proto, canonname, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                raise UnauthorizedActionError(
                    f"SSRF blocked: Host '{host}' resolved to restricted IP '{ip_str}'"
                )
            resolved_ips.append(ip_str)

        if not resolved_ips:
            raise UnauthorizedActionError(f"SSRF check failed: No valid IP addresses resolved for '{host}'")

        return resolved_ips

    def _validate_outbound_target(self, target: str) -> None:
        """
        Validates that target URL is in allowlist and passes SSRF/DNS resolution checks.
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
            if not host:
                raise UnauthorizedActionError(f"External execution rejected: Target '{target}' has no hostname")

            if host.lower() in {"localhost", "0.0.0.0", "127.0.0.1", "::1"}:
                raise UnauthorizedActionError(
                    f"External execution rejected: Target '{target}' points to forbidden loopback/localhost"
                )

            # Resolve and check all IP addresses
            self._resolve_and_validate_host(host)

    def _validate_request_payload(self, proposal: ActionProposal) -> str:
        """
        Enforces allowed HTTP methods and payload size limits.
        """
        # Payload size validation
        encoded_params = json.dumps(proposal.parameters).encode("utf-8")
        if len(encoded_params) > self.max_payload_bytes:
            raise UnauthorizedActionError(
                f"Request payload size ({len(encoded_params)} bytes) exceeds limit ({self.max_payload_bytes} bytes)"
            )

        # Allowed HTTP methods
        method = "GET"
        if proposal.action_type == ActionType.DATA_FETCH:
            method = "GET"
        elif proposal.action_type == ActionType.EXTERNAL_API_CALL:
            param_method = proposal.parameters.get("method", "POST").upper()
            if param_method not in {"GET", "POST"}:
                raise UnauthorizedActionError(
                    f"HTTP method '{param_method}' is not permitted for external execution (only GET, POST allowed)"
                )
            method = param_method

        return method

    def execute(
        self,
        proposal: ActionProposal,
        decision: PolicyDecision,
        org: Organisation
    ) -> ExecutionReceipt:
        """
        Atomic execution flow:
        AUTHORIZE -> RESERVE -> EXECUTE -> VERIFY RESULT -> COMMIT (or ROLLBACK on failure)
        """
        # Step 1: Enforce preconditions (approval, replay, cryptographic signature, TTL, pause state)
        token = self._verify_preconditions(proposal, decision, org)

        # Step 2: Validate outbound target against strict boundary & SSRF guards
        self._validate_outbound_target(proposal.target)

        # Step 3: Enforce allowed action types for external execution
        if proposal.action_type not in {ActionType.EXTERNAL_API_CALL, ActionType.DATA_FETCH, ActionType.SIMULATED_ALLOCATION}:
            raise UnauthorizedActionError(
                f"External execution rejected: ActionType '{proposal.action_type.value}' not allowed for external adapter"
            )

        # Step 4: Validate request payload and determine HTTP method
        http_method = self._validate_request_payload(proposal)

        # Step 5: RESERVE credits in Escrow before dispatch
        self._reserve_credits(proposal, token, org)

        # Step 6: Dispatch external call (protected by rollback on any failure)
        try:
            http_status, output_data = self._dispatch(proposal, http_method=http_method)

            # Step 7: Verify execution result
            if http_status >= 400:
                raise ExternalExecutionError(
                    f"External execution failed with HTTP status {http_status}: {output_data}"
                )

            # Validate response payload size
            resp_bytes = len(canonical_json(output_data).encode("utf-8"))
            if resp_bytes > self.max_payload_bytes:
                raise ExternalExecutionError(
                    f"Response payload size ({resp_bytes} bytes) exceeds limit ({self.max_payload_bytes} bytes)"
                )

            # Step 8: COMMIT settlement (Escrow -> External Sink)
            self._commit_reservation(proposal, token, org)

            # Mark token consumed only upon successful commitment
            self._consumed_tokens.add(token)

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

        except Exception as exc:
            # Safely release / roll back escrowed credits to TREASURY
            self._rollback_reservation(proposal, token, org)
            if isinstance(exc, (UnauthorizedActionError, ExternalExecutionError)):
                raise exc
            raise ExternalExecutionError(f"External execution dispatch failed: {str(exc)}") from exc

    def _dispatch(
        self,
        proposal: ActionProposal,
        http_method: str = "GET"
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Dispatches request to mock handler, sandbox, or live HTTP client.
        Enforces timeout, non-redirect or redirect validation, and payload capture.
        """
        if self.mock_handler:
            return self.mock_handler(proposal.target, proposal.parameters)

        if proposal.target.startswith("api://") or proposal.target.startswith("sandbox://"):
            return 200, {
                "source": "controlled_gateway",
                "route": proposal.target,
                "data": {"status": "SUCCESS", "parameters": proposal.parameters},
                "value_metric": round(proposal.requested_credits * proposal.expected_value_score * 1.8, 2)
            }

        # Live outbound HTTP dispatch
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
                if http_method == "GET":
                    resp = client.get(proposal.target, params=proposal.parameters)
                elif http_method == "POST":
                    resp = client.post(proposal.target, json=proposal.parameters)
                else:
                    raise UnauthorizedActionError(f"Unsupported HTTP method '{http_method}'")

                # Handle HTTP redirects securely
                if resp.is_redirect:
                    location = resp.headers.get("Location")
                    if location:
                        redirect_target = urljoin(proposal.target, location)
                        # Validate redirect destination through full SSRF and allowlist guards
                        self._validate_outbound_target(redirect_target)
                        if self.allow_redirects:
                            # Re-dispatch to validated redirect target
                            redirect_prop = proposal.model_copy(update={"target": redirect_target})
                            return self._dispatch(redirect_prop, http_method="GET")
                        else:
                            raise UnauthorizedActionError(
                                f"SSRF boundary blocked redirect to '{redirect_target}' (allow_redirects=False)"
                            )

                status = resp.status_code
                if len(resp.content) > self.max_payload_bytes:
                    raise ExternalExecutionError(
                        f"Response body length {len(resp.content)} exceeds payload limit of {self.max_payload_bytes} bytes"
                    )

                try:
                    data = resp.json()
                except Exception:
                    data = {"raw_text": resp.text[:2000]}

                return status, data

        except httpx.TimeoutException as te:
            raise ExternalExecutionError(f"External call timed out after {self.timeout_seconds}s: {te}") from te
        except httpx.RequestError as re:
            raise ExternalExecutionError(f"External connection failure: {re}") from re
