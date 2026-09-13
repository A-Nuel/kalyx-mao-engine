from datetime import datetime
from typing import Optional
from src.identity.models import Principal, Membership, MembershipRole
from src.identity.context import IdentityContext


class IdentityRepository:
    """Persistence boundary for principals and tenant memberships."""

    def __init__(self, db):
        self.db = db

    def save_principal(self, principal: Principal) -> None:
        with self.db.conn:
            self.db.conn.execute(
                "INSERT INTO principals (id, name, active, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, active=excluded.active",
                (principal.id, principal.name, int(principal.active), principal.created_at.isoformat()),
            )

    def save_membership(self, membership: Membership) -> None:
        with self.db.conn:
            self.db.conn.execute(
                "INSERT INTO tenant_memberships (principal_id, tenant_id, role, active, created_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(principal_id, tenant_id) DO UPDATE SET role=excluded.role, active=excluded.active",
                (membership.principal_id, membership.tenant_id, membership.role.value, int(membership.active), membership.created_at.isoformat()),
            )

    def get_context(self, principal_id: str, tenant_id: str) -> Optional[IdentityContext]:
        row = self.db.conn.execute(
            "SELECT p.id AS principal_id, p.active AS principal_active, m.tenant_id, m.role, m.active AS membership_active "
            "FROM principals p JOIN tenant_memberships m ON m.principal_id=p.id "
            "WHERE p.id=? AND m.tenant_id=?",
            (principal_id, tenant_id),
        ).fetchone()
        if not row or not row["principal_active"] or not row["membership_active"]:
            return None
        return IdentityContext(
            principal_id=row["principal_id"], tenant_id=row["tenant_id"], role=MembershipRole(row["role"])
        )

    def require_context(self, principal_id: str, tenant_id: str) -> IdentityContext:
        context = self.get_context(principal_id, tenant_id)
        if context is None:
            raise PermissionError("Authenticated principal has no active membership in tenant")
        return context
