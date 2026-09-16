"""API security middlewares: request size limits, rate limiting, and correlation IDs."""

import os
import time
import uuid
from collections import defaultdict
from typing import Dict, List, Tuple
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

DEFAULT_MAX_REQUEST_BYTES = 1_048_576  # 1 MB


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """Enforce bounded request body sizes to prevent memory exhaustion and payload abuse."""

    def __init__(self, app, max_bytes: int = DEFAULT_MAX_REQUEST_BYTES):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        configured_max = int(os.getenv("KALYX_MAX_REQUEST_BYTES", str(self.max_bytes)))
        content_length = request.headers.get("Content-Length")

        if content_length:
            try:
                length = int(content_length)
                if length > configured_max:
                    return JSONResponse(
                        {
                            "error": "payload_too_large",
                            "message": f"Request payload exceeds maximum allowed size of {configured_max} bytes",
                        },
                        status_code=413,
                    )
            except ValueError:
                pass

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter per client IP / principal for sensitive mutations and reads."""

    def __init__(self, app):
        super().__init__(app)
        # Store timestamps of requests: key -> list of float timestamps
        self._history: Dict[str, List[float]] = defaultdict(list)

    def _is_enabled(self) -> bool:
        val = os.getenv("KALYX_RATE_LIMIT_ENABLED", "true").strip().lower()
        return val in {"1", "true", "yes", "on"}

    def _is_mutation(self, method: str, path: str) -> bool:
        if method in {"POST", "PUT", "DELETE", "PATCH"}:
            return True
        return False

    async def dispatch(self, request: Request, call_next):
        if not self._is_enabled():
            return await call_next(request)

        # Health endpoint is never rate limited
        if request.url.path == "/api/health":
            return await call_next(request)

        now = time.time()
        window_seconds = 60.0
        client_ip = request.client.host if request.client else "unknown"
        principal = request.headers.get("X-Principal-ID", client_ip)
        method = request.method
        path = request.url.path

        is_mut = self._is_mutation(method, path)
        limit = int(
            os.getenv("KALYX_RATE_LIMIT_MUTATIONS_PER_MIN", "60")
            if is_mut
            else os.getenv("KALYX_RATE_LIMIT_READS_PER_MIN", "300")
        )

        key = f"{'mut' if is_mut else 'read'}:{principal}"
        timestamps = self._history[key]

        # Evict timestamps older than 60s
        cutoff = now - window_seconds
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)

        if len(timestamps) >= limit:
            retry_after = int(window_seconds - (now - timestamps[0])) + 1
            return JSONResponse(
                {
                    "error": "rate_limit_exceeded",
                    "message": "Rate limit exceeded. Please retry later.",
                    "retry_after": retry_after,
                },
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        timestamps.append(now)
        return await call_next(request)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate or generate unique request correlation IDs (X-Request-ID)."""

    async def dispatch(self, request: Request, call_next):
        incoming_id = request.headers.get("X-Request-ID", "").strip()
        request_id = incoming_id if incoming_id and len(incoming_id) <= 64 else f"req-{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
