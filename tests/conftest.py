"""Shared test isolation fixtures.

The API application is imported once for the integration suite, so middleware
state (including the in-memory rate-limit window) survives across TestClient
instances. Most API tests are not testing rate limiting and should not become
order-dependent as the suite grows. The dedicated hardening test explicitly
enables the limiter when it needs to verify 429 behavior.
"""

import pytest


@pytest.fixture(autouse=True)
def disable_rate_limit_by_default(monkeypatch):
    monkeypatch.setenv("KALYX_RATE_LIMIT_ENABLED", "false")
