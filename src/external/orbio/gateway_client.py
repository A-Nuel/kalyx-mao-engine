"""Orbio inference gateway client (HTTP, not MCP).

Verified from official docs:
- POST {base}/chat/completions  (OpenAI-compatible)
- GET  {base}/key               balance + rate limits
- Authorization: Bearer $ORBIO_API_KEY
- X-Orbio-Balance response header = balance at request start
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


class OrbioGatewayError(Exception):
    def __init__(self, message: str, *, status_code: Optional[int] = None, payload: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class OrbioGatewayClient:
    def __init__(self, gateway_base: str, api_key: str, timeout: float = 60.0):
        if not api_key:
            raise OrbioGatewayError("API key required for gateway client")
        self.gateway_base = gateway_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get_key(self) -> Dict[str, Any]:
        url = f"{self.gateway_base}/key"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            raise OrbioGatewayError(f"GET /key failed: HTTP {e.code}", status_code=e.code, payload={"raw": body[:300]}) from e
        except urllib.error.URLError as e:
            raise OrbioGatewayError(f"GET /key transport error: {e}") from e

    def chat_completions(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """Returns (response_json, response_headers_lower)."""
        url = f"{self.gateway_base}/chat/completions"
        body: Dict[str, Any] = {"model": model, "messages": messages}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if extra:
            body.update(extra)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return json.loads(resp.read().decode("utf-8")), headers
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode("utf-8", errors="replace") if e.fp else ""
            raise OrbioGatewayError(
                f"chat/completions failed: HTTP {e.code}",
                status_code=e.code,
                payload={"raw": body_txt[:500]},
            ) from e
        except urllib.error.URLError as e:
            raise OrbioGatewayError(f"chat/completions transport error: {e}") from e
