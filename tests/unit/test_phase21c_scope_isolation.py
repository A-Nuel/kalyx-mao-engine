import pytest

from src.api.server import _org_id_or_404
from src.domain.entities import Organisation
from src.identity.context import IdentityContext
from src.identity.execution_context import (
    ExecutionContext,
    reset_current_execution_context,
    set_current_execution_context,
)
from src.identity.models import MembershipRole
from src.persistence.database import Database
from src.persistence.repositories import SqliteRepository


def _seed_orgs(db):
    repo = SqliteRepository(db)
    repo.save_organisation(Organisation(id="org-a", tenant_id="tenant-a", mission="A"))
    repo.save_organisation(Organisation(id="org-b", tenant_id="tenant-b", mission="B"))
    db.conn.commit()


def test_org_lookup_is_tenant_scoped_when_trusted_context_exists():
    db = Database(":memory:")
    try:
        _seed_orgs(db)
        ctx = ExecutionContext.from_identity(
            IdentityContext("principal-a", "tenant-a", MembershipRole.OWNER),
            organisation_id="org-a",
        )
        token = set_current_execution_context(ctx)
        try:
            assert _org_id_or_404(db, "org-a")["tenant_id"] == "tenant-a"
            with pytest.raises(Exception) as exc:
                _org_id_or_404(db, "org-b")
            assert getattr(exc.value, "status_code", None) == 404
        finally:
            reset_current_execution_context(token)
    finally:
        db.close()


def test_org_lookup_is_organisation_scoped_within_tenant():
    db = Database(":memory:")
    try:
        repo = SqliteRepository(db)
        repo.save_organisation(Organisation(id="org-a", tenant_id="tenant-a", mission="A"))
        repo.save_organisation(Organisation(id="org-c", tenant_id="tenant-a", mission="C"))
        db.conn.commit()

        ctx = ExecutionContext.from_identity(
            IdentityContext("principal-a", "tenant-a", MembershipRole.OWNER),
            organisation_id="org-a",
        )
        token = set_current_execution_context(ctx)
        try:
            with pytest.raises(Exception) as exc:
                _org_id_or_404(db, "org-c")
            assert getattr(exc.value, "status_code", None) == 404
        finally:
            reset_current_execution_context(token)
    finally:
        db.close()


def test_context_reset_prevents_scope_leakage():
    db = Database(":memory:")
    try:
        _seed_orgs(db)
        ctx = ExecutionContext.from_identity(
            IdentityContext("principal-a", "tenant-a", MembershipRole.OWNER),
            organisation_id="org-a",
        )
        token = set_current_execution_context(ctx)
        reset_current_execution_context(token)

        # Without a request-bound trusted context, the helper retains legacy
        # direct-call behavior used by local/test tooling.
        assert _org_id_or_404(db, "org-b")["tenant_id"] == "tenant-b"
    finally:
        db.close()
