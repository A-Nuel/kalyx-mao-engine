import pytest

from src.identity.context import IdentityContext
from src.identity.execution_context import ExecutionContext
from src.identity.models import MembershipRole


def test_execution_context_is_derived_from_trusted_identity():
    identity = IdentityContext("principal-a", "tenant-a", MembershipRole.OPERATOR)

    ctx = ExecutionContext.from_identity(
        identity,
        organisation_id="org-a",
        request_id="req-123",
    )

    assert ctx.principal_id == "principal-a"
    assert ctx.tenant_id == "tenant-a"
    assert ctx.organisation_id == "org-a"
    assert ctx.role == "operator"
    assert ctx.request_id == "req-123"
    assert ctx.can_write()
    assert not ctx.can_administer()


def test_execution_context_rejects_cross_tenant_resource():
    ctx = ExecutionContext("principal-a", "tenant-a", "org-a", "operator")

    with pytest.raises(PermissionError, match="Tenant boundary violation"):
        ctx.require_tenant("tenant-b")

    with pytest.raises(PermissionError, match="Tenant boundary violation"):
        ctx.require_organisation("tenant-b", "org-a")


def test_execution_context_rejects_cross_organisation_resource():
    ctx = ExecutionContext("principal-a", "tenant-a", "org-a", "operator")

    with pytest.raises(PermissionError, match="Organisation boundary violation"):
        ctx.require_organisation("tenant-a", "org-b")


def test_organisation_scope_is_required_for_organisation_resources():
    ctx = ExecutionContext("principal-a", "tenant-a", None, "owner")

    with pytest.raises(PermissionError, match="Organisation scope is required"):
        ctx.require_organisation("tenant-a", "org-a")


def test_execution_context_rejects_blank_organisation():
    identity = IdentityContext("principal-a", "tenant-a", MembershipRole.OWNER)

    with pytest.raises(ValueError, match="organisation_id must be non-empty"):
        ExecutionContext.from_identity(identity, organisation_id="   ")


def test_execution_context_preserves_role_authority():
    owner = ExecutionContext("p1", "t1", "o1", "owner")
    viewer = ExecutionContext("p2", "t1", "o1", "viewer")

    assert owner.can_write()
    assert owner.can_administer()
    assert not viewer.can_write()
    assert not viewer.can_administer()
