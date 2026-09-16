"""Phase 11 Production Hardening & Operational Readiness Verification Suite.

Tests:
1. Request body limit: 413 Payload Too Large when payload > limit.
2. Rate limiting: 429 Too Many Requests with Retry-After when threshold exceeded.
3. Correlation IDs: X-Request-ID propagation and auto-generation.
4. Structured error responses: Sanitized JSON, no traceback or raw SQL leaks.
5. Fail-closed production identity:
   - Plain X-Principal-ID rejected with 401 in production mode.
   - Valid HMAC Bearer token accepted.
   - Tampered / expired token rejected with 401.
6. RBAC permissions:
   - VIEWER role principal permitted to read, but rejected with 403 on mutations.
   - OPERATOR / OWNER role permitted to perform mutations.
7. Audit event scoping:
   - SQL-level scoping prevents cross-tenant or cross-org event leakage.
8. Health check contract:
   - 200 OK with database: connected when healthy.
   - 503 Service Unavailable with database: disconnected when DB unreachable.
"""

import json
import os
import time
import uuid
import pytest
from fastapi.testclient import TestClient

from src.api.identity_auth import create_identity_token
from src.api.server import app
from src.domain.entities import Organisation
from src.domain.enums import OrgState
from src.domain.events import AuditEvent, canonical_json
from src.identity.models import Membership, MembershipRole, Principal
from src.identity.repository import IdentityRepository
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore, SqliteRepository


@pytest.fixture
def test_db(tmp_path):
    db_path = str(tmp_path / "kalyx_hardening.db")
    db = Database(db_path)
    try:
        # Seed two tenants
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("tenant-alpha", "Alpha Corp", "active"),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("tenant-beta", "Beta LLC", "active"),
        )

        identity = IdentityRepository(db)
        # Principals
        identity.save_principal(Principal(id="user-owner-a", name="Alice Owner"))
        identity.save_principal(Principal(id="user-viewer-a", name="Victor Viewer"))
        identity.save_principal(Principal(id="user-operator-a", name="Oscar Operator"))
        identity.save_principal(Principal(id="user-beta", name="Bob Beta"))

        # Memberships
        identity.save_membership(Membership(principal_id="user-owner-a", tenant_id="tenant-alpha", role=MembershipRole.OWNER))
        identity.save_membership(Membership(principal_id="user-viewer-a", tenant_id="tenant-alpha", role=MembershipRole.VIEWER))
        identity.save_membership(Membership(principal_id="user-operator-a", tenant_id="tenant-alpha", role=MembershipRole.OPERATOR))
        identity.save_membership(Membership(principal_id="user-beta", tenant_id="tenant-beta", role=MembershipRole.OWNER))

        repo = SqliteRepository(db)
        org_a = Organisation(
            id="org-alpha",
            tenant_id="tenant-alpha",
            mission="Alpha Core Ops",
            treasury_balance=500,
            state=OrgState.EXECUTING,
        )
        org_b = Organisation(
            id="org-beta",
            tenant_id="tenant-beta",
            mission="Beta Shadow Ops",
            treasury_balance=200,
            state=OrgState.EXECUTING,
        )
        repo.save_organisation(org_a)
        repo.save_organisation(org_b)
        db.conn.commit()
    finally:
        db.close()
    return db_path


# ==============================================================================
# 1. REQUEST BODY LIMIT
# ==============================================================================

def test_request_body_size_limit_exceeded_returns_413(test_db, monkeypatch):
    monkeypatch.setenv("KALYX_DB", test_db)
    monkeypatch.setenv("KALYX_MAX_REQUEST_BYTES", "1024")  # 1 KB limit
    client = TestClient(app)

    # Oversized payload (> 1 KB)
    large_payload = {"mission": "X" * 2048, "budget": 10}
    response = client.post(
        "/api/missions",
        json=large_payload,
        headers={"Content-Length": str(len(json.dumps(large_payload)))},
    )
    assert response.status_code == 413
    data = response.json()
    assert data["error"] == "payload_too_large"
    assert "maximum allowed size" in data["message"]


# ==============================================================================
# 2. RATE LIMITING
# ==============================================================================

