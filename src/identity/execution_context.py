"""Canonical trusted identity/execution scope for Phase 21.

This object is created only after the authentication boundary has resolved a
trusted IdentityContext. Downstream code may use it as the single scope object
for request/execution ownership without trusting client-supplied identifiers.
"""

from dataclasses import dataclass
from typing import Optional

from src.identity.context import IdentityContext


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable scope carried from authentication into governed execution."""

    principal_id: str
    tenant_id: str
    organisation_id: Optional[str]
    role: str
    request_id: Optional[str] = None

    @classmethod
    def from_identity(
        cls,
        identity: IdentityContext,
        *,
        organisation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> "ExecutionContext":
        if organisation_id is not None and not organisation_id.strip():
            raise ValueError("organisation_id must be non-empty when provided")
        return cls(
            principal_id=identity.principal_id,
            tenant_id=identity.tenant_id,
            organisation_id=organisation_id,
            role=identity.role.value,
            request_id=request_id,
        )

    def require_tenant(self, resource_tenant_id: str) -> None:
        if resource_tenant_id != self.tenant_id:
            raise PermissionError("Tenant boundary violation")

    def require_organisation(self, resource_tenant_id: str, resource_organisation_id: str) -> None:
        self.require_tenant(resource_tenant_id)
        if self.organisation_id is None:
            raise PermissionError("Organisation scope is required")
        if resource_organisation_id != self.organisation_id:
            raise PermissionError("Organisation boundary violation")

    def can_write(self) -> bool:
        return self.role in {"owner", "admin", "operator"}

    def can_administer(self) -> bool:
        return self.role in {"owner", "admin"}
