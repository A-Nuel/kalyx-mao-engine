import pytest

from src.domain.blockchain import BlockchainTransactionIntent
from src.domain.orbio_activation import OrbioCreditActivationIntent
from src.domain.events import canonical_json
from src.domain.enums import ActionType
from src.execution.authority import BlockchainExecutionAuthority
from src.governance.execution_approval import ExecutionApprovalManager
from src.identity.wallet import WalletIdentity
from src.persistence.database import Database
from src.settlement.blockchain.signer import ExternalTransactionSigner


def _intent(tenant="tenant-a", org="org-a"):
    return OrbioCreditActivationIntent(
        tenant_id=tenant,
        organisation_id=org,
        operation_id="op-phase21-e2e",
        chain_id=4663,
        network="robinhood",
        credit_contract="0xE33322DA1380e61E5Ae5DfB21e7f62924c73004C",
        amount=1_000_000,
        max_fee_per_gas=25_000_000_000,
        max_priority_fee_per_gas=1_500_000_000,
        gas_limit=150_000,
        idempotency_key="phase21-e2e",
    )


def test_execution_authority_is_organisation_bound():
    signer = ExternalTransactionSigner("0x4675b9d0323479b1af399c87331d1d2436e6be99")
    authority = BlockchainExecutionAuthority("auth-a", "tenant-a", "org-a", signer)
    authority.assert_scope("tenant-a", "org-a")
    with pytest.raises(PermissionError):
        authority.assert_scope("tenant-b", "org-a")
    with pytest.raises(PermissionError):
        authority.assert_scope("tenant-a", "org-b")


def test_wallet_identity_contains_no_signing_secret():
    wallet = WalletIdentity(
        wallet_id="wallet-a",
        tenant_id="tenant-a",
        organisation_id="org-a",
        chain_id=4663,
        address="0x4675b9d0323479b1af399c87331d1d2436e6be99",
        provider="external",
    )
    assert "private" not in wallet.__dict__
    assert "key" not in wallet.__dict__
    assert wallet.checksum_address


def test_durable_approval_binds_principal_org_authority_intent_and_policy():
    db = Database(":memory:")
    try:
        manager = ExecutionApprovalManager(db, "test-secret")
        decision = {"decision_id": "dec-1", "result": "ALLOW"}
        approval = manager.issue(
            tenant_id="tenant-a",
            organisation_id="org-a",
            principal_id="principal-a",
            authority_id="auth-a",
            intent_hash="intent-1",
            policy_decision_id="dec-1",
            policy_decision=decision,
        )
        ok, reason = manager.verify_and_consume(
            approval,
            tenant_id="tenant-a",
            organisation_id="org-a",
            principal_id="principal-a",
            authority_id="auth-a",
            intent_hash="intent-1",
            policy_decision_id="dec-1",
            policy_decision=decision,
        )
        assert ok and reason is None
        ok, reason = manager.verify_and_consume(
            approval,
            tenant_id="tenant-a",
            organisation_id="org-a",
            principal_id="principal-a",
            authority_id="auth-a",
            intent_hash="intent-1",
            policy_decision_id="dec-1",
            policy_decision=decision,
        )
        assert not ok and "replay" in reason
    finally:
        db.close()


def test_approval_cannot_cross_organisation_or_authority():
    db = Database(":memory:")
    try:
        manager = ExecutionApprovalManager(db, "test-secret")
        approval = manager.issue(
            tenant_id="tenant-a",
            organisation_id="org-a",
            principal_id="principal-a",
            authority_id="auth-a",
            intent_hash="intent-1",
            policy_decision_id="dec-1",
            policy_decision={"decision_id": "dec-1"},
        )
        ok, reason = manager.verify_and_consume(
            approval,
            tenant_id="tenant-a",
            organisation_id="org-b",
            principal_id="principal-a",
            authority_id="auth-a",
            intent_hash="intent-1",
            policy_decision_id="dec-1",
            policy_decision={"decision_id": "dec-1"},
        )
        assert not ok and "scope" in reason

        ok, reason = manager.verify_and_consume(
            approval,
            tenant_id="tenant-a",
            organisation_id="org-a",
            principal_id="principal-a",
            authority_id="auth-b",
            intent_hash="intent-1",
            policy_decision_id="dec-1",
            policy_decision={"decision_id": "dec-1"},
        )
        assert not ok and "authority" in reason
    finally:
        db.close()


def test_multi_org_e2e_scope_separation():
    db = Database(":memory:")
    try:
        manager = ExecutionApprovalManager(db, "test-secret")
        for org in ("org-a", "org-b"):
            approval = manager.issue(
                tenant_id="tenant-a",
                organisation_id=org,
                principal_id=f"principal-{org}",
                authority_id=f"authority-{org}",
                intent_hash=f"intent-{org}",
                policy_decision_id=f"decision-{org}",
                policy_decision={"decision_id": f"decision-{org}", "org": org},
            )
            ok, _ = manager.verify_and_consume(
                approval,
                tenant_id="tenant-a",
                organisation_id=org,
                principal_id=f"principal-{org}",
                authority_id=f"authority-{org}",
                intent_hash=f"intent-{org}",
                policy_decision_id=f"decision-{org}",
                policy_decision={"decision_id": f"decision-{org}", "org": org},
            )
            assert ok
    finally:
        db.close()


def test_approval_mutation_is_rejected():
    db = Database(":memory:")
    try:
        manager = ExecutionApprovalManager(db, "test-secret")
        approval = manager.issue(
            tenant_id="tenant-a",
            organisation_id="org-a",
            principal_id="principal-a",
            authority_id="auth-a",
            intent_hash="intent-1",
            policy_decision_id="dec-1",
            policy_decision={"decision_id": "dec-1", "limit": 1},
        )
        ok, reason = manager.verify_and_consume(
            approval,
            tenant_id="tenant-a",
            organisation_id="org-a",
            principal_id="principal-a",
            authority_id="auth-a",
            intent_hash="intent-1",
            policy_decision_id="dec-1",
            policy_decision={"decision_id": "dec-1", "limit": 2},
        )
        assert not ok and "hash" in reason
    finally:
        db.close()