def test_rate_limiting_enforcement(test_db, monkeypatch):
    monkeypatch.setenv("KALYX_DB", test_db)
    monkeypatch.setenv("KALYX_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("KALYX_RATE_LIMIT_MUTATIONS_PER_MIN", "3")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "false")
    monkeypatch.setenv("KALYX_REQUIRE_OPERATOR_AUTH", "false")
    client = TestClient(app)

    # Use unique client identifier
    test_headers = {"X-Principal-ID": f"ratelimit-test-{uuid.uuid4().hex[:6]}"}

    # First 3 mutations should proceed past rate limiting
    for i in range(3):
        res = client.post(
            "/api/organisations/org-alpha/pause",
            headers=test_headers,
        )
        # May succeed (200) or already paused, but must NOT be 429
        assert res.status_code in {200, 404, 409}, f"Unexpected status on attempt {i}: {res.status_code}"

    # 4th mutation must be blocked by rate limiter
    res_blocked = client.post(
        "/api/organisations/org-alpha/pause",
        headers=test_headers,
    )
    assert res_blocked.status_code == 429
    data = res_blocked.json()
    assert data["error"] == "rate_limit_exceeded"
    assert "Retry-After" in res_blocked.headers

    # Health check endpoint must NEVER be rate limited
    health_res = client.get("/api/health")
    assert health_res.status_code == 200


# ==============================================================================
# 3. CORRELATION IDS & STRUCTURED ERROR SANITIZATION
# ==============================================================================

def test_correlation_id_propagation_and_generation(test_db, monkeypatch):
    monkeypatch.setenv("KALYX_DB", test_db)
    client = TestClient(app)

    # 1. Propagate existing X-Request-ID
    custom_id = "trace-req-custom-9999"
    res = client.get("/api/health", headers={"X-Request-ID": custom_id})
    assert res.headers.get("X-Request-ID") == custom_id

    # 2. Auto-generate X-Request-ID if absent
    res2 = client.get("/api/health")
    assert "X-Request-ID" in res2.headers
    assert len(res2.headers["X-Request-ID"]) > 0


def test_structured_error_sanitization_no_traceback_or_sql_leak(test_db, monkeypatch):
    monkeypatch.setenv("KALYX_DB", test_db)
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "false")
    client = TestClient(app)

    # 404 Not Found
    res = client.get("/api/organisations/non-existent-org-id")
    assert res.status_code == 404
    data = res.json()
    assert "error" in data
    assert "detail" in data
    # Must not contain Python stack trace keywords or SQL syntax
    content_str = res.text
    assert "Traceback (most recent call last)" not in content_str
    assert "SELECT " not in content_str

    # 422 Validation Error
    res_val = client.post("/api/missions", json={"invalid_field": 123})
    assert res_val.status_code == 422
    val_data = res_val.json()
    assert val_data["error"] == "validation_error"
    assert "Traceback" not in res_val.text


# ==============================================================================
# 4. FAIL-CLOSED PRODUCTION IDENTITY
# ==============================================================================

def test_production_identity_fail_closed(test_db, monkeypatch):
    prod_secret = "kalyx-super-secure-production-signing-secret"
    monkeypatch.setenv("KALYX_DB", test_db)
    monkeypatch.setenv("KALYX_POLICY_SECRET", prod_secret)
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "production")
    client = TestClient(app)

    # 1. Unverified X-Principal-ID alone is rejected in production
    res_unverified = client.get(
        "/api/organisations/org-alpha",
        headers={"X-Principal-ID": "user-owner-a", "X-Tenant-ID": "tenant-alpha"},
    )
    assert res_unverified.status_code == 401
    assert "Production authentication requires a valid Bearer token" in res_unverified.json().get("detail", "")

    # 2. Valid Bearer token signed with policy secret is accepted
    valid_token = create_identity_token("user-owner-a", prod_secret, ttl_seconds=300)
    res_valid = client.get(
        "/api/organisations/org-alpha",
        headers={"Authorization": f"Bearer {valid_token}", "X-Tenant-ID": "tenant-alpha"},
    )
    assert res_valid.status_code == 200
    assert res_valid.json()["organisation"]["id"] == "org-alpha"

    # 3. Tampered Bearer token signature is rejected
    tampered_token = valid_token[:-4] + "dead"
    res_tampered = client.get(
        "/api/organisations/org-alpha",
        headers={"Authorization": f"Bearer {tampered_token}", "X-Tenant-ID": "tenant-alpha"},
    )
    assert res_tampered.status_code == 401
    assert "Invalid or expired Bearer token" in res_tampered.json().get("detail", "")

    # 4. Expired Bearer token is rejected
    expired_token = create_identity_token("user-owner-a", prod_secret, ttl_seconds=-60)
    res_expired = client.get(
        "/api/organisations/org-alpha",
        headers={"Authorization": f"Bearer {expired_token}", "X-Tenant-ID": "tenant-alpha"},
    )
    assert res_expired.status_code == 401


# ==============================================================================
# 5. RBAC PERMISSIONS (VIEWER CANNOT MUTATE)
# ==============================================================================

