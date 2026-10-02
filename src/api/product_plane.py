"""Phase 22 Product Plane.

User-facing account/workspace/organisation primitives sit above the existing
Kalyx control plane. This module deliberately keeps product credentials and
provider credentials separate from execution authority.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address
import httpx
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from src.api.identity_auth import create_identity_token, verify_identity_token
from src.persistence.factory import create_database
from src.identity.models import Membership, MembershipRole, Principal
from src.identity.repository import IdentityRepository
from src.identity.social import configured_social_providers

router = APIRouter(prefix="/api/v1/product", tags=["product-plane"])
logger = logging.getLogger("kalyx.product_plane")


PRODUCT_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_users (
    id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL UNIQUE,
    email TEXT,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_identities (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(kind, subject),
    FOREIGN KEY(user_id) REFERENCES product_users(id)
);
CREATE TABLE IF NOT EXISTS wallet_challenges (
    id TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    message TEXT NOT NULL,
    nonce TEXT NOT NULL UNIQUE,
    expires_at REAL NOT NULL,
    consumed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at REAL NOT NULL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES product_users(id)
);
CREATE TABLE IF NOT EXISTS orbio_oauth_states (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    state_hash TEXT NOT NULL UNIQUE,
    code_verifier_ciphertext TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    expires_at REAL NOT NULL,
    consumed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orbio_oauth_states_expiry
    ON orbio_oauth_states(expires_at, consumed);
CREATE TABLE IF NOT EXISTS agent_credentials (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    scopes_json TEXT NOT NULL,
    expires_at REAL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    last_used_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_credentials_scope
    ON agent_credentials(tenant_id, organisation_id, agent_id);
CREATE TABLE IF NOT EXISTS provider_connections (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    connection_type TEXT NOT NULL,
    secret_ciphertext TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_connections_scope
    ON provider_connections(tenant_id, organisation_id, provider);
CREATE TABLE IF NOT EXISTS organisation_profiles (
    organisation_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_profiles_tenant ON organisation_profiles(tenant_id);

CREATE TABLE IF NOT EXISTS organisation_policies (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_policies_scope
    ON organisation_policies(tenant_id, organisation_id, enabled);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _challenge_chain_id(message: str) -> int:
    """Recover the chain ID bound into the signed challenge without a schema migration."""
    try:
        line = next(line for line in message.splitlines() if line.startswith("Chain ID: "))
        return int(line.split(":", 1)[1].strip())
    except (StopIteration, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Challenge has invalid chain metadata") from exc


def ensure_product_schema(db: Any) -> None:
    statements = [s.strip() for s in PRODUCT_SCHEMA.split(";") if s.strip()]
    with db.conn:
        for statement in statements:
            db.conn.execute(statement)


def _credential_fernet() -> Fernet:
    raw = os.getenv("KALYX_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not raw:
        # A deterministic development key is intentionally never acceptable as
        # a production secret. Production bootstrap should require this setting.
        if os.getenv("KALYX_ENV", "demo").strip().lower() in {"production", "prod"}:
            raise RuntimeError("KALYX_CREDENTIAL_ENCRYPTION_KEY is required in production")
        raw = base64_key_from_secret("kalyx-product-plane-development-key")
    try:
        return Fernet(raw.encode())
    except Exception as exc:
        raise RuntimeError("KALYX_CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key") from exc


def base64_key_from_secret(secret: str) -> str:
    import base64
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()).decode()


def _hash_agent_key(secret: str) -> str:
    pepper = os.getenv("KALYX_POLICY_SECRET", "phase7-demo-policy-secret")
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()


def _bearer(headers_authorization: Optional[str]) -> Optional[str]:
    if headers_authorization and headers_authorization.lower().startswith("bearer "):
        return headers_authorization[7:].strip()
    return None


def _authenticate(db: Any, authorization: Optional[str]) -> tuple[str, str, str]:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Bearer session required")
    principal_id = verify_identity_token(token, os.getenv("KALYX_POLICY_SECRET", "phase7-demo-policy-secret"))
    if not principal_id:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    row = db.conn.execute(
        "SELECT u.id AS user_id, u.principal_id FROM product_users u WHERE u.principal_id = ?",
        (principal_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Product account not found")
    return row["user_id"], principal_id, token


def _tenant_for_user(db: Any, principal_id: str) -> str:
    row = db.conn.execute(
        "SELECT tenant_id FROM tenant_memberships WHERE principal_id = ? AND active = TRUE ORDER BY created_at LIMIT 1",
        (principal_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=409, detail="Account has no active workspace")
    return row["tenant_id"]


def _require_org(db: Any, principal_id: str, org_id: str) -> tuple[str, dict]:
    row = db.conn.execute(
        "SELECT * FROM organisations WHERE id = ?",
        (org_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organisation not found")
    tenant_id = row["tenant_id"]
    membership = IdentityRepository(db).get_context(principal_id, tenant_id)
    if not membership:
        raise HTTPException(status_code=403, detail="Workspace membership required")
    return tenant_id, dict(row)


class WalletChallengeRequest(BaseModel):
    address: str = Field(min_length=42, max_length=42)
    chain_id: int = Field(default=4663, ge=1)


class WalletVerifyRequest(BaseModel):
    challenge_id: str
    signature: str = Field(min_length=10)


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class MissionCreateRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=2_000)
    budget: int = Field(default=25, ge=1, le=1_000)
    live: bool = False


class OrganisationCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    mission: str = Field(min_length=3, max_length=2_000)


class AgentCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    role: str = Field(default="RESEARCHER", min_length=2, max_length=64)
    model_name: str = Field(default="openai/gpt-4o-mini", max_length=160)
    authority_ceiling: int = Field(default=25, ge=0, le=1_000_000)
    allowed_action_types: list[str] = Field(default_factory=lambda: ["INTERNAL_ANALYSIS"])


class PolicyCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    spending_ceiling: int = Field(default=25, ge=0, le=1_000_000)
    human_approval_threshold: int = Field(default=40, ge=0, le=1_000_000)
    allowed_actions: list[str] = Field(default_factory=list)
    allowed_targets: list[str] = Field(default_factory=list)
    allowed_providers: list[str] = Field(default_factory=list)
    per_agent_ceiling: int = Field(default=25, ge=0, le=1_000_000)
    daily_compute_budget: int = Field(default=100, ge=0, le=1_000_000)
    compute_call_limit: int = Field(default=8, ge=0, le=1_000)
    per_agent_compute_call_limit: int = Field(default=3, ge=0, le=100)
    max_prompt_chars: int = Field(default=20_000, ge=1_000, le=100_000)


class ConfiguredGovernanceRule:
    """Non-bypassable organisation-configured limits applied before execution."""
    rule_id = "RULE-CONFIGURED-ORG"
    description = "Organisation policy profile limits spend, actions, targets and providers"

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def evaluate(self, proposal: Any, agent: Any, org: Any, **kwargs: Any) -> Optional[str]:
        ceiling = int(self.config.get("spending_ceiling", 25))
        per_agent = int(self.config.get("per_agent_ceiling", ceiling))
        if proposal.requested_credits > ceiling:
            return f"Organisation policy ceiling exceeded: {proposal.requested_credits} > {ceiling}"
        if proposal.requested_credits > per_agent:
            return f"Agent policy ceiling exceeded: {proposal.requested_credits} > {per_agent}"
        allowed_actions = set(self.config.get("allowed_actions") or [])
        if allowed_actions and proposal.action_type.value not in allowed_actions:
            return f"Action '{proposal.action_type.value}' is not allowed by the organisation policy profile"
        allowed_targets = set(self.config.get("allowed_targets") or [])
        if allowed_targets and proposal.target not in allowed_targets:
            return f"Target '{proposal.target}' is not allowed by the organisation policy profile"
        allowed_providers = set(self.config.get("allowed_providers") or [])
        if allowed_providers:
            provider = proposal.parameters.get("provider") or proposal.parameters.get("provider_name")
            if not provider or provider not in allowed_providers:
                return f"Provider '{provider or 'unspecified'}' is not allowed by the organisation policy profile"
        return None


def get_active_policy_config(db: Any, organisation_id: str) -> dict[str, Any]:
    row = db.conn.execute(
        "SELECT config_json FROM organisation_policies WHERE organisation_id = ? AND enabled = TRUE ORDER BY version DESC LIMIT 1",
        (organisation_id,),
    ).fetchone()
    return json.loads(row["config_json"]) if row else {}


class ProviderConnectionRequest(BaseModel):
    provider: str = Field(min_length=2, max_length=80)
    connection_type: str = Field(default="api_key", max_length=40)
    api_key: Optional[str] = Field(default=None, max_length=4096)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrbioOAuthRefreshResponse(BaseModel):
    organisation_id: str
    provider: str
    status: str


def _orbio_public_base_url() -> str:
    base = os.getenv("KALYX_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="KALYX_PUBLIC_BASE_URL is not configured")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise HTTPException(status_code=500, detail="KALYX_PUBLIC_BASE_URL must be an HTTPS URL")
    return base


def _orbio_client_credentials() -> tuple[str, str]:
    client_id = os.getenv("ORBIO_CLIENT_ID", "").strip()
    client_secret = os.getenv("ORBIO_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="Orbio OAuth is not configured")
    return client_id, client_secret


def _orbio_redirect_uri() -> str:
    return _orbio_public_base_url() + "/auth/orbio/callback"


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _hash_oauth_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _encrypt_secret(value: str) -> str:
    return _credential_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt_secret(value: str) -> str:
    try:
        return _credential_fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Stored Orbio credential could not be decrypted") from exc


def _orbio_token_exchange(
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    client_id, client_secret = _orbio_client_credentials()
    try:
        response = httpx.post(
            "https://www.orbio.so/api/oauth/token",
            auth=(client_id, client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            timeout=15.0,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        logger.exception("Orbio OAuth token exchange failed")
        raise HTTPException(status_code=502, detail="Could not reach Orbio token endpoint") from exc
    if response.status_code != 200:
        try:
            detail = response.json().get("error") or response.json().get("error_description")
        except ValueError:
            detail = None
        raise HTTPException(status_code=502, detail=f"Orbio token exchange failed{': ' + detail if detail else ''}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Orbio returned invalid token response") from exc
    if not payload.get("access_token") or not payload.get("refresh_token"):
        raise HTTPException(status_code=502, detail="Orbio token response is missing required tokens")
    return payload


def _orbio_userinfo(access_token: str) -> dict[str, Any]:
    try:
        response = httpx.get(
            "https://www.orbio.so/api/oauth/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        logger.exception("Orbio OAuth userinfo request failed")
        raise HTTPException(status_code=502, detail="Could not reach Orbio userinfo endpoint") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Orbio userinfo request failed")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Orbio returned invalid userinfo") from exc


def _store_orbio_connection(
    db: Any,
    *,
    user_id: str,
    tenant_id: str,
    organisation_id: str,
    token_payload: dict[str, Any],
    userinfo: dict[str, Any],
) -> str:
    now = _now()
    access_token = str(token_payload["access_token"])
    refresh_token = str(token_payload["refresh_token"])
    expires_at = time.time() + int(token_payload.get("expires_in", 3600))
    metadata = {
        "issuer": "https://www.orbio.so",
        "subject": userinfo.get("sub"),
        "email": userinfo.get("email"),
        "email_verified": userinfo.get("email_verified"),
        "wallet_address": userinfo.get("wallet_address"),
        "chain_id": userinfo.get("chain_id"),
        "scope": token_payload.get("scope", ""),
        "token_type": token_payload.get("token_type", "Bearer"),
        "expires_at": expires_at,
        "user_id": user_id,
    }
    existing = db.conn.execute(
        "SELECT id FROM provider_connections WHERE organisation_id = ? AND provider = 'orbio' AND status = 'ACTIVE' ORDER BY created_at DESC LIMIT 1",
        (organisation_id,),
    ).fetchone()
    connection_id = existing["id"] if existing else f"prov_{uuid.uuid4().hex}"
    if existing:
        db.conn.execute(
            "UPDATE provider_connections SET connection_type = ?, secret_ciphertext = ?, metadata_json = ?, status = 'ACTIVE', updated_at = ? WHERE id = ?",
            (
                "oauth2",
                json.dumps({"access_token": _encrypt_secret(access_token), "refresh_token": _encrypt_secret(refresh_token)}, sort_keys=True),
                json.dumps(metadata, sort_keys=True),
                now,
                connection_id,
            ),
        )
    else:
        secret = {
            "access_token": _encrypt_secret(access_token),
            "refresh_token": _encrypt_secret(refresh_token),
        }
        db.conn.execute(
            "INSERT INTO provider_connections (id,tenant_id,organisation_id,provider,connection_type,secret_ciphertext,metadata_json,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                connection_id,
                tenant_id,
                organisation_id,
                "orbio",
                "oauth2",
                json.dumps(secret, sort_keys=True),
                json.dumps(metadata, sort_keys=True),
                "ACTIVE",
                now,
                now,
            ),
        )
    return connection_id


@router.get("/organisations/{org_id}/providers/orbio/authorize")
def authorize_orbio(
    org_id: str,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        user_id, principal_id, _ = _authenticate(db, authorization)
        tenant_id, _ = _require_org(db, principal_id, org_id)
        client_id, _ = _orbio_client_credentials()
        redirect_uri = _orbio_redirect_uri()
        state = secrets.token_urlsafe(48)
        verifier = _pkce_verifier()
        db.conn.execute(
            "INSERT INTO orbio_oauth_states (id,user_id,tenant_id,organisation_id,state_hash,code_verifier_ciphertext,redirect_uri,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"oas_{uuid.uuid4().hex}",
                user_id,
                tenant_id,
                org_id,
                _hash_oauth_state(state),
                _encrypt_secret(verifier),
                redirect_uri,
                time.time() + 300,
                _now(),
            ),
        )
        db.conn.commit()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email wallet balance inference tools",
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        return {
            "authorization_url": "https://www.orbio.so/oauth/authorize?" + urllib.parse.urlencode(params),
            "redirect_uri": redirect_uri,
            "expires_in": 300,
        }
    finally:
        db.close()


@router.get("/auth/orbio/callback")
def orbio_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    iss: Optional[str] = None,
    error: Optional[str] = None,
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"Orbio authorization failed: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Orbio callback requires code and state")
    if iss and iss.rstrip("/") != "https://www.orbio.so":
        raise HTTPException(status_code=400, detail="Unexpected OAuth issuer")
    db = create_database()
    try:
        ensure_product_schema(db)
        row = db.conn.execute(
            "SELECT * FROM orbio_oauth_states WHERE state_hash = ? AND consumed = FALSE",
            (_hash_oauth_state(state),),
        ).fetchone()
        if not row or time.time() > float(row["expires_at"]):
            raise HTTPException(status_code=400, detail="OAuth state is invalid or expired")
        verifier = _decrypt_secret(row["code_verifier_ciphertext"])
        token_payload = _orbio_token_exchange(
            code=code,
            redirect_uri=row["redirect_uri"],
            code_verifier=verifier,
        )
        userinfo = _orbio_userinfo(token_payload["access_token"])
        if userinfo.get("iss") and str(userinfo["iss"]).rstrip("/") != "https://www.orbio.so":
            raise HTTPException(status_code=502, detail="Orbio userinfo issuer mismatch")
        connection_id = _store_orbio_connection(
            db,
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            organisation_id=row["organisation_id"],
            token_payload=token_payload,
            userinfo=userinfo,
        )
        db.conn.execute("UPDATE orbio_oauth_states SET consumed = TRUE WHERE id = ?", (row["id"],))
        db.conn.commit()
        return RedirectResponse(
            url=f"/onboarding?orbio=connected&org={urllib.parse.quote(row['organisation_id'])}",
            status_code=303,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Orbio OAuth callback failed")
        try:
            db.conn.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Orbio connection could not be completed")
    finally:
        db.close()


@router.post("/organisations/{org_id}/providers/orbio/refresh")
def refresh_orbio(
    org_id: str,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        tenant_id, _ = _require_org(db, principal_id, org_id)
        row = db.conn.execute(
            "SELECT * FROM provider_connections WHERE organisation_id = ? AND provider = 'orbio' AND status = 'ACTIVE' ORDER BY updated_at DESC LIMIT 1",
            (org_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Orbio is not connected")
        secret = json.loads(row["secret_ciphertext"] or "{}")
        refresh_token = _decrypt_secret(secret["refresh_token"])
        client_id, client_secret = _orbio_client_credentials()
        try:
            response = httpx.post(
                "https://www.orbio.so/api/oauth/token",
                auth=(client_id, client_secret),
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                timeout=15.0,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Could not reach Orbio token endpoint") from exc
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Orbio refresh failed")
        payload = response.json()
        if not payload.get("access_token") or not payload.get("refresh_token"):
            raise HTTPException(status_code=502, detail="Orbio refresh response is missing required tokens")
        metadata = json.loads(row["metadata_json"] or "{}")
        metadata.update({
            "scope": payload.get("scope", metadata.get("scope", "")),
            "token_type": payload.get("token_type", "Bearer"),
            "expires_at": time.time() + int(payload.get("expires_in", 3600)),
        })
        db.conn.execute(
            "UPDATE provider_connections SET secret_ciphertext = ?, metadata_json = ?, status = 'ACTIVE', updated_at = ? WHERE id = ? AND tenant_id = ?",
            (
                json.dumps({
                    "access_token": _encrypt_secret(payload["access_token"]),
                    "refresh_token": _encrypt_secret(payload["refresh_token"]),
                }, sort_keys=True),
                json.dumps(metadata, sort_keys=True),
                _now(),
                row["id"],
                tenant_id,
            ),
        )
        db.conn.commit()
        return {"organisation_id": org_id, "provider": "orbio", "status": "ACTIVE"}
    finally:
        db.close()


@router.delete("/organisations/{org_id}/providers/orbio")
def disconnect_orbio(
    org_id: str,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        tenant_id, _ = _require_org(db, principal_id, org_id)
        row = db.conn.execute(
            "SELECT * FROM provider_connections WHERE organisation_id = ? AND provider = 'orbio' AND status = 'ACTIVE' ORDER BY updated_at DESC LIMIT 1",
            (org_id,),
        ).fetchone()
        if not row:
            return {"organisation_id": org_id, "provider": "orbio", "status": "DISCONNECTED"}
        secret = json.loads(row["secret_ciphertext"] or "{}")
        refresh_token = _decrypt_secret(secret["refresh_token"])
        client_id, client_secret = _orbio_client_credentials()
        try:
            response = httpx.post(
                "https://www.orbio.so/api/oauth/revoke",
                auth=(client_id, client_secret),
                data={"refresh_token": refresh_token},
                timeout=15.0,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Could not reach Orbio revoke endpoint") from exc
        if response.status_code not in {200, 204}:
            raise HTTPException(status_code=502, detail="Orbio disconnect failed")
        db.conn.execute(
            "UPDATE provider_connections SET status = 'REVOKED', updated_at = ? WHERE id = ? AND tenant_id = ?",
            (_now(), row["id"], tenant_id),
        )
        db.conn.commit()
        return {"organisation_id": org_id, "provider": "orbio", "status": "DISCONNECTED"}
    finally:
        db.close()


@router.get("/auth/social/providers")
def social_providers() -> dict[str, Any]:
    return {"providers": configured_social_providers(), "mode": "oauth-oidc-adapter"}


@router.get("/auth/wallet/config")
def wallet_config() -> dict[str, Any]:
    """Return public wallet-connector configuration; never expose private credentials."""
    project_id = os.getenv("KALYX_REOWN_PROJECT_ID", "").strip()
    return {
        "walletconnect": {
            "configured": bool(project_id),
            "project_id": project_id,
            "provider": "reown-appkit" if project_id else None,
        },
        "chain": {
            "id": 4663,
            "name": "Robinhood Chain",
            "rpc_url": "https://rpc.mainnet.chain.robinhood.com",
            "currency": "ETH",
            "explorer": "https://robinhoodchain.blockscout.com",
        },
    }


@router.post("/auth/wallet/challenge")
def wallet_challenge(request: WalletChallengeRequest) -> dict[str, Any]:
    address = request.address
    try:
        address = to_checksum_address(address)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid EVM wallet address") from exc

    db = create_database()
    try:
        ensure_product_schema(db)
        nonce = secrets.token_urlsafe(24)
        challenge_id = f"wch_{uuid.uuid4().hex}"
        message = (
            "Kalyx sign-in\n\n"
            "This signature authenticates your wallet to Kalyx. "
            "It does not authorize a blockchain transaction.\n\n"
            f"Address: {address}\n"
            f"Chain ID: {request.chain_id}\n"
            f"Nonce: {nonce}\n"
            f"Issued At: {_now()}"
        )
        expires = time.time() + 300
        db.conn.execute(
            "INSERT INTO wallet_challenges (id,address,message,nonce,expires_at,created_at) VALUES (?,?,?,?,?,?)",
            (challenge_id, address, message, nonce, expires, _now()),
        )
        db.conn.commit()
        return {"challenge_id": challenge_id, "message": message, "expires_at": expires}
    finally:
        db.close()


@router.post("/auth/wallet/verify")
def wallet_verify(request: WalletVerifyRequest) -> dict[str, Any]:
    """Verify a wallet challenge and atomically establish the product identity/session.

    Production Postgres uses pooled connections. Any exception after the first
    write must explicitly rollback before the connection is returned to the
    pool; otherwise a failed transaction can poison the next request.
    """
    db = create_database()
    try:
        ensure_product_schema(db)
        with db.conn:
            row = db.conn.execute(
                "SELECT * FROM wallet_challenges WHERE id = ? AND consumed = FALSE",
                (request.challenge_id,),
            ).fetchone()
            if not row or time.time() > float(row["expires_at"]):
                raise HTTPException(status_code=400, detail="Challenge is invalid or expired")

            try:
                recovered = Account.recover_message(
                    encode_defunct(text=row["message"]),
                    signature=request.signature,
                )
                recovered = to_checksum_address(recovered)
            except Exception as exc:
                raise HTTPException(status_code=401, detail="Wallet signature verification failed") from exc

            if recovered.lower() != row["address"].lower():
                raise HTTPException(status_code=401, detail="Signature does not match challenged wallet")

            db.conn.execute(
                "UPDATE wallet_challenges SET consumed = TRUE WHERE id = ?",
                (request.challenge_id,),
            )

            identity = db.conn.execute(
                "SELECT i.user_id FROM product_identities i WHERE i.kind = 'wallet' AND lower(i.subject) = lower(?)",
                (recovered,),
            ).fetchone()

            if identity:
                user_id = identity["user_id"]
                user = db.conn.execute(
                    "SELECT * FROM product_users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if not user:
                    raise HTTPException(status_code=500, detail="Wallet identity is missing its product account")
                principal_id = user["principal_id"]
                tenant_id = _tenant_for_user(db, principal_id)
            else:
                user_id = f"usr_{uuid.uuid4().hex}"
                principal_id = f"prn_{uuid.uuid4().hex}"
                tenant_id = f"ws_{uuid.uuid4().hex}"
                display_name = f"{recovered[:6]}…{recovered[-4:]}"

                db.conn.execute(
                    "INSERT INTO product_users (id,principal_id,display_name,created_at) VALUES (?,?,?,?)",
                    (user_id, principal_id, display_name, _now()),
                )
                db.conn.execute(
                    "INSERT INTO product_identities (id,user_id,kind,subject,metadata_json,created_at) VALUES (?,?,?,?,?,?)",
                    (
                        f"ident_{uuid.uuid4().hex}",
                        user_id,
                        "wallet",
                        recovered,
                        json.dumps({"chain_id": _challenge_chain_id(row["message"])}),
                        _now(),
                    ),
                )
                db.conn.execute(
                    "INSERT INTO tenants (id,name,status,created_at) VALUES (?,?,?,?)",
                    (tenant_id, f"{display_name}'s Workspace", "active", _now()),
                )
                IdentityRepository(db).save_principal(
                    Principal(id=principal_id, name=display_name, active=True)
                )
                IdentityRepository(db).save_membership(
                    Membership(
                        principal_id=principal_id,
                        tenant_id=tenant_id,
                        role=MembershipRole.OWNER,
                        active=True,
                    )
                )

            token = create_identity_token(
                principal_id,
                os.getenv("KALYX_POLICY_SECRET", "phase7-demo-policy-secret"),
                ttl_seconds=86400,
            )
            db.conn.execute(
                "INSERT INTO product_sessions (id,user_id,principal_id,token_hash,expires_at,created_at) VALUES (?,?,?,?,?,?)",
                (
                    f"sess_{uuid.uuid4().hex}",
                    user_id,
                    principal_id,
                    hashlib.sha256(token.encode()).hexdigest(),
                    time.time() + 86400,
                    _now(),
                ),
            )

            return {
                "token": token,
                "user": {"id": user_id, "principal_id": principal_id},
                "workspace": {"tenant_id": tenant_id},
                "wallet": recovered,
                "chain_id": _challenge_chain_id(row["message"]),
            }
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Wallet authentication transaction failed",
            extra={"challenge_id": request.challenge_id},
        )
        try:
            db.conn.rollback()
        except Exception:
            logger.exception("Failed to rollback wallet authentication transaction")
        raise HTTPException(
            status_code=500,
            detail="Wallet authentication could not be completed",
        )
    finally:
        db.close()


@router.get("/me")
def me(authorization: Optional[str] = Header(default=None, alias="Authorization")) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        user_id, principal_id, _ = _authenticate(db, authorization)
        tenant_id = _tenant_for_user(db, principal_id)
        user = db.conn.execute("SELECT * FROM product_users WHERE id = ?", (user_id,)).fetchone()
        identities = db.conn.execute(
            "SELECT kind, subject, metadata_json FROM product_identities WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        orgs = db.conn.execute(
            """
            SELECT o.id, o.mission, o.state, o.created_at,
                   p.display_name, p.description
            FROM organisations o
            LEFT JOIN organisation_profiles p ON p.organisation_id = o.id
            WHERE o.tenant_id = ?
            ORDER BY o.created_at DESC
            """,
            (tenant_id,),
        ).fetchall()
        return {
            "user": dict(user),
            "identities": [
                {**dict(i), "metadata": json.loads(i["metadata_json"] or "{}")}
                for i in identities
            ],
            "workspace": {"tenant_id": tenant_id},
            "organisations": [dict(o) for o in orgs],
        }
    finally:
        db.close()


@router.get("/workspaces")
def list_workspaces(authorization: Optional[str] = Header(default=None, alias="Authorization")) -> list[dict[str, Any]]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        rows = db.conn.execute(
            "SELECT t.id, t.name, t.status, m.role FROM tenants t JOIN tenant_memberships m ON m.tenant_id=t.id WHERE m.principal_id=? AND m.active = TRUE ORDER BY t.created_at",
            (principal_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.post("/workspaces")
def create_workspace(
    request: WorkspaceCreateRequest,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        user_id, principal_id, _ = _authenticate(db, authorization)
        tenant_id = f"ws_{uuid.uuid4().hex}"
        db.conn.execute(
            "INSERT INTO tenants (id,name,status,created_at) VALUES (?,?,?,?)",
            (tenant_id, request.name.strip(), "active", _now()),
        )
        IdentityRepository(db).save_membership(
            Membership(principal_id=principal_id, tenant_id=tenant_id, role=MembershipRole.OWNER, active=True)
        )
        db.conn.commit()
        return {"tenant_id": tenant_id, "name": request.name.strip(), "role": "owner"}
    finally:
        db.close()


@router.post("/workspaces/{tenant_id}/organisations")
def create_organisation(
    tenant_id: str,
    request: OrganisationCreateRequest,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        context = IdentityRepository(db).get_context(principal_id, tenant_id)
        if not context or not context.can_write():
            raise HTTPException(status_code=403, detail="Workspace write permission required")
        org_id = f"org_{uuid.uuid4().hex[:16]}"
        created_at = _now()
        db.conn.execute(
            "INSERT INTO organisations (id,tenant_id,mission,treasury_balance,state,created_at) VALUES (?,?,?,?,?,?)",
            (org_id, tenant_id, request.mission.strip(), 0, "INITIALIZING", created_at),
        )
        db.conn.execute(
            """
            INSERT INTO organisation_profiles
                (organisation_id, tenant_id, display_name, description, created_at, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            (org_id, tenant_id, request.name.strip(), request.mission.strip(), created_at, created_at),
        )
        db.conn.commit()
        return {"organisation_id": org_id, "name": request.name.strip(), "tenant_id": tenant_id, "state": "INITIALIZING"}
    finally:
        db.close()


