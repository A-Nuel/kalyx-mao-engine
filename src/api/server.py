import os
import secrets
from typing import Any, Dict
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from src.api.config import cors_origins, operator_key, require_operator_auth
from src.api.identity_auth import require_identity_for_org, require_identity_for_tenant
from src.api.mission_service import run_mission
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore, SqliteLedger
from src.tenancy.ledger import TenantScopedLedger
from src.tenancy.organisation_ledger import OrganisationScopedLedger

app = FastAPI(title="Kalyx Command Centre API", version="0.8.0")
app.add_middleware(CORSMiddleware, allow_origins=cors_origins(), allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])


def _identity_enabled() -> bool:
    return os.getenv("KALYX_IDENTITY_AUTH", "false").strip().lower() in {"1", "true", "yes", "on"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


class IdentityAuthorizationMiddleware(BaseHTTPMiddleware):
    """Authenticate resource scope before an organisation handler can read it."""
    async def dispatch(self, request, call_next):
        if not _identity_enabled() or not request.url.path.startswith("/api/organisations"):
            return await call_next(request)
        db = Database(os.getenv("KALYX_DB", "data/kalyx.db"))
        try:
            parts = [p for p in request.url.path.split("/") if p]
            principal = request.headers.get("X-Principal-ID")
            tenant = request.headers.get("X-Tenant-ID")
            try:
                if len(parts) == 2 and parts[1] == "organisations":
                    if not tenant:
                        return JSONResponse({"detail": "Tenant scope required"}, status_code=400)
                    require_identity_for_tenant(db, tenant, principal, tenant)
                elif len(parts) >= 3:
                    require_identity_for_org(db, parts[2], principal, tenant)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            return await call_next(request)
        finally:
            db.close()


app.add_middleware(IdentityAuthorizationMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


def _db() -> Database:
    return Database(os.getenv("KALYX_DB", "data/kalyx.db"))


def _org_id_or_404(db: Database, org_id: str) -> Dict[str, Any]:
    row = db.conn.execute("SELECT * FROM organisations WHERE id = ?", (org_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return dict(row)


def _org_scoped_ledger(db: Database, org: Dict[str, Any]):
    """Build an organisation-scoped ledger from persisted org ownership."""
    tenant_id = org.get("tenant_id") or "tenant-demo"
    tenant_ledger = TenantScopedLedger(SqliteLedger(db, initial_treasury=0), tenant_id)
    return OrganisationScopedLedger(tenant_ledger, org["id"], initial_treasury=0)


def _require_operator(x_api_key: str | None) -> None:
    if not require_operator_auth():
        return
    expected = operator_key()
    if not expected or not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Operator authentication required")


def _operator_event(db: Database, org_id: str, event_type: str, previous_state: str, new_state: str) -> None:
    SqliteEventStore(db, verify_on_startup=True).append_event(actor_id="human-operator", event_type=event_type, entity_id=org_id, payload={"org_id": org_id, "previous_state": previous_state, "new_state": new_state})


class MissionRequest(BaseModel):
    mission: str = Field(min_length=3, max_length=2_000)
    budget: int = Field(default=100, ge=1, le=10_000)
    live: bool = False


@app.get("/api/health")
def health() -> Dict[str, Any]:
    db = _db()
    try:
        db.conn.execute("SELECT 1")
        return {"status": "ok", "service": "kalyx-command-centre", "version": app.version}
    finally: db.close()


@app.post("/api/missions")
def create_and_run_mission(request: MissionRequest, x_api_key: str | None = Header(default=None, alias="X-API-Key"), x_principal_id: str | None = Header(default=None, alias="X-Principal-ID"), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID")) -> Dict[str, Any]:
    _require_operator(x_api_key)
    db = _db()
    try:
        if _identity_enabled():
            if not x_tenant_id:
                raise HTTPException(status_code=400, detail="Tenant scope required")
            require_identity_for_tenant(db, x_tenant_id, x_principal_id, x_tenant_id)
        try:
            return run_mission(request.mission, request.budget, live=request.live, tenant_id=x_tenant_id or "tenant-demo")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Mission execution failed: {type(exc).__name__}: {exc}") from exc
    finally: db.close()


@app.get("/api/organisations")
def organisations(x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID")) -> list[Dict[str, Any]]:
    db = _db()
    try:
        if _identity_enabled():
            if not x_tenant_id:
                raise HTTPException(status_code=400, detail="Tenant scope required")
            # Middleware already authenticated this selector; query is still explicitly scoped.
            return [dict(r) for r in db.conn.execute("SELECT * FROM organisations WHERE tenant_id = ? ORDER BY created_at DESC", (x_tenant_id,)).fetchall()]
        return [dict(r) for r in db.conn.execute("SELECT * FROM organisations ORDER BY created_at DESC").fetchall()]
    finally: db.close()


@app.get("/api/organisations/{org_id}")
def organisation(org_id: str) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        ledger = _org_scoped_ledger(db, org)
        agents = db.conn.execute("SELECT * FROM agents WHERE org_id = ? ORDER BY reputation_score DESC", (org_id,)).fetchall()
        tasks = db.conn.execute("SELECT * FROM tasks WHERE org_id = ? ORDER BY created_at DESC", (org_id,)).fetchall()
        return {"organisation": org, "treasury": ledger.get_balance("TREASURY"), "agents": [dict(a) for a in agents], "tasks": [dict(t) for t in tasks]}
    finally: db.close()


@app.get("/api/organisations/{org_id}/agents")
def agents(org_id: str) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id); return [dict(r) for r in db.conn.execute("SELECT * FROM agents WHERE org_id = ? ORDER BY reputation_score DESC", (org_id,)).fetchall()]
    finally: db.close()


@app.get("/api/organisations/{org_id}/tasks")
def tasks(org_id: str) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id); return [dict(r) for r in db.conn.execute("SELECT * FROM tasks WHERE org_id = ? ORDER BY created_at DESC", (org_id,)).fetchall()]
    finally: db.close()


@app.get("/api/organisations/{org_id}/proposals")
def proposals(org_id: str, limit: int = Query(default=100, ge=1, le=500)) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id); rows = db.conn.execute("SELECT p.*, t.org_id FROM proposals p JOIN tasks t ON t.id = p.task_id WHERE t.org_id = ? ORDER BY p.created_at DESC LIMIT ?", (org_id, limit)).fetchall(); return [dict(r) for r in rows]
    finally: db.close()


@app.get("/api/organisations/{org_id}/decisions")
def decisions(org_id: str, limit: int = Query(default=100, ge=1, le=500)) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id); rows = db.conn.execute("SELECT d.*, p.task_id FROM policy_decisions d JOIN proposals p ON p.id = d.proposal_id JOIN tasks t ON t.id = p.task_id WHERE t.org_id = ? ORDER BY d.timestamp DESC LIMIT ?", (org_id, limit)).fetchall(); return [dict(r) for r in rows]
    finally: db.close()


@app.get("/api/organisations/{org_id}/ledger")
def ledger(org_id: str, limit: int = Query(default=200, ge=1, le=1000)) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        l = _org_scoped_ledger(db, org)
        entries = l.get_entries()[-limit:]
        return {"treasury": l.get_balance("TREASURY"), "escrow": l.get_balance("ESCROW"), "external_sink": l.get_balance("EXTERNAL_SINK"), "conserved": l.verify_conservation(), "entries": [e.model_dump(mode="json") for e in entries]}
    finally: db.close()


@app.get("/api/organisations/{org_id}/events")
def events(org_id: str, limit: int = Query(default=200, ge=1, le=1000)) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id); store = SqliteEventStore(db, verify_on_startup=True); all_events = store.get_events(); related_ids = {org_id}
        task_ids = {r["id"] for r in db.conn.execute("SELECT id FROM tasks WHERE org_id = ?", (org_id,)).fetchall()}; related_ids.update(task_ids)
        if task_ids:
            ph = ",".join("?" for _ in task_ids); proposal_ids = {r["id"] for r in db.conn.execute(f"SELECT id FROM proposals WHERE task_id IN ({ph})", tuple(task_ids)).fetchall()}; related_ids.update(proposal_ids)
            if proposal_ids:
                ph = ",".join("?" for _ in proposal_ids); related_ids.update(r["id"] for r in db.conn.execute(f"SELECT id FROM execution_receipts WHERE proposal_id IN ({ph})", tuple(proposal_ids)).fetchall())
        return [e.model_dump(mode="json") for e in all_events if e.entity_id in related_ids or e.payload.get("org_id") == org_id][-limit:]
    finally: db.close()


@app.get("/api/organisations/{org_id}/audit")
def audit(org_id: str) -> Dict[str, Any]:
    db = _db()
    try:
        _org_id_or_404(db, org_id); store = SqliteEventStore(db, verify_on_startup=False)
        try: valid, error = store.verify_integrity()
        except Exception as exc: valid, error = False, f"Audit verification failed: {type(exc).__name__}: {exc}"
        receipts = db.conn.execute("SELECT v.*, e.proposal_id FROM verification_receipts v JOIN execution_receipts e ON e.id = v.execution_id JOIN proposals p ON p.id = e.proposal_id JOIN tasks t ON t.id = p.task_id WHERE t.org_id = ? ORDER BY v.timestamp DESC", (org_id,)).fetchall()
        return {"chain_valid": valid, "chain_error": error, "verification_receipts": [dict(r) for r in receipts]}
    finally: db.close()


@app.get("/api/organisations/{org_id}/policies")
def policies(org_id: str) -> Dict[str, Any]: return {"policy_decisions": decisions(org_id)}


@app.post("/api/organisations/{org_id}/pause")
def pause(org_id: str, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Dict[str, Any]:
    _require_operator(x_api_key); db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        if org["state"] == "PAUSED": return {"id": org_id, "state": "PAUSED", "changed": False}
        _operator_event(db, org_id, "ORG_PAUSED", org["state"], "PAUSED"); db.conn.execute("UPDATE organisations SET state = ? WHERE id = ?", ("PAUSED", org_id)); db.conn.commit(); return {"id": org_id, "state": "PAUSED", "previous_state": org["state"], "changed": True}
    finally: db.close()


@app.post("/api/organisations/{org_id}/resume")
def resume(org_id: str, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Dict[str, Any]:
    _require_operator(x_api_key); db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        if org["state"] != "PAUSED": raise HTTPException(status_code=409, detail="Organisation is not paused")
        _operator_event(db, org_id, "ORG_RESUMED", "PAUSED", "EXECUTING"); db.conn.execute("UPDATE organisations SET state = ? WHERE id = ?", ("EXECUTING", org_id)); db.conn.commit(); return {"id": org_id, "state": "EXECUTING", "changed": True}
    finally: db.close()


WEB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../apps/web"))
if os.path.isdir(WEB_ROOT):
    app.mount("/assets", StaticFiles(directory=os.path.join(WEB_ROOT, "assets")), name="assets")
    @app.get("/")
    def dashboard() -> FileResponse: return FileResponse(os.path.join(WEB_ROOT, "index.html"))
