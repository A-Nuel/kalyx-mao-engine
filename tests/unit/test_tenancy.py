import pytest
from src.tenancy import Tenant, TenantContext


def test_tenant_has_stable_workspace_identity():
    tenant = Tenant(id="tenant-a", name="Research Lab")
    assert tenant.id == "tenant-a"
    assert tenant.status == "active"


def test_context_accepts_resources_from_same_tenant():
    TenantContext(tenant_id="tenant-a").require("tenant-a")


def test_context_rejects_cross_tenant_resource_access():
    with pytest.raises(PermissionError, match="Tenant boundary violation"):
        TenantContext(tenant_id="tenant-a").require("tenant-b")
