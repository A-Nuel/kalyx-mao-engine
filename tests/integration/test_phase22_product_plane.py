import os
import urllib.parse

import pytest
from fastapi.testclient import TestClient
from eth_account import Account
from eth_account.messages import encode_defunct


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "demo")
    monkeypatch.setenv("KALYX_IDENTITY_AUTH", "false")
    monkeypatch.setenv("KALYX_DATABASE_URL", "")
    monkeypatch.setenv("KALYX_CREDENTIAL_ENCRYPTION_KEY", "")
    from src.api.server import app
    return TestClient(app)


def test_wallet_onboarding_and_configuration(client):
    account = Account.create()
    challenge = client.post(
        "/api/v1/product/auth/wallet/challenge",
        json={"address": account.address, "chain_id": 4663},
    )
    assert challenge.status_code == 200
    payload = challenge.json()
    signed = Account.sign_message(encode_defunct(text=payload["message"]), account.key)

    verified = client.post(
        "/api/v1/product/auth/wallet/verify",
        json={"challenge_id": payload["challenge_id"], "signature": signed.signature.hex()},
    )
    assert verified.status_code == 200
    token = verified.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/v1/product/me", headers=headers)
    assert me.status_code == 200
    tenant_id = me.json()["workspace"]["tenant_id"]

    org = client.post(
        f"/api/v1/product/workspaces/{tenant_id}/organisations",
        headers=headers,
        json={"name": "Revenue Org", "mission": "Build governed revenue"},
    )
    assert org.status_code == 200
    org_id = org.json()["organisation_id"]

    agent = client.post(
        f"/api/v1/product/organisations/{org_id}/agents",
        headers=headers,
        json={
            "name": "CEO",
            "role": "CEO",
            "model_name": "openai/gpt-4o-mini",
            "authority_ceiling": 25,
            "allowed_action_types": ["INTERNAL_ANALYSIS", "DATA_FETCH"],
        },
    )
    assert agent.status_code == 200
    agent_id = agent.json()["agent_id"]

    key = client.post(
        f"/api/v1/product/organisations/{org_id}/agents/{agent_id}/keys",
        headers=headers,
    )
    assert key.status_code == 200
    raw_key = key.json()["key"]
    assert raw_key.startswith("kal_agent_")
    assert raw_key not in key.json().get("warning", "")

    policy = client.post(
        f"/api/v1/product/organisations/{org_id}/policies",
        headers=headers,
        json={
            "name": "Default Governance",
            "spending_ceiling": 25,
            "human_approval_threshold": 20,
            "allowed_actions": ["INTERNAL_ANALYSIS", "DATA_FETCH"],
            "daily_compute_budget": 50,
        },
    )
    assert policy.status_code == 200

    provider = client.post(
        f"/api/v1/product/organisations/{org_id}/providers",
        headers=headers,
        json={"provider": "orbio", "connection_type": "api_key", "api_key": "sk-orbio-test"},
    )
    assert provider.status_code == 200
    assert provider.json()["secret_returned"] is False

    bootstrap = client.get(
        f"/api/v1/product/organisations/{org_id}/bootstrap",
        headers=headers,
    )
    assert bootstrap.status_code == 200
    readiness = bootstrap.json()["readiness"]
    assert readiness["identity"]
    assert readiness["organisation"]
    assert readiness["policy"]
    assert readiness["agent"]
    assert readiness["provider"]
    assert readiness["ready_for_mission"]


def test_wallet_signature_cannot_be_replayed(client):
    account = Account.create()
    challenge = client.post(
        "/api/v1/product/auth/wallet/challenge",
        json={"address": account.address},
    ).json()
    signed = Account.sign_message(encode_defunct(text=challenge["message"]), account.key)
    body = {"challenge_id": challenge["challenge_id"], "signature": signed.signature.hex()}
    assert client.post("/api/v1/product/auth/wallet/verify", json=body).status_code == 200
    assert client.post("/api/v1/product/auth/wallet/verify", json=body).status_code == 400


