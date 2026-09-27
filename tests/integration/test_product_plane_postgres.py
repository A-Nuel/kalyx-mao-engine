"""PostgreSQL end-to-end coverage for the Product Plane.

These tests specifically exercise the production PostgreSQL boolean boundary
that SQLite previously allowed to hide: identity active flags, wallet
challenge consumption, agent credential revocation, and organisation policy
enablement must remain Python booleans at the application boundary.
"""

from __future__ import annotations

import os

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from src.api.server import app
from src.persistence.factory import create_database

pytestmark = pytest.mark.postgres


def _postgres_available() -> bool:
    url = os.getenv("KALYX_DATABASE_URL", "").strip().lower()
    return url.startswith("postgres://") or url.startswith("postgresql://")


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="KALYX_DATABASE_URL not set to PostgreSQL",
)


@requires_postgres
def test_product_plane_wallet_auth_and_onboarding_round_trip_postgres(monkeypatch):
    """Exercise the real new-wallet -> onboarding path against PostgreSQL.

    This catches type errors that SQLite accepts but PostgreSQL rejects:
    - Principal.active / Membership.active
    - wallet_challenges.consumed
    - organisation_policies.enabled
    It also verifies the subsequent organisation/CEO/provider path used by
    the A+C onboarding flow.
    """
    monkeypatch.setenv("KALYX_POLICY_SECRET", "postgres-product-plane-test-secret")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")
    account = Account.create()
    address = account.address
    client = TestClient(app)

    challenge_response = client.post(
        "/api/v1/product/auth/wallet/challenge",
        json={"address": address, "chain_id": 4663},
    )
    assert challenge_response.status_code == 200, challenge_response.text
    challenge = challenge_response.json()

    signed = Account.sign_message(
        encode_defunct(text=challenge["message"]),
        account.key,
    )
    verify_response = client.post(
        "/api/v1/product/auth/wallet/verify",
        json={
            "challenge_id": challenge["challenge_id"],
            "signature": signed.signature.hex(),
        },
    )
    assert verify_response.status_code == 200, verify_response.text
    auth = verify_response.json()
    token = auth["token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/v1/product/me", headers=headers)
    assert me.status_code == 200, me.text
    identity = next(item for item in me.json()["identities"] if item["kind"] == "wallet")
    assert identity["subject"].lower() == address.lower()

    workspaces = client.get("/api/v1/product/workspaces", headers=headers)
    assert workspaces.status_code == 200, workspaces.text
    assert len(workspaces.json()) == 1
    tenant_id = workspaces.json()[0]["tenant_id"]

    organisation = client.post(
        f"/api/v1/product/workspaces/{tenant_id}/organisations",
        headers=headers,
        json={
            "name": "Postgres Boolean Test Org",
            "mission": "Verify Product Plane PostgreSQL compatibility",
        },
    )
    assert organisation.status_code == 200, organisation.text
    org_id = organisation.json()["organisation_id"]

    ceo = client.post(
        f"/api/v1/product/organisations/{org_id}/agents",
        headers=headers,
        json={
            "name": "CEO-01",
            "role": "CEO",
            "model_name": "openai/gpt-4o-mini",
            "authority_ceiling": 25,
            "allowed_action_types": ["INTERNAL_ANALYSIS"],
        },
    )
    assert ceo.status_code == 200, ceo.text

    policy = client.post(
        f"/api/v1/product/organisations/{org_id}/policies",
        headers=headers,
        json={
            "name": "Default Governance",
            "spending_ceiling": 25,
            "human_approval_threshold": 40,
            "allowed_actions": [],
            "allowed_targets": [],
            "allowed_providers": [],
            "per_agent_ceiling": 25,
            "daily_compute_budget": 100,
            "compute_call_limit": 8,
            "per_agent_compute_call_limit": 3,
            "max_prompt_chars": 20_000,
        },
    )
    assert policy.status_code == 200, policy.text

    agent_key = client.post(
        f"/api/v1/product/organisations/{org_id}/agents/{ceo.json()['agent_id']}/keys",
        headers=headers,
    )
    assert agent_key.status_code == 200, agent_key.text

    revoked = client.post(
        f"/api/v1/product/organisations/{org_id}/agents/{ceo.json()['agent_id']}/keys/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200, revoked.text

    provider = client.post(
        f"/api/v1/product/organisations/{org_id}/providers",
        headers=headers,
        json={
            "provider": "simulated",
            "connection_type": "simulated",
            "metadata": {"source": "postgres-regression-test"},
        },
    )
    assert provider.status_code == 200, provider.text

    bootstrap = client.get(
        f"/api/v1/product/organisations/{org_id}/bootstrap",
        headers=headers,
    )
    assert bootstrap.status_code == 200, bootstrap.text
    readiness = bootstrap.json()["readiness"]
    assert readiness["identity"] is True
    assert readiness["organisation"] is True
    assert readiness["policy"] is True
    assert readiness["agent"] is True
    assert readiness["provider"] is True
    assert readiness["ready_for_mission"] is True

    db = create_database()
    try:
        principal = db.conn.execute(
            "SELECT active FROM principals WHERE id = ?",
            (auth["user"]["principal_id"],),
        ).fetchone()
        assert isinstance(principal["active"], bool)
        assert principal["active"] is True

        membership = db.conn.execute(
            "SELECT active FROM tenant_memberships WHERE principal_id = ? AND tenant_id = ?",
            (auth["user"]["principal_id"], tenant_id),
        ).fetchone()
        assert isinstance(membership["active"], bool)
        assert membership["active"] is True

        challenge_row = db.conn.execute(
            "SELECT consumed FROM wallet_challenges WHERE id = ?",
            (challenge["challenge_id"],),
        ).fetchone()
        assert isinstance(challenge_row["consumed"], bool)
        assert challenge_row["consumed"] is True

        policy_row = db.conn.execute(
            "SELECT enabled FROM organisation_policies WHERE organisation_id = ?",
            (org_id,),
        ).fetchone()
        assert isinstance(policy_row["enabled"], bool)
        assert policy_row["enabled"] is True

        credential_row = db.conn.execute(
            "SELECT revoked FROM agent_credentials WHERE organisation_id = ? AND agent_id = ?",
            (org_id, ceo.json()["agent_id"]),
        ).fetchone()
        assert isinstance(credential_row["revoked"], bool)
        assert credential_row["revoked"] is True

        boolean_columns = db.conn.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (
                    (table_name = 'principals' AND column_name = 'active')
                 OR (table_name = 'tenant_memberships' AND column_name = 'active')
                 OR (table_name = 'wallet_challenges' AND column_name = 'consumed')
                 OR (table_name = 'product_sessions' AND column_name = 'revoked')
                 OR (table_name = 'agent_credentials' AND column_name = 'revoked')
                 OR (table_name = 'organisation_policies' AND column_name = 'enabled')
              )
            ORDER BY table_name, column_name
            """
        ).fetchall()
        assert {(row["table_name"], row["column_name"], row["data_type"]) for row in boolean_columns} == {
            ("agent_credentials", "revoked", "boolean"),
            ("organisation_policies", "enabled", "boolean"),
            ("principals", "active", "boolean"),
            ("product_sessions", "revoked", "boolean"),
            ("tenant_memberships", "active", "boolean"),
            ("wallet_challenges", "consumed", "boolean"),
        }

    finally:
        db.close()


@requires_postgres
def test_product_plane_existing_wallet_can_sign_in_again(monkeypatch):
    """A second challenge for the same wallet must take the existing-identity path."""
    monkeypatch.setenv("KALYX_POLICY_SECRET", "postgres-product-plane-test-secret")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "true")
    account = Account.create()
    client = TestClient(app)

    def sign_in():
        challenge = client.post(
            "/api/v1/product/auth/wallet/challenge",
            json={"address": account.address, "chain_id": 4663},
        )
        assert challenge.status_code == 200, challenge.text
        payload = challenge.json()
        signed = Account.sign_message(
            encode_defunct(text=payload["message"]),
            account.key,
        )
        verified = client.post(
            "/api/v1/product/auth/wallet/verify",
            json={
                "challenge_id": payload["challenge_id"],
                "signature": signed.signature.hex(),
            },
        )
        assert verified.status_code == 200, verified.text
        return payload, verified.json()

    first_challenge, first_auth = sign_in()
    second_challenge, second_auth = sign_in()

    assert first_auth["user"]["principal_id"] == second_auth["user"]["principal_id"]
    assert first_auth["workspace"]["tenant_id"] == second_auth["workspace"]["tenant_id"]

    replay = client.post(
        "/api/v1/product/auth/wallet/verify",
        json={
            "challenge_id": first_challenge["challenge_id"],
            "signature": "0x" + "00" * 65,
        },
    )
    assert replay.status_code == 400, replay.text

    db = create_database()
    try:
        rows = db.conn.execute(
            "SELECT consumed FROM wallet_challenges WHERE id IN (?, ?)",
            (first_challenge["challenge_id"], second_challenge["challenge_id"]),
        ).fetchall()
        assert all(isinstance(row["consumed"], bool) and row["consumed"] is True for row in rows)
    finally:
        db.close()
