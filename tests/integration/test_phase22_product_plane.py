import os

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
