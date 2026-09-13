import pytest
from src.identity.context import IdentityContext
from src.identity.models import Principal, Membership, MembershipRole
from src.identity.repository import IdentityRepository
from src.persistence.database import Database


def test_identity_context_requires_matching_tenant():
    ctx = IdentityContext("p1", "tenant-a", MembershipRole.OPERATOR)
    ctx.require_tenant("tenant-a")
    with pytest.raises(PermissionError):
        ctx.require_tenant("tenant-b")


def test_membership_is_required_and_role_is_authoritative():
    db = Database(":memory:")
    try:
        repo = IdentityRepository(db)
        repo.save_principal(Principal(id="p1", name="Alice"))
        repo.save_membership(Membership(principal_id="p1", tenant_id="tenant-demo", role=MembershipRole.VIEWER))
        ctx = repo.require_context("p1", "tenant-demo")
        assert ctx.role is MembershipRole.VIEWER
        assert not ctx.can_write()
        assert not ctx.can_administer()
        assert repo.get_context("p1", "tenant-other") is None
    finally:
        db.close()


def test_inactive_membership_cannot_authorize():
    db = Database(":memory:")
    try:
        repo = IdentityRepository(db)
        repo.save_principal(Principal(id="p1", name="Alice"))
        repo.save_membership(Membership(principal_id="p1", tenant_id="tenant-demo", role=MembershipRole.OWNER, active=False))
        with pytest.raises(PermissionError):
            repo.require_context("p1", "tenant-demo")
    finally:
        db.close()