def test_rbac_role_enforcement_viewer_cannot_mutate(test_db, monkeypatch):
    monkeypatch.setenv("KALYX_DB", test_db)
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")
    monkeypatch.setenv("KALYX_REQUIRE_OPERATOR_AUTH", "false")
    client = TestClient(app)

    viewer_headers = {
        "X-Principal-ID": "user-viewer-a",
        "X-Tenant-ID": "tenant-alpha",
    }
    operator_headers = {
        "X-Principal-ID": "user-operator-a",
        "X-Tenant-ID": "tenant-alpha",
    }

    # 1. Viewer CAN read organisation details
    read_res = client.get("/api/organisations/org-alpha", headers=viewer_headers)
    assert read_res.status_code == 200

    # 2. Viewer CANNOT pause organisation
    pause_res = client.post("/api/organisations/org-alpha/pause", headers=viewer_headers)
    assert pause_res.status_code == 403
    assert "write permission required" in pause_res.text.lower() or "permission" in pause_res.text.lower()

    # 3. Viewer CANNOT resume organisation
    resume_res = client.post("/api/organisations/org-alpha/resume", headers=viewer_headers)
    assert resume_res.status_code == 403

    # 4. Viewer CANNOT create and run missions
    mission_res = client.post(
        "/api/missions",
        json={"mission": "Unauthorized Viewer Mission", "budget": 10},
        headers=viewer_headers,
    )
    assert mission_res.status_code == 403

    # 5. Operator CAN pause and resume organisation
    op_pause = client.post("/api/organisations/org-alpha/pause", headers=operator_headers)
    assert op_pause.status_code == 200
    assert op_pause.json()["state"] == "PAUSED"

    op_resume = client.post("/api/organisations/org-alpha/resume", headers=operator_headers)
    assert op_resume.status_code == 200
    assert op_resume.json()["state"] == "EXECUTING"


# ==============================================================================
# 6. AUDIT EVENT SQL SCOPING
# ==============================================================================

def test_audit_event_scoping_no_cross_tenant_leakage(test_db, monkeypatch):
    monkeypatch.setenv("KALYX_DB", test_db)
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")

    db = Database(test_db)
    event_store = SqliteEventStore(db, verify_on_startup=False)

    # Append events explicitly with tenant and organisation scopes
    event_store.append_event(
        actor_id="agent-alpha-1",
        event_type="ALPHA_EXCLUSIVE_ACTION",
        entity_id="org-alpha",
        payload={"secret": "alpha-secret-data"},
        tenant_id="tenant-alpha",
        organisation_id="org-alpha",
    )
    event_store.append_event(
        actor_id="agent-beta-1",
        event_type="BETA_EXCLUSIVE_ACTION",
        entity_id="org-beta",
        payload={"secret": "beta-secret-data"},
        tenant_id="tenant-beta",
        organisation_id="org-beta",
    )
    db.close()

    client = TestClient(app)

    # Query as Alpha user
    alpha_res = client.get(
        "/api/organisations/org-alpha/events",
        headers={"X-Principal-ID": "user-owner-a", "X-Tenant-ID": "tenant-alpha"},
    )
    assert alpha_res.status_code == 200
    alpha_events = alpha_res.json()

    event_types = [e["event_type"] for e in alpha_events]
    assert "ALPHA_EXCLUSIVE_ACTION" in event_types
    # Invariant: Beta event must NOT leak into Alpha query
    assert "BETA_EXCLUSIVE_ACTION" not in event_types
    for e in alpha_events:
        assert "beta-secret-data" not in json.dumps(e)

    # Invariant: Alpha user attempting to read Beta's events receives 404
    cross_tenant_res = client.get(
        "/api/organisations/org-beta/events",
        headers={"X-Principal-ID": "user-owner-a", "X-Tenant-ID": "tenant-alpha"},
    )
    assert cross_tenant_res.status_code == 404


# ==============================================================================
# 7. HEALTH CHECK CONTRACT
# ==============================================================================

def test_health_check_endpoint_contract(test_db, monkeypatch):
    monkeypatch.setenv("KALYX_DB", test_db)
    client = TestClient(app)

    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert "version" in data
    assert "environment" in data

    # Degraded health when database is completely unreachable.
    # Use a path whose parent cannot be created on Linux CI runners
    # (/dev/null is a file, so makedirs of a child path fails).
    # The assertion remains 503 Service Unavailable — contract unchanged.
    monkeypatch.setenv("KALYX_DB", "/dev/null/kalyx_unreachable.db")
    res_degraded = client.get("/api/health")
    assert res_degraded.status_code == 503
    degraded_data = res_degraded.json()
    assert "Database unavailable" in degraded_data.get("detail", "")
