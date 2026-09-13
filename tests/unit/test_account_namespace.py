import pytest

from src.tenancy.account_namespace import AccountNamespace, SYSTEM_MINT


def test_physical_account_includes_tenant_and_org():
    ns = AccountNamespace(tenant_id="tenant-a", organisation_id="org-1")
    assert ns.physical("TREASURY") == "tenant-a:org-1:TREASURY"
    assert ns.physical("ESCROW") == "tenant-a:org-1:ESCROW"
    assert ns.physical(SYSTEM_MINT) == SYSTEM_MINT


def test_rejects_caller_supplied_physical_strings():
    ns = AccountNamespace(tenant_id="tenant-a", organisation_id="org-1")
    with pytest.raises(ValueError):
        ns.physical("tenant-a:org-1:TREASURY")


def test_two_orgs_have_distinct_accounts():
    a = AccountNamespace("tenant-a", "org-a")
    b = AccountNamespace("tenant-a", "org-b")
    assert a.physical("TREASURY") != b.physical("TREASURY")


def test_two_tenants_have_distinct_accounts():
    a = AccountNamespace("tenant-a", "org-1")
    b = AccountNamespace("tenant-b", "org-1")
    assert a.physical("TREASURY") != b.physical("TREASURY")
