import os
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore, SqliteLedger

app = FastAPI(title="Kalyx Command Centre API", version="0.5.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])


def _db() -> Database:
    return Database(os.getenv("KALYX_DB", "data/kalyx.db"))


def _org_id_or_404(db: Database, org_id: str) -> Dict[str, Any]:
    row = db.conn.execute("SELECT * FROM organisations WHERE id = ?", (org_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return dict(row)


def _operator_event(db: Database, org_id: str, event_type: str, previous_state: str, new_state: str) -> None:
    SqliteEventStore(db, verify_on_startup=True).append_event(
        actor_id="human-operator", event_type=event_type, entity_id=org_id,
        payload={"org_id": org_id, "previous_state": previous_state, "new_state": new_state},
    )


@app.get("/api/health")
def health() -> Dict[str, Any]:
    db = _db()
    try:
        db.conn.execute("SELECT 1")
        return {"status": "ok", "service": "kalyx-command-centre"}
    finally:
        db.close()


@app.get("/api/organisations")
def organisations() -> list[Dict[str, Any]]:
    db = _db()
    try:
        return [dict(r) for r in db.conn.execute("SELECT * FROM organisations ORDER BY created_at DESC").fetchall()]
    finally:
        db.close()


@app.get("/api/organisations/{org_id}")
def organisation(org_id: str) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        ledger = SqliteLedger(db, initial_treasury=0)
        agents = db.conn.execute("SELECT * FROM agents WHERE org_id = ? ORDER BY reputation_score DESC", (org_id,)).fetchall()
        tasks = db.conn.execute("SELECT * FROM tasks WHERE org_id = ? ORDER BY created_at DESC", (org_id,)).fetchall()
        return {"organisation": org, "treasury": ledger.get_balance("TREASURY"), "agents": [dict(a) for a in agents], "tasks": [dict(t) for t in tasks]}
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/agents")
def agents(org_id: str) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        return [dict(r) for r in db.conn.execute("SELECT * FROM agents WHERE org_id = ? ORDER BY reputation_score DESC", (org_id,)).fetchall()]
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/tasks")
def tasks(org_id: str) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        return [dict(r) for r in db.conn.execute("SELECT * FROM tasks WHERE org_id = ? ORDER BY created_at DESC", (org_id,)).fetchall()]
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/proposals")
def proposals(org_id: str, limit: int = Query(default=100, ge=1, le=500)) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        rows = db.conn.execute("SELECT p.*, t.org_id FROM proposals p JOIN tasks t ON t.id = p.task_id WHERE t.org_id = ? ORDER BY p.created_at DESC LIMIT ?", (org_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/decisions")
def decisions(org_id: str, limit: int = Query(default=100, ge=1, le=500)) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        rows = db.conn.execute("SELECT d.*, p.task_id FROM policy_decisions d JOIN proposals p ON p.id = d.proposal_id JOIN tasks t ON t.id = p.task_id WHERE t.org_id = ? ORDER BY d.timestamp DESC LIMIT ?", (org_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/ledger")
def ledger(org_id: str, limit: int = Query(default=200, ge=1, le=1000)) -> Dict[str, Any]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        l = SqliteLedger(db, initial_treasury=0)
        entries = l.get_entries()[-limit:]
        return {"treasury": l.get_balance("TREASURY"), "escrow": l.get_balance("ESCROW"), "external_sink": l.get_balance("EXTERNAL_SINK"), "conserved": l.verify_conservation(), "entries": [e.model_dump(mode="json") for e in entries]}
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/events")
def events(org_id: str, limit: int = Query(default=200, ge=1, le=1000)) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        store = SqliteEventStore(db, verify_on_startup=True)
        result = [e.model_dump(mode="json") for e in store.get_events() if e.entity_id == org_id or e.payload.get("org_id") == org_id]
        return result[-limit:]
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/audit")
def audit(org_id: str) -> Dict[str, Any]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        store = SqliteEventStore(db, verify_on_startup=True)
        valid, error = store.verify_integrity()
        receipts = db.conn.execute("SELECT v.*, e.proposal_id FROM verification_receipts v JOIN execution_receipts e ON e.id = v.execution_id JOIN proposals p ON p.id = e.proposal_id JOIN tasks t ON t.id = p.task_id WHERE t.org_id = ? ORDER BY v.timestamp DESC", (org_id,)).fetchall()
        return {"chain_valid": valid, "chain_error": error, "verification_receipts": [dict(r) for r in receipts]}
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/policies")
def policies(org_id: str) -> Dict[str, Any]:
    return {"policy_decisions": decisions(org_id)}


@app.post("/api/organisations/{org_id}/pause")
def pause(org_id: str) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        if org["state"] == "PAUSED":
            return {"id": org_id, "state": "PAUSED", "changed": False}
        _operator_event(db, org_id, "ORG_PAUSED", org["state"], "PAUSED")
        db.conn.execute("UPDATE organisations SET state = ? WHERE id = ?", ("PAUSED", org_id))
        db.conn.commit()
        return {"id": org_id, "state": "PAUSED", "previous_state": org["state"], "changed": True}
    finally:
        db.close()


@app.post("/api/organisations/{org_id}/resume")
def resume(org_id: str) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        if org["state"] != "PAUSED":
            raise HTTPException(status_code=409, detail="Organisation is not paused")
        _operator_event(db, org_id, "ORG_RESUMED", "PAUSED", "EXECUTING")
        db.conn.execute("UPDATE organisations SET state = ? WHERE id = ?", ("EXECUTING", org_id))
        db.conn.commit()
        return {"id": org_id, "state": "EXECUTING", "changed": True}
    finally:
        db.close()


WEB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../apps/web"))
if os.path.isdir(WEB_ROOT):
    app.mount("/assets", StaticFiles(directory=os.path.join(WEB_ROOT, "assets")), name="assets")

    @app.get("/")
    def dashboard() -> FileResponse:
        return FileResponse(os.path.join(WEB_ROOT, "index.html"))
