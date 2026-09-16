"""Phase 14A unit tests: simulated Orbio provider, secrets, outcomes."""
import pytest
from src.external.models import (
    ExternalProviderOutcome,
    InferenceRequest,
    KeyLifecycleIntent,
    KeyLifecycleOperation,
)
from src.external.orbio.simulated_provider import SimulatedOrbioProvider
from src.external.orbio.service import ExternalEconomyService, ExternalKeyRepository, redact_secrets
from src.persistence.database import Database


def test_simulated_balance_and_key_lifecycle():
    provider = SimulatedOrbioProvider(initial_available="50.00")
    bal = provider.get_balance(tenant_id="t1", organisation_id="o1")
    assert bal.currency == "USD"
    assert bal.available == "50.00"

    intent = KeyLifecycleIntent(
        tenant_id="t1",
        organisation_id="o1",
        operation=KeyLifecycleOperation.CREATE,
        actor_principal_id="principal-1",
        authorization_token="AUTH-test",
    )
    receipt = provider.execute_key_lifecycle(intent)
    assert receipt.outcome == ExternalProviderOutcome.SUCCESS
    assert receipt.key_id is not None
    assert receipt.one_time_secret and receipt.one_time_secret.startswith("sk-orbio-sim-")

    # Secret must appear in one-time receipt but audit_safe_dict must not include it
    safe = receipt.audit_safe_dict()
    assert "one_time_secret" not in safe
    assert safe["secret_delivered"] is True

    status = provider.get_key_status(tenant_id="t1", organisation_id="o1", key_id=receipt.key_id)
    assert status.status == "active"

    revoke = provider.execute_key_lifecycle(
        KeyLifecycleIntent(
            tenant_id="t1",
            organisation_id="o1",
            operation=KeyLifecycleOperation.REVOKE,
            actor_principal_id="principal-1",
            key_id=receipt.key_id,
            authorization_token="AUTH-test",
        )
    )
    assert revoke.outcome == ExternalProviderOutcome.SUCCESS
    assert provider.get_key_status(tenant_id="t1", organisation_id="o1", key_id=receipt.key_id).status == "revoked"


@pytest.mark.parametrize(
    "forced,expected",
    [
        (ExternalProviderOutcome.FAILURE, ExternalProviderOutcome.FAILURE),
        (ExternalProviderOutcome.TIMEOUT, ExternalProviderOutcome.TIMEOUT),
        (ExternalProviderOutcome.UNKNOWN, ExternalProviderOutcome.UNKNOWN),
        (ExternalProviderOutcome.INSUFFICIENT_BALANCE, ExternalProviderOutcome.INSUFFICIENT_BALANCE),
        (ExternalProviderOutcome.KEY_REVOKED, ExternalProviderOutcome.KEY_REVOKED),
    ],
)
def test_simulated_forced_outcomes(forced, expected):
    provider = SimulatedOrbioProvider(force_outcome=forced)
    req = InferenceRequest(
        tenant_id="t1",
        organisation_id="o1",
        mission_id="m1",
        agent_id="a1",
        model="test/model",
        messages=[{"role": "user", "content": "hi"}],
        authorization_token="AUTH-x",
        idempotency_key="idem-1",
    )
    receipt = provider.run_inference(req)
    assert receipt.outcome == expected


def test_inference_success_consumes_simulated_balance():
    provider = SimulatedOrbioProvider(initial_available="1.00", initial_used="0.00")
    req = InferenceRequest(
        tenant_id="t1",
        organisation_id="o1",
        mission_id="m1",
        agent_id="a1",
        model="test/model",
        messages=[{"role": "user", "content": "hi"}],
        authorization_token="AUTH-x",
        idempotency_key="stable-key-abc",
    )
    before = provider.get_balance(tenant_id="t1", organisation_id="o1").available
    receipt = provider.run_inference(req)
    assert receipt.outcome == ExternalProviderOutcome.SUCCESS
    after = provider.get_balance(tenant_id="t1", organisation_id="o1").available
    assert float(after) < float(before)
    assert "cost_usd" in receipt.usage


def test_key_metadata_persisted_without_secret():
    db = Database(":memory:")
    try:
        repo = ExternalKeyRepository(db.conn)
        provider = SimulatedOrbioProvider()
        service = ExternalEconomyService(provider, key_repo=repo)

        receipt = service.create_key(
            tenant_id="tenant-a",
            organisation_id="org-a",
            actor_principal_id="p1",
            authorization_token="AUTH-ok",
        )
        assert receipt.one_time_secret is not None
        records = repo.list_keys("tenant-a", "org-a")
        assert len(records) == 1
        assert records[0].key_id == receipt.key_id
        assert records[0].status == "active"
        # Ensure DB row text does not contain the secret
        dump = str(dict(db.conn.execute("SELECT * FROM external_keys").fetchone()))
        assert receipt.one_time_secret not in dump
    finally:
        db.close()


def test_tenant_isolation_on_key_revoke():
    db = Database(":memory:")
    try:
        repo = ExternalKeyRepository(db.conn)
        provider = SimulatedOrbioProvider()
        service = ExternalEconomyService(provider, key_repo=repo)

        r = service.create_key(
            tenant_id="tenant-a",
            organisation_id="org-a",
            actor_principal_id="p1",
            authorization_token="AUTH-ok",
        )
        with pytest.raises(PermissionError):
            service.revoke_key(
                tenant_id="tenant-b",
                organisation_id="org-b",
                key_id=r.key_id,
                actor_principal_id="p2",
                authorization_token="AUTH-ok",
            )
    finally:
        db.close()


def test_redact_secrets():
    payload = {"api_key": "sk-secret", "nested": {"one_time_secret": "x", "ok": 1}}
    redacted = redact_secrets(payload)
    assert redacted["api_key"] == "***REDACTED***"
    assert redacted["nested"]["one_time_secret"] == "***REDACTED***"
    assert redacted["nested"]["ok"] == 1


def test_create_key_requires_authorization():
    service = ExternalEconomyService(SimulatedOrbioProvider())
    with pytest.raises(PermissionError):
        service.create_key(
            tenant_id="t", organisation_id="o", actor_principal_id="p", authorization_token=""
        )
