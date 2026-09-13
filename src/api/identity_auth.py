import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException

from src.identity.context import IdentityContext
from src.identity.repository import IdentityRepository
from src.persistence.database import Database


def identity_auth_required() -> bool:
    return os.getenv("KALYX_IDENTITY_AUTH", "false").strip().lower() in {"1", "true", "yes", "on"}


def _principal_id(x_principal_id: Optional[str]) -> str:
    if not x_principal_id:
        raise HTTPException(status_code=401, detail="Authenticated principal required")
    return x_principal_id


def require_identity_for_org(
    db: Database,
    org_id: str,
    x_principal_id: Optional[str],
    x_tenant_id: Optional[str],
) -> Optional[IdentityContext]:
    """Authorize access to an organisation using persisted principal membership.

    X-Tenant-ID is only a scope selector. It is never trusted as proof of membership;
    IdentityRepository verifies the principal's active membership before returning a context.
    """
    if not identity_auth_required():
        return None
    principal_id = _principal_id(x_principal_id)
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="Tenant scope required")
    row = db.conn.execute("SELECT tenant_id FROM organisations WHERE id = ?", (org_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organisation not found")
    resource_tenant = row["tenant_id"]
    if resource_tenant != x_tenant_id:
        # Do not reveal whether an organisation exists in another tenant.
        raise HTTPException(status_code=404, detail="Organisation not found")
    context = IdentityRepository(db).get_context(principal_id, resource_tenant)
    if context is None:
        raise HTTPException(status_code=403, detail="Principal is not authorized for this tenant")
    context.require_organisation(resource_tenant, org_id, resource_tenant)
    return context


def require_identity_for_tenant(
    db: Database,
    tenant_id: str,
    x_principal_id: Optional[str],
    x_tenant_id: Optional[str],
) -> Optional[IdentityContext]:
    if not identity_auth_required():
        return None
    principal_id = _principal_id(x_principal_id)
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="Tenant scope required")
    if not secrets.compare_digest(tenant_id, x_tenant_id):
        raise HTTPException(status_code=404, detail="Resource not found")
    context = IdentityRepository(db).get_context(principal_id, tenant_id)
    if context is None:
        raise HTTPException(status_code=403, detail="Principal is not authorized for this tenant")
    context.require_tenant(tenant_id)
    return context