def test_agent_credential_is_hash_only_and_revocable(client):
    account = Account.create()
    challenge = client.post("/api/v1/product/auth/wallet/challenge", json={"address": account.address}).json()
    signed = Account.sign_message(encode_defunct(text=challenge["message"]), account.key)
    token = client.post(
        "/api/v1/product/auth/wallet/verify",
        json={"challenge_id": challenge["challenge_id"], "signature": signed.signature.hex()},
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    tenant = client.get("/api/v1/product/me", headers=headers).json()["workspace"]["tenant_id"]
    org = client.post(
        f"/api/v1/product/workspaces/{tenant}/organisations",
        headers=headers,
        json={"name": "Key Org", "mission": "Test credentials"},
    ).json()["organisation_id"]
    agent = client.post(
        f"/api/v1/product/organisations/{org}/agents",
        headers=headers,
        json={"name": "Researcher", "role": "RESEARCHER"},
    ).json()["agent_id"]
    key = client.post(f"/api/v1/product/organisations/{org}/agents/{agent}/keys", headers=headers).json()["key"]

    who = client.get("/api/v1/product/agent/me", headers={"X-Kalyx-Agent-Key": key})
    assert who.status_code == 200
    assert who.json()["agent_id"] == agent

    revoked = client.post(
        f"/api/v1/product/organisations/{org}/agents/{agent}/keys/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200
    assert client.get("/api/v1/product/agent/me", headers={"X-Kalyx-Agent-Key": key}).status_code == 401


def test_policy_ceiling_blocks_over_budget_mission(client):
    account = Account.create()
    challenge = client.post("/api/v1/product/auth/wallet/challenge", json={"address": account.address}).json()
    signed = Account.sign_message(encode_defunct(text=challenge["message"]), account.key)
    token = client.post(
        "/api/v1/product/auth/wallet/verify",
        json={"challenge_id": challenge["challenge_id"], "signature": signed.signature.hex()},
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    tenant = client.get("/api/v1/product/me", headers=headers).json()["workspace"]["tenant_id"]
    org = client.post(
        f"/api/v1/product/workspaces/{tenant}/organisations",
        headers=headers,
        json={"name": "Governed Org", "mission": "Test limits"},
    ).json()["organisation_id"]
    client.post(
        f"/api/v1/product/organisations/{org}/agents",
        headers=headers,
        json={"name": "CEO", "role": "CEO", "authority_ceiling": 25},
    )
    client.post(
        f"/api/v1/product/organisations/{org}/policies",
        headers=headers,
        json={"name": "Strict", "spending_ceiling": 1, "per_agent_ceiling": 1, "human_approval_threshold": 1},
    )
    result = client.post(
        f"/api/v1/product/organisations/{org}/missions",
        headers=headers,
        json={"objective": "Do something", "budget": 2},
    )
    assert result.status_code == 403


def test_multiple_workspaces_are_not_implicitly_collapsed(client):
    account = Account.create()
    challenge = client.post("/api/v1/product/auth/wallet/challenge", json={"address": account.address}).json()
    signed = Account.sign_message(encode_defunct(text=challenge["message"]), account.key)
    token = client.post(
        "/api/v1/product/auth/wallet/verify",
        json={"challenge_id": challenge["challenge_id"], "signature": signed.signature.hex()},
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    first = client.get("/api/v1/product/me", headers=headers).json()["workspace"]["tenant_id"]
    second = client.post("/api/v1/product/workspaces", headers=headers, json={"name": "Second Workspace"}).json()["tenant_id"]
    assert first != second
    workspaces = client.get("/api/v1/product/workspaces", headers=headers)
    assert workspaces.status_code == 200
    assert {w["id"] for w in workspaces.json()} >= {first, second}
    org = client.post(
        f"/api/v1/product/workspaces/{second}/organisations",
        headers=headers,
        json={"name": "Second Org", "mission": "Workspace isolation"},
    )
    assert org.status_code == 200


def _wallet_session(client):
    account = Account.create()
    challenge = client.post(
        "/api/v1/product/auth/wallet/challenge",
        json={"address": account.address, "chain_id": 4663},
    ).json()
    signed = Account.sign_message(encode_defunct(text=challenge["message"]), account.key)
    token = client.post(
        "/api/v1/product/auth/wallet/verify",
        json={"challenge_id": challenge["challenge_id"], "signature": signed.signature.hex()},
    ).json()["token"]
    return token


def test_orbio_oauth_pkce_callback_stores_encrypted_connection(client, monkeypatch):
    monkeypatch.setenv("ORBIO_CLIENT_ID", "orbio-client-test")
    monkeypatch.setenv("ORBIO_CLIENT_SECRET", "orbio-secret-test")
    monkeypatch.setenv("KALYX_PUBLIC_BASE_URL", "https://kalyx.example")
    token = _wallet_session(client)
    headers = {"Authorization": f"Bearer {token}"}
    tenant = client.get("/api/v1/product/me", headers=headers).json()["workspace"]["tenant_id"]
    org = client.post(
        f"/api/v1/product/workspaces/{tenant}/organisations",
        headers=headers,
        json={"name": "Orbio Org", "mission": "Use delegated compute"},
    ).json()["organisation_id"]

    start = client.get(
        f"/api/v1/product/organisations/{org}/providers/orbio/authorize",
        headers=headers,
    )
    assert start.status_code == 200
    auth_url = start.json()["authorization_url"]
    parsed = urllib.parse.urlparse(auth_url)
    params = urllib.parse.parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.orbio.so"
    assert params["client_id"] == ["orbio-client-test"]
    assert params["redirect_uri"] == ["https://kalyx.example/api/v1/product/auth/orbio/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"]
    assert params["state"]

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, **kwargs):
        assert url == "https://www.orbio.so/api/oauth/token"
        assert kwargs["auth"] == ("orbio-client-test", "orbio-secret-test")
        assert kwargs["data"]["grant_type"] == "authorization_code"
        assert kwargs["data"]["redirect_uri"] == "https://kalyx.example/api/v1/product/auth/orbio/callback"
        assert kwargs["data"]["code_verifier"]
        return FakeResponse(200, {
            "access_token": "orbio_at_test",
            "refresh_token": "orbio_rt_test",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid profile email wallet balance inference tools",
        })

    def fake_get(url, **kwargs):
        assert url == "https://www.orbio.so/api/oauth/userinfo"
        assert kwargs["headers"]["Authorization"] == "Bearer orbio_at_test"
        return FakeResponse(200, {
            "sub": "orbio-user-1",
            "email": "user@example.com",
            "email_verified": True,
            "wallet_address": "0x123",
            "chain_id": 4663,
            "iss": "https://www.orbio.so",
        })

    monkeypatch.setattr("src.api.product_plane.httpx.post", fake_post)
    monkeypatch.setattr("src.api.product_plane.httpx.get", fake_get)

    callback = client.get(
        "/api/v1/product/auth/orbio/callback",
        params={"code": "auth-code", "state": params["state"][0], "iss": "https://www.orbio.so"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == f"/onboarding?orbio=connected&org={org}"

    providers = client.get(
        f"/api/v1/product/organisations/{org}/providers",
        headers=headers,
    ).json()
    assert len(providers) == 1
    assert providers[0]["provider"] == "orbio"
    assert providers[0]["connection_type"] == "oauth2"
    assert providers[0]["metadata"]["subject"] == "orbio-user-1"

    from src.persistence.factory import create_database
    db = create_database()
    try:
        row = db.conn.execute(
            "SELECT secret_ciphertext FROM provider_connections WHERE organisation_id = ? AND provider = 'orbio'",
            (org,),
        ).fetchone()
        assert row
        assert "orbio_at_test" not in row["secret_ciphertext"]
        assert "orbio_rt_test" not in row["secret_ciphertext"]
    finally:
        db.close()

    replay = client.get(
        "/api/v1/product/auth/orbio/callback",
        params={"code": "auth-code", "state": params["state"][0], "iss": "https://www.orbio.so"},
        follow_redirects=False,
    )
    assert replay.status_code == 400


def test_orbio_oauth_rejects_unexpected_issuer(client, monkeypatch):
    monkeypatch.setenv("ORBIO_CLIENT_ID", "orbio-client-test")
    monkeypatch.setenv("ORBIO_CLIENT_SECRET", "orbio-secret-test")
    monkeypatch.setenv("KALYX_PUBLIC_BASE_URL", "https://kalyx.example")
    token = _wallet_session(client)
    headers = {"Authorization": f"Bearer {token}"}
    tenant = client.get("/api/v1/product/me", headers=headers).json()["workspace"]["tenant_id"]
    org = client.post(
        f"/api/v1/product/workspaces/{tenant}/organisations",
        headers=headers,
        json={"name": "Issuer Org", "mission": "Reject issuer spoofing"},
    ).json()["organisation_id"]
    start = client.get(
        f"/api/v1/product/organisations/{org}/providers/orbio/authorize",
        headers=headers,
    )
    state = urllib.parse.parse_qs(urllib.parse.urlparse(start.json()["authorization_url"]).query)["state"][0]
    result = client.get(
        "/api/v1/product/auth/orbio/callback",
        params={"code": "auth-code", "state": state, "iss": "https://evil.example"},
        follow_redirects=False,
    )
    assert result.status_code == 400
