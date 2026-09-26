"""Phase 22 Product Plane.

User-facing account/workspace/organisation primitives sit above the existing
Kalyx control plane. This module deliberately keeps product credentials and
provider credentials separate from execution authority.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from src.api.identity_auth import create_identity_token, verify_identity_token
from src.persistence.factory import create_database
from src.identity.models import Membership, MembershipRole, Principal
from src.identity.repository import IdentityRepository

router = APIRouter(prefix="/api/v1/product", tags=["product-plane"])


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
    consumed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at REAL NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES product_users(id)
);
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
    revoked INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS organisation_policies (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_policies_scope
    ON organisation_policies(tenant_id, organisation_id, enabled);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_product_schema(db: Any) -> None:
    with db.conn:
        db.conn.executescript(PRODUCT_SCHEMA)


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
        "SELECT tenant_id FROM tenant_memberships WHERE principal_id = ? AND active = 1 ORDER BY created_at LIMIT 1",
        (principal_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=409, detail="Account has no active workspace")
    return row["tenant_id"]


def _require_org(db: Any, principal_id: str, org_id: str) -> tuple[str, dict]:
    tenant_id = _tenant_for_user(db, principal_id)
    row = db.conn.execute(
        "SELECT * FROM organisations WHERE id = ? AND tenant_id = ?",
        (org_id, tenant_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organisation not found")
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


class ProviderConnectionRequest(BaseModel):
    provider: str = Field(min_length=2, max_length=80)
    connection_type: str = Field(default="api_key", max_length=40)
    api_key: Optional[str] = Field(default=None, max_length=4096)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/auth/wallet/challenge")
def wallet_challenge(request: WalletChallengeRequest) -> dict[str, Any]:
    address = request.address
    try:
        address = Account.to_checksum_address(address)
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
    db = create_database()
    try:
        ensure_product_schema(db)
        row = db.conn.execute(
            "SELECT * FROM wallet_challenges WHERE id = ? AND consumed = 0",
            (request.challenge_id,),
        ).fetchone()
        if not row or time.time() > float(row["expires_at"]):
            raise HTTPException(status_code=400, detail="Challenge is invalid or expired")

        try:
            recovered = Account.recover_message(
                encode_defunct(text=row["message"]),
                signature=request.signature,
            )
            recovered = Account.to_checksum_address(recovered)
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Wallet signature verification failed") from exc

        if recovered.lower() != row["address"].lower():
            raise HTTPException(status_code=401, detail="Signature does not match challenged wallet")

        db.conn.execute("UPDATE wallet_challenges SET consumed = 1 WHERE id = ?", (request.challenge_id,))

        identity = db.conn.execute(
            "SELECT i.user_id FROM product_identities i WHERE i.kind = 'wallet' AND lower(i.subject) = lower(?)",
            (recovered,),
        ).fetchone()

        if identity:
            user_id = identity["user_id"]
            user = db.conn.execute("SELECT * FROM product_users WHERE id = ?", (user_id,)).fetchone()
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
                (f"ident_{uuid.uuid4().hex}", user_id, "wallet", recovered, json.dumps({"chain_id": 4663}), _now()),
            )
            db.conn.execute(
                "INSERT INTO tenants (id,name,status,created_at) VALUES (?,?,?,?)",
                (tenant_id, f"{display_name}'s Workspace", "active", _now()),
            )
            IdentityRepository(db).save_principal(
                Principal(id=principal_id, name=display_name, active=True)
            )
            IdentityRepository(db).save_membership(
                Membership(principal_id=principal_id, tenant_id=tenant_id, role=MembershipRole.OWNER, active=True)
            )

        token = create_identity_token(principal_id, os.getenv("KALYX_POLICY_SECRET", "phase7-demo-policy-secret"), ttl_seconds=86400)
        db.conn.execute(
            "INSERT INTO product_sessions (id,user_id,principal_id,token_hash,expires_at,created_at) VALUES (?,?,?,?,?,?)",
            (
                f"sess_{uuid.uuid4().hex}", user_id, principal_id,
                hashlib.sha256(token.encode()).hexdigest(), time.time() + 86400, _now(),
            ),
        )
        db.conn.commit()
        return {
            "token": token,
            "user": {"id": user_id, "principal_id": principal_id},
            "workspace": {"tenant_id": tenant_id},
            "wallet": recovered,
        }
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
            "SELECT id, mission, state, created_at FROM organisations WHERE tenant_id = ? ORDER BY created_at DESC",
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
        if _tenant_for_user(db, principal_id) != tenant_id:
            raise HTTPException(status_code=403, detail="Workspace membership required")
        context = IdentityRepository(db).get_context(principal_id, tenant_id)
        if not context or not context.can_write():
            raise HTTPException(status_code=403, detail="Workspace write permission required")
        org_id = f"org_{uuid.uuid4().hex[:16]}"
        db.conn.execute(
            "INSERT INTO organisations (id,tenant_id,mission,treasury_balance,state,created_at) VALUES (?,?,?,?,?,?)",
            (org_id, tenant_id, request.mission.strip(), 0, "INITIALIZING", _now()),
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
            "UPDATE agent_credentials SET revoked = 1 WHERE organisation_id = ? AND agent_id = ? AND revoked = 0",
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
            "UPDATE agent_credentials SET revoked = 1 WHERE organisation_id = ? AND agent_id = ? AND revoked = 0",
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
            (policy_id, tenant_id, org_id, request.name, version, 1, json.dumps(config, sort_keys=True), _now(), _now()),
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
        return {
            "organisation": org,
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
