from dataclasses import dataclass


@dataclass(frozen=True)
class TenantContext:
    """Request/execution scope used to prevent accidental cross-tenant access."""

    tenant_id: str

    def require(self, resource_tenant_id: str) -> None:
        if resource_tenant_id != self.tenant_id:
            raise PermissionError(
                f"Tenant boundary violation: context={self.tenant_id!r}, "
                f"resource={resource_tenant_id!r}"
            )
