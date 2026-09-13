from dataclasses import dataclass
from src.identity.models import MembershipRole


@dataclass(frozen=True)
class IdentityContext:
    """Trusted authorization context created by the authentication boundary."""

    principal_id: str
    tenant_id: str
    role: MembershipRole

    def can_write(self) -> bool:
        return self.role in {MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.OPERATOR}

    def can_administer(self) -> bool:
        return self.role in {MembershipRole.OWNER, MembershipRole.ADMIN}

    def require_tenant(self, resource_tenant_id: str) -> None:
        if resource_tenant_id != self.tenant_id:
            raise PermissionError("Tenant boundary violation")

    def require_organisation(self, resource_tenant_id: str, resource_org_id: str, organisation_tenant_id: str) -> None:
        self.require_tenant(resource_tenant_id)
        if resource_org_id == "":
            raise PermissionError("Organisation scope is required")
        if organisation_tenant_id != self.tenant_id:
            raise PermissionError("Organisation belongs to another tenant")
