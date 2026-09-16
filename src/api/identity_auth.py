import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from fastapi import HTTPException

from src.identity.context import IdentityContext
from src.identity.models import MembershipRole
from src.identity.repository import IdentityRepository
from src.persistence.database import Database


def is_production() -> bool:
    return (
        os.getenv("KALYX_ENV", "demo").strip().lower() in {"production", "prod"}
        or os.getenv("KALYX_IDENTITY_AUTH", "").strip().lower() == "production"
    )


def identity_auth_required() -> bool:
    if is_production():
        return True
    return os.getenv("KALYX_IDENTITY_AUTH", "false").strip().lower() in {"1", "true", "yes", "on", "production"}


def create_identity_token(principal_id: str, secret: str, ttl_seconds: int = 3600) -> str:
    """Generate a tamper-evident bearer token bound to a principal."""
    expiry = int(time.time()) + ttl_seconds
    msg = f"{principal_id}:{expiry}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{principal_id}:{expiry}:{sig}"


def verify_identity_token(token: str, secret: str) -> Optional[str]:
    """Verify bearer token signature and expiry, returning principal_id or None."""
    parts = token.split(":")
    if len(parts) != 3:
        return None
    principal_id, expiry_str, sig = parts
    try:
        expiry = int(expiry_str)
    except ValueError:
        return None
    if time.time() > expiry:
        return None
    msg = f"{principal_id}:{expiry}".encode("utf-8")
    expected_sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    if not secrets.compare_digest(sig, expected_sig):
        return None
    return principal_id


def _resolve_principal_id(
    x_principal_id: Optional[str] = None,
    authorization: Optional[str] = None,
    x_api_key: Optional[str] = None,
) -> str:
    op_key = os.getenv("KALYX_OPERATOR_KEY", "").strip()
    if op_key and x_api_key and secrets.compare_digest(x_api_key, op_key):
        return x_principal_id or "principal-demo"

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        policy_sec = os.getenv("KALYX_POLICY_SECRET", "phase7-demo-policy-secret").strip()
        verified = verify_identity_token(token, policy_sec)
        if verified:
            return verified
        raise HTTPException(status_code=401, detail="Invalid or expired Bearer token")

    if is_production():
        raise HTTPException(
            status_code=401,
            detail="Production authentication requires a valid Bearer token or API key; unverified headers are rejected.",
        )

    if not x_principal_id:
        raise HTTPException(status_code=401, detail="Authenticated principal required")
    return x_principal_id


def require_write_permission(context: Optional[IdentityContext]) -> None:
    if context is not None and not context.can_write():
        raise HTTPException(status_code=403, detail="Viewer role is read-only; write permission required")


def require_admin_permission(context: Optional[IdentityContext]) -> None:
    if context is not None and not context.can_administer():
        raise HTTPException(status_code=403, detail="Administrative permission required")


def require_identity_for_org(
    db: Database,
    org_id: str,
    x_principal_id: Optional[str],
    x_tenant_id: Optional[str],
    authorization: Optional[str] = None,
    x_api_key: Optional[str] = None,
) -> Optional[IdentityContext]:
    """Authorize access to an organisation using persisted principal membership.

    X-Tenant-ID is only a scope selector. It is never trusted as proof of membership;
    IdentityRepository verifies the principal's active membership before returning a context.
    """
    if not identity_auth_required():
        return None

    principal_id = _resolve_principal_id(x_principal_id, authorization, x_api_key)

    op_key = os.getenv("KALYX_OPERATOR_KEY", "").strip()
    is_operator = bool(op_key and x_api_key and secrets.compare_digest(x_api_key, op_key))

    row = db.conn.execute("SELECT tenant_id FROM organisations WHERE id = ?", (org_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organisation not found")
    resource_tenant = row["tenant_id"]

    if not x_tenant_id:
        if is_operator:
            x_tenant_id = resource_tenant
        else:
            raise HTTPException(status_code=400, detail="Tenant scope required")

    if resource_tenant != x_tenant_id:
        # Do not reveal whether an organisation exists in another tenant.
        raise HTTPException(status_code=404, detail="Organisation not found")
    context = IdentityRepository(db).get_context(principal_id, resource_tenant)
    if context is None:
        if is_operator:
            context = IdentityContext(principal_id=principal_id, tenant_id=resource_tenant, role=MembershipRole.OWNER)
        else:
            raise HTTPException(status_code=403, detail="Principal is not authorized for this tenant")
    context.require_organisation(resource_tenant, org_id, resource_tenant)
    return context


def require_identity_for_tenant(
    db: Database,
    tenant_id: str,
    x_principal_id: Optional[str],
    x_tenant_id: Optional[str],
    authorization: Optional[str] = None,
    x_api_key: Optional[str] = None,
) -> Optional[IdentityContext]:
    if not identity_auth_required():
        return None
    principal_id = _resolve_principal_id(x_principal_id, authorization, x_api_key)
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="Tenant scope required")
    if not secrets.compare_digest(tenant_id, x_tenant_id):
        raise HTTPException(status_code=404, detail="Resource not found")
    context = IdentityRepository(db).get_context(principal_id, tenant_id)
    if context is None:
        raise HTTPException(status_code=403, detail="Principal is not authorized for this tenant")
    context.require_tenant(tenant_id)
    return context