@router.get("/organisations/{org_id}/agents")
def list_agents(org_id: str, authorization: Optional[str] = Header(default=None, alias="Authorization")) -> list[dict[str, Any]]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        tenant_id, _ = _require_org(db, principal_id, org_id)
        rows = db.conn.execute(
            "SELECT * FROM agents WHERE org_id = ? ORDER BY created_at DESC", (org_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.post("/organisations/{org_id}/agents")
def create_agent(
    org_id: str,
    request: AgentCreateRequest,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        tenant_id, _ = _require_org(db, principal_id, org_id)
        agent_id = f"agent_{uuid.uuid4().hex[:16]}"
        allowed = json.dumps(request.allowed_action_types)
        db.conn.execute(
            "INSERT INTO agents (id,org_id,role,model_name,credit_balance,reputation_score,authority_ceiling,allowed_action_types,status,successful_tasks,failed_tasks,policy_violations,performance_score,risk_score,resource_efficiency,reliability_score,task_history) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (agent_id, org_id, request.role.upper(), request.model_name, 0, 100.0, request.authority_ceiling,
             allowed, "ACTIVE", 0, 0, 0, 100.0, 0.0, 1.0, 100.0, "[]"),
        )
        db.conn.commit()
        return {"agent_id": agent_id, "organisation_id": org_id, "role": request.role.upper(), "model_name": request.model_name}
    finally:
        db.close()


@router.post("/organisations/{org_id}/agents/{agent_id}/keys")
def create_agent_key(
    org_id: str,
    agent_id: str,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        tenant_id, _ = _require_org(db, principal_id, org_id)
        exists = db.conn.execute("SELECT id FROM agents WHERE id = ? AND org_id = ?", (agent_id, org_id)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Agent not found")
        secret = "kal_agent_" + secrets.token_urlsafe(32)
        credential_id = f"akey_{uuid.uuid4().hex}"
        db.conn.execute(
            "INSERT INTO agent_credentials (id,tenant_id,organisation_id,agent_id,name,key_prefix,key_hash,scopes_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (credential_id, tenant_id, org_id, agent_id, "default", secret[:18], _hash_agent_key(secret), json.dumps(["agent:propose"]), _now()),
        )
        db.conn.commit()
        return {
            "credential_id": credential_id,
            "key": secret,
            "warning": "Shown once. Kalyx stores only a hash of this Kalyx credential.",
        }
    finally:
        db.close()


@router.get("/agent/me")
def agent_me(x_kalyx_agent_key: Optional[str] = Header(default=None, alias="X-Kalyx-Agent-Key")) -> dict[str, Any]:
    if not x_kalyx_agent_key:
        raise HTTPException(status_code=401, detail="Kalyx agent credential required")
    db = create_database()
    try:
        ensure_product_schema(db)
        row = db.conn.execute(
            "SELECT id,tenant_id,organisation_id,agent_id,scopes_json,expires_at FROM agent_credentials WHERE key_hash = ? AND revoked = FALSE",
            (_hash_agent_key(x_kalyx_agent_key),),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid or revoked Kalyx agent credential")
        if row["expires_at"] is not None and time.time() > float(row["expires_at"]):
            raise HTTPException(status_code=401, detail="Kalyx agent credential expired")
        db.conn.execute(
            "UPDATE agent_credentials SET last_used_at = ? WHERE id = ?",
            (_now(), row["id"]),
        )
        db.conn.commit()
        return {
            "credential_id": row["id"],
            "tenant_id": row["tenant_id"],
            "organisation_id": row["organisation_id"],
            "agent_id": row["agent_id"],
            "scopes": json.loads(row["scopes_json"]),
        }
    finally:
        db.close()


@router.post("/organisations/{org_id}/agents/{agent_id}/keys/rotate")
def rotate_agent_key(
    org_id: str,
    agent_id: str,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        tenant_id, _ = _require_org(db, principal_id, org_id)
        db.conn.execute(
            "UPDATE agent_credentials SET revoked = TRUE WHERE organisation_id = ? AND agent_id = ? AND revoked = FALSE",
            (org_id, agent_id),
        )
        secret = "kal_agent_" + secrets.token_urlsafe(32)
        credential_id = f"akey_{uuid.uuid4().hex}"
        db.conn.execute(
            "INSERT INTO agent_credentials (id,tenant_id,organisation_id,agent_id,name,key_prefix,key_hash,scopes_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (credential_id, tenant_id, org_id, agent_id, "rotated", secret[:18], _hash_agent_key(secret), json.dumps(["agent:propose"]), _now()),
        )
        db.conn.commit()
        return {"credential_id": credential_id, "key": secret, "warning": "Shown once."}
    finally:
        db.close()


@router.post("/organisations/{org_id}/agents/{agent_id}/keys/revoke")
def revoke_agent_keys(
    org_id: str,
    agent_id: str,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        _require_org(db, principal_id, org_id)
        cur = db.conn.execute(
            "UPDATE agent_credentials SET revoked = TRUE WHERE organisation_id = ? AND agent_id = ? AND revoked = FALSE",
            (org_id, agent_id),
        )
        db.conn.commit()
        return {"revoked": cur.rowcount}
    finally:
        db.close()


@router.post("/organisations/{org_id}/providers")
def connect_provider(
    org_id: str,
    request: ProviderConnectionRequest,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        tenant_id, _ = _require_org(db, principal_id, org_id)
        ciphertext = None
        if request.api_key:
            ciphertext = _credential_fernet().encrypt(request.api_key.encode()).decode()
        connection_id = f"prov_{uuid.uuid4().hex}"
        db.conn.execute(
            "INSERT INTO provider_connections (id,tenant_id,organisation_id,provider,connection_type,secret_ciphertext,metadata_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (connection_id, tenant_id, org_id, request.provider.lower(), request.connection_type, ciphertext, json.dumps(request.metadata), _now(), _now()),
        )
        db.conn.commit()
        return {
            "connection_id": connection_id,
            "provider": request.provider.lower(),
            "connection_type": request.connection_type,
            "status": "ACTIVE",
            "secret_stored": bool(ciphertext),
            "secret_returned": False,
        }
    finally:
        db.close()


@router.get("/organisations/{org_id}/providers")
def list_providers(org_id: str, authorization: Optional[str] = Header(default=None, alias="Authorization")) -> list[dict[str, Any]]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        _require_org(db, principal_id, org_id)
        rows = db.conn.execute(
            "SELECT id,provider,connection_type,status,metadata_json,created_at,updated_at FROM provider_connections WHERE organisation_id = ? ORDER BY created_at DESC",
            (org_id,),
        ).fetchall()
        return [
            {**dict(r), "metadata": json.loads(r["metadata_json"] or "{}")}
            for r in rows
        ]
    finally:
        db.close()


@router.post("/organisations/{org_id}/policies")
def create_policy(
    org_id: str,
    request: PolicyCreateRequest,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        tenant_id, _ = _require_org(db, principal_id, org_id)
        row = db.conn.execute(
            "SELECT COALESCE(MAX(version),0) AS v FROM organisation_policies WHERE organisation_id = ?",
            (org_id,),
        ).fetchone()
        version = int(row["v"]) + 1
        policy_id = f"pol_{uuid.uuid4().hex}"
        config = request.model_dump()
        db.conn.execute(
            "INSERT INTO organisation_policies (id,tenant_id,organisation_id,name,version,enabled,config_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (policy_id, tenant_id, org_id, request.name, version, True, json.dumps(config, sort_keys=True), _now(), _now()),
        )
        db.conn.execute(
            "UPDATE organisations SET state = CASE WHEN state = 'INITIALIZING' THEN 'PLANNING' ELSE state END WHERE id = ?",
            (org_id,),
        )
        db.conn.commit()
        return {"policy_id": policy_id, "version": version, "config": config, "status": "ACTIVE"}
    finally:
        db.close()


@router.get("/organisations/{org_id}/policies")
def list_policies(org_id: str, authorization: Optional[str] = Header(default=None, alias="Authorization")) -> list[dict[str, Any]]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        _require_org(db, principal_id, org_id)
        rows = db.conn.execute(
            "SELECT * FROM organisation_policies WHERE organisation_id = ? ORDER BY version DESC",
            (org_id,),
        ).fetchall()
        return [
            {**dict(r), "config": json.loads(r["config_json"] or "{}")}
            for r in rows
        ]
    finally:
        db.close()


@router.post("/organisations/{org_id}/missions")
def create_mission(
    org_id: str,
    request: MissionCreateRequest,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        tenant_id, _ = _require_org(db, principal_id, org_id)
        policy = get_active_policy_config(db, org_id)
        if not policy:
            raise HTTPException(status_code=409, detail="Configure an organisation policy before creating missions")
        ceo = db.conn.execute(
            "SELECT id FROM agents WHERE org_id = ? AND upper(role) = 'CEO' AND status NOT IN ('SUSPENDED','RETIRED') LIMIT 1",
            (org_id,),
        ).fetchone()
        if not ceo:
            raise HTTPException(status_code=409, detail="Create an active CEO agent before creating missions")
        if request.budget > int(policy.get("spending_ceiling", 25)):
            raise HTTPException(status_code=403, detail="Mission budget exceeds organisation policy ceiling")
        from src.api.mission_service import run_mission
        return run_mission(
            request.objective,
            request.budget,
            live=request.live,
            tenant_id=tenant_id,
            organisation_id=org_id,
        )
    finally:
        db.close()


@router.get("/organisations/{org_id}/bootstrap")
def bootstrap_org(org_id: str, authorization: Optional[str] = Header(default=None, alias="Authorization")) -> dict[str, Any]:
    db = create_database()
    try:
        ensure_product_schema(db)
        _, principal_id, _ = _authenticate(db, authorization)
        tenant_id, org = _require_org(db, principal_id, org_id)
        agents = db.conn.execute("SELECT id,role,model_name,authority_ceiling,status FROM agents WHERE org_id = ?", (org_id,)).fetchall()
        providers = db.conn.execute("SELECT id,provider,connection_type,status,metadata_json FROM provider_connections WHERE organisation_id = ?", (org_id,)).fetchall()
        policies = db.conn.execute("SELECT id,name,version,enabled,config_json FROM organisation_policies WHERE organisation_id = ? ORDER BY version DESC", (org_id,)).fetchall()
        profile = db.conn.execute(
            "SELECT display_name,description,created_at,updated_at FROM organisation_profiles WHERE organisation_id = ?",
            (org_id,),
        ).fetchone()
        return {
            "organisation": {**org, "profile": dict(profile) if profile else None},
            "readiness": {
                "identity": True,
                "organisation": True,
                "policy": bool(policies),
                "agent": bool(agents),
                "provider": bool(providers),
                "ready_for_mission": bool(policies and agents),
            },
            "agents": [dict(a) for a in agents],
            "providers": [dict(p) for p in providers],
            "policies": [dict(p) for p in policies],
        }
    finally:
        db.close()
