"""Orbio Remote MCP client (JSON-RPC over HTTP).

Verified tools (official docs only; no invented tools):
- orbio_get_balance
- orbio_create_key
- orbio_get_key_status
- orbio_revoke_key

Endpoint: https://www.orbio.so/api/mcp (HTTP transport)
Unauthenticated calls receive 401.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


class OrbioMCPError(Exception):
    def __init__(self, message: str, *, status_code: Optional[int] = None, payload: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class OrbioMCPClient:
    """Minimal JSON-RPC 2.0 client for Orbio MCP."""

    KNOWN_TOOLS = (
        "orbio_get_balance",
        "orbio_create_key",
        "orbio_get_key_status",
        "orbio_revoke_key",
    )

    def __init__(self, mcp_url: str, api_key: Optional[str] = None, timeout: float = 30.0):
        self.mcp_url = mcp_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._next_id = 1

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.mcp_url, data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    raise OrbioMCPError("Empty MCP response", status_code=resp.status)
                # SSE may wrap; try JSON first
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    # Attempt to extract last data: line from SSE
                    for line in reversed(raw.splitlines()):
                        if line.startswith("data:"):
                            return json.loads(line[5:].strip())
                    raise OrbioMCPError("Malformed MCP response", status_code=resp.status, payload={"raw": raw[:500]})
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode("utf-8", errors="replace") if e.fp else ""
            try:
                payload = json.loads(body_txt) if body_txt else {}
            except json.JSONDecodeError:
                payload = {"raw": body_txt[:500]}
            raise OrbioMCPError(
                f"MCP HTTP {e.code}: {payload.get('error') or payload.get('error_description') or body_txt[:200]}",
                status_code=e.code,
                payload=payload,
            ) from e
        except urllib.error.URLError as e:
            raise OrbioMCPError(f"MCP transport error: {e}") from e

    def initialize(self) -> Dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        return self._post({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "kalyx-mao-engine", "version": "phase14a"},
            },
        })

    def tools_list(self) -> List[Dict[str, Any]]:
        req_id = self._next_id
        self._next_id += 1
        resp = self._post({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/list",
            "params": {},
        })
        if "error" in resp:
            raise OrbioMCPError(f"tools/list error: {resp['error']}", payload=resp)
        result = resp.get("result") or {}
        return result.get("tools") or []

    def tools_call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        if name not in self.KNOWN_TOOLS:
            raise OrbioMCPError(
                f"Refusing unknown MCP tool '{name}'. Verified tools: {', '.join(self.KNOWN_TOOLS)}"
            )
        req_id = self._next_id
        self._next_id += 1
        resp = self._post({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        if "error" in resp:
            raise OrbioMCPError(f"tools/call {name} error: {resp['error']}", payload=resp)
        return resp.get("result")

    def get_balance(self) -> Any:
        return self.tools_call("orbio_get_balance")

    def create_key(self) -> Any:
        return self.tools_call("orbio_create_key")

    def get_key_status(self, key_id: Optional[str] = None) -> Any:
        args = {"key_id": key_id} if key_id else {}
        return self.tools_call("orbio_get_key_status", args)

    def revoke_key(self, key_id: str) -> Any:
        return self.tools_call("orbio_revoke_key", {"key_id": key_id})
