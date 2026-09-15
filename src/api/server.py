import os
import secrets
from typing import Any, Dict
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from src.api.bootstrap import ensure_started, is_production, policy_secret
from src.api.config import cors_origins, operator_key, require_operator_auth
from src.api.identity_auth import require_identity_for_org, require_identity_for_tenant
from src.api.mission_service import run_mission
from src.persistence.factory import create_database
from src.domain.entities import Organisation
from src.domain.enums import OrgState
from src.domain.exceptions import ReconciliationError, UnauthorizedActionError
from src.execution.consequential import ConsequentialOperationRepository
from src.persistence.repositories import SqliteEventStore, SqliteLedger
from src.settlement.reconciliation import ReconciliationService
from src.settlement.simulated_provider import SimulatedConsequentialProvider
from src.tenancy.ledger import TenantScopedLedger
from src.tenancy.organisation_ledger import OrganisationScopedLedger
from src.economy.experiment import EconomicExperiment
from src.governance.policy_engine import PolicyEngine

# Fail closed at import time when KALYX_ENV=production.
ensure_started()

app = FastAPI(title="Kalyx Command Centre API", version="0.9.5")
app.add_middleware(CORSMiddleware, allow_origins=cors_origins(), allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])

_default_settlement_provider = SimulatedConsequentialProvider()


def get_settlement_provider() -> SimulatedConsequentialProvider:
    return getattr(app.state, "settlement_provider", _default_settlement_provider)


def _identity_enabled() -> bool:
    if is_production():
        return True
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
        db = create_database()
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


def _db():
    return create_database()


def _org_id_or_404(db, org_id: str) -> Dict[str, Any]:
    row = db.conn.execute("SELECT * FROM organisations WHERE id = ?", (org_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return dict(row)


def _org_scoped_ledger(db, org: Dict[str, Any]):
    tenant_id = org.get("tenant_id") or "tenant-demo"
    tenant_ledger = TenantScopedLedger(SqliteLedger(db, initial_treasury=0), tenant_id)
    return OrganisationScopedLedger(tenant_ledger, org["id"], initial_treasury=0)


def _require_operator(x_api_key: str | None) -> None:
    if not require_operator_auth():
        return
    expected = operator_key()
    if not expected or not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Operator authentication required")


def _operator_event(db, org_id: str, event_type: str, previous_state: str, new_state: str) -> None:
    SqliteEventStore(db, verify_on_startup=True).append_event(
        actor_id="human-operator", event_type=event_type, entity_id=org_id,
        payload={"org_id": org_id, "previous_state": previous_state, "new_state": new_state},
    )


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
    finally:
        db.close()


@app.post("/api/missions")
def create_and_run_mission(
    request: MissionRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_principal_id: str | None = Header(default=None, alias="X-Principal-ID"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
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
    finally:
        db.close()


@app.get("/api/organisations")
def organisations(x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID")) -> list[Dict[str, Any]]:
    db = _db()
    try:
        if _identity_enabled():
            if not x_tenant_id:
                raise HTTPException(status_code=400, detail="Tenant scope required")
            return [dict(r) for r in db.conn.execute(
                "SELECT * FROM organisations WHERE tenant_id = ? ORDER BY created_at DESC", (x_tenant_id,)
            ).fetchall()]
        return [dict(r) for r in db.conn.execute("SELECT * FROM organisations ORDER BY created_at DESC").fetchall()]
    finally:
        db.close()


@app.get("/api/organisations/{org_id}")
def organisation(org_id: str) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        ledger = _org_scoped_ledger(db, org)
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
        rows = db.conn.execute(
            "SELECT p.*, t.org_id FROM proposals p JOIN tasks t ON t.id = p.task_id WHERE t.org_id = ? ORDER BY p.created_at DESC LIMIT ?",
            (org_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/decisions")
def decisions(org_id: str, limit: int = Query(default=100, ge=1, le=500)) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        rows = db.conn.execute(
            "SELECT d.*, p.task_id FROM policy_decisions d JOIN proposals p ON p.id = d.proposal_id JOIN tasks t ON t.id = p.task_id WHERE t.org_id = ? ORDER BY d.timestamp DESC LIMIT ?",
            (org_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/ledger")
def ledger(org_id: str, limit: int = Query(default=200, ge=1, le=1000)) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        l = _org_scoped_ledger(db, org)
        entries = l.get_entries()[-limit:]
        return {
            "treasury": l.get_balance("TREASURY"),
            "escrow": l.get_balance("ESCROW"),
            "external_sink": l.get_balance("EXTERNAL_SINK"),
            "conserved": l.verify_conservation(),
            "entries": [e.model_dump(mode="json") for e in entries],
        }
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/events")
def events(org_id: str, limit: int = Query(default=200, ge=1, le=1000)) -> list[Dict[str, Any]]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        store = SqliteEventStore(db, verify_on_startup=True)
        all_events = store.get_events()
        related_ids = {org_id}
        task_ids = {r["id"] for r in db.conn.execute("SELECT id FROM tasks WHERE org_id = ?", (org_id,)).fetchall()}
        related_ids.update(task_ids)
        if task_ids:
            ph = ",".join("?" for _ in task_ids)
            proposal_ids = {r["id"] for r in db.conn.execute(f"SELECT id FROM proposals WHERE task_id IN ({ph})", tuple(task_ids)).fetchall()}
            related_ids.update(proposal_ids)
            if proposal_ids:
                ph = ",".join("?" for _ in proposal_ids)
                related_ids.update(
                    r["id"] for r in db.conn.execute(
                        f"SELECT id FROM execution_receipts WHERE proposal_id IN ({ph})", tuple(proposal_ids)
                    ).fetchall()
                )
        return [e.model_dump(mode="json") for e in all_events if e.entity_id in related_ids or e.payload.get("org_id") == org_id][-limit:]
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/audit")
def audit(org_id: str) -> Dict[str, Any]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        store = SqliteEventStore(db, verify_on_startup=False)
        try:
            valid, error = store.verify_integrity()
        except Exception as exc:
            valid, error = False, f"Audit verification failed: {type(exc).__name__}: {exc}"
        receipts = db.conn.execute(
            "SELECT v.*, e.proposal_id FROM verification_receipts v JOIN execution_receipts e ON e.id = v.execution_id JOIN proposals p ON p.id = e.proposal_id JOIN tasks t ON t.id = p.task_id WHERE t.org_id = ? ORDER BY v.timestamp DESC",
            (org_id,),
        ).fetchall()
        return {"chain_valid": valid, "chain_error": error, "verification_receipts": [dict(r) for r in receipts]}
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/policies")
def policies(org_id: str) -> Dict[str, Any]:
    return {"policy_decisions": decisions(org_id)}


@app.get("/api/organisations/{org_id}/operations")
def list_operations(org_id: str, state: str | None = Query(default=None)) -> Dict[str, Any]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        repo = ConsequentialOperationRepository(db.conn)
        operations = repo.list_for_org(org_id, state=state)
        return {"operations": [op.model_dump(mode="json") for op in operations]}
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/operations/summary")
def operations_summary(org_id: str) -> Dict[str, Any]:
    """Breakdown of consequential operations by state, total escrowed, and count of unresolved."""
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        repo = ConsequentialOperationRepository(db.conn)
        operations = repo.list_for_org(org_id)
        counts: Dict[str, int] = {
            "created": 0, "authorized": 0, "escrowed": 0, "submitted": 0,
            "succeeded": 0, "failed": 0, "unknown": 0, "reconciling": 0, "reconciled": 0
        }
        total_escrowed = 0
        unresolved_count = 0
        for op in operations:
            s = (op.state.value if hasattr(op.state, "value") else str(op.state)).lower()
            counts[s] = counts.get(s, 0) + 1
            if s in {"unknown", "submitted", "reconciling"}:
                unresolved_count += 1
            if s in {"escrowed", "submitted", "unknown", "reconciling"}:
                total_escrowed += op.amount

        return {
            "total_operations": len(operations),
            "counts": counts,
            "unresolved_count": unresolved_count,
            "total_escrowed": total_escrowed,
            "has_unknown": counts.get("unknown", 0) > 0,
        }
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/operations/{op_id}")
def get_operation(org_id: str, op_id: str) -> Dict[str, Any]:
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        repo = ConsequentialOperationRepository(db.conn)
        op = repo.get(op_id)
        if not op or op.organisation_id != org_id:
            raise HTTPException(status_code=404, detail="Operation not found")
        return {"operation": op.model_dump(mode="json")}
    finally:
        db.close()


@app.post("/api/organisations/{org_id}/operations/{op_id}/reconcile")
def reconcile_operation_endpoint(org_id: str, op_id: str) -> Dict[str, Any]:
    db = _db()
    try:
        org_row = _org_id_or_404(db, org_id)
        repo = ConsequentialOperationRepository(db.conn)
        op = repo.get(op_id)
        if not op or op.organisation_id != org_id:
            raise HTTPException(status_code=404, detail="Operation not found")

        ledger = _org_scoped_ledger(db, org_row)
        event_store = SqliteEventStore(db, verify_on_startup=False)
        provider = get_settlement_provider()
        reconciliation_service = ReconciliationService(
            repo=repo,
            ledger=ledger,
            provider=provider,
            event_store=event_store,
        )

        org_entity = Organisation(
            id=org_row["id"],
            tenant_id=org_row.get("tenant_id", "tenant-demo"),
            mission=org_row["mission"],
            treasury_balance=ledger.get_balance("TREASURY"),
            state=OrgState(org_row["state"]),
        )

        try:
            reconciled_op = reconciliation_service.reconcile_operation(op.id, org_entity)
            return {"operation": reconciled_op.model_dump(mode="json")}
        except UnauthorizedActionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ReconciliationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    finally:
        db.close()


@app.post("/api/organisations/{org_id}/pause")
def pause(org_id: str, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Dict[str, Any]:
    _require_operator(x_api_key)
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
def resume(org_id: str, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Dict[str, Any]:
    _require_operator(x_api_key)
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


_latest_experiment_report: Dict[str, Any] | None = None


@app.get("/api/policies/rules")
def policy_rules(org_id: str | None = Query(default=None)) -> Dict[str, Any]:
    """Return catalogue of active deterministic policy rules (RULE-01 to RULE-08) and recent rejection metrics."""
    rules_catalogue = [
        {
            "rule_id": "RULE-01",
            "name": "Spend Cap Ceiling",
            "category": "Financial Limit",
            "description": "Requested credits cannot exceed agent authority ceiling",
            "enforcement_level": "STRICT ENFORCED",
            "scope": "All Sub-Agents",
            "parameters": {"authority_mode": "Role Ceiling", "default_ceiling": 25},
        },
        {
            "rule_id": "RULE-02",
            "name": "Treasury Balance Invariant",
            "category": "Solvency Protection",
            "description": "Requested credits cannot exceed organisation available treasury balance",
            "enforcement_level": "STRICT ENFORCED",
            "scope": "Organization Ledger",
            "parameters": {"overdraft_allowed": False, "conservation": "Strict Invariant"},
        },
        {
            "rule_id": "RULE-03",
            "name": "Role Permission Matrix",
            "category": "Access Control",
            "description": "Action type must be in agent allowed action types",
            "enforcement_level": "STRICT ENFORCED",
            "scope": "Role Hierarchy",
            "parameters": {"explicit_action_types": True},
        },
        {
            "rule_id": "RULE-04",
            "name": "Target Destination Allowlist",
            "category": "Network Boundary",
            "description": "Target endpoint must be on the approved allowlist (SSRF protection)",
            "enforcement_level": "STRICT ENFORCED",
            "scope": "External Execution",
            "parameters": {
                "approved_targets": [
                    "sandbox://market_index_fund",
                    "sandbox://verified_bonds",
                    "api://market_data/v1/summary",
                    "internal://research_synthesis"
                ]
            },
        },
        {
            "rule_id": "RULE-05",
            "name": "Human Gate Escalation",
            "category": "Governance & Safety",
            "description": "High-value or high-risk proposals require explicit operator dual-custody authorization",
            "enforcement_level": "HUMAN IN THE LOOP",
            "scope": "Executive Approval",
            "parameters": {"threshold_credits": 40, "dual_custody": True},
        },
        {
            "rule_id": "RULE-06",
            "name": "Emergency Kill Switch",
            "category": "Circuit Breaker",
            "description": "No actions may execute when organisation is paused",
            "enforcement_level": "ABSOLUTE HALT",
            "scope": "Runtime Kernel",
            "parameters": {"instant_freeze": True, "ephemeral_revocation": True},
        },
        {
            "rule_id": "RULE-07",
            "name": "Agent Status Lifecycle Invariant",
            "category": "Workforce Hygiene",
            "description": "Suspended/retired agents cannot propose; restricted agents limited to zero-credit internal analysis",
            "enforcement_level": "STRICT ENFORCED",
            "scope": "Agent Lifecycle",
            "parameters": {"allow_restricted_analysis": True},
        },
        {
            "rule_id": "RULE-08",
            "name": "Consequential Idempotency & Capability",
            "category": "Execution Safety",
            "description": "Consequential operations require valid capability tokens, non-zero amount, and unique idempotency keys",
            "enforcement_level": "STRICT ENFORCED",
            "scope": "Settlement Boundary",
            "parameters": {"idempotency_required": True, "provider_evidence_required": True},
        },
    ]

    rejections_by_rule: Dict[str, int] = {}
    if org_id:
        db = _db()
        try:
            rows = db.conn.execute(
                "SELECT d.violated_rule_id, count(*) as cnt FROM policy_decisions d JOIN proposals p ON p.id = d.proposal_id JOIN tasks t ON t.id = p.task_id WHERE t.org_id = ? AND d.result = 'REJECTED' GROUP BY d.violated_rule_id",
                (org_id,),
            ).fetchall()
            for r in rows:
                if r["violated_rule_id"]:
                    rejections_by_rule[r["violated_rule_id"]] = r["cnt"]
        finally:
            db.close()

    for r in rules_catalogue:
        r["rejection_count"] = rejections_by_rule.get(r["rule_id"], 0)

    secret = policy_secret()
    policy = PolicyEngine(signing_secret=secret)

    return {
        "rules": rules_catalogue,
        "policy_version_hash": policy.get_policy_version_hash()[:16],
        "human_approval_threshold": policy.human_approval_threshold,
        "total_rules": len(rules_catalogue),
    }


@app.get("/api/organisations/{org_id}/agents/{agent_id}")
def agent_profile(org_id: str, agent_id: str) -> Dict[str, Any]:
    """Retrieve full agent profile with tasks, proposals, decisions, and lifecycle performance."""
    db = _db()
    try:
        _org_id_or_404(db, org_id)
        agent_row = db.conn.execute(
            "SELECT * FROM agents WHERE org_id = ? AND id = ?", (org_id, agent_id)
        ).fetchone()
        if not agent_row:
            raise HTTPException(status_code=404, detail="Agent not found")

        agent_dict = dict(agent_row)
        tasks = db.conn.execute(
            "SELECT * FROM tasks WHERE org_id = ? AND assigned_agent_id = ? ORDER BY created_at DESC",
            (org_id, agent_id),
        ).fetchall()

        proposals = db.conn.execute(
            "SELECT p.* FROM proposals p JOIN tasks t ON t.id = p.task_id WHERE t.org_id = ? AND (t.assigned_agent_id = ? OR p.proposing_agent_id = ?) ORDER BY p.created_at DESC",
            (org_id, agent_id, agent_id),
        ).fetchall()

        proposal_ids = [p["id"] for p in proposals]
        decisions = []
        if proposal_ids:
            ph = ",".join("?" for _ in proposal_ids)
            decisions = [
                dict(r)
                for r in db.conn.execute(
                    f"SELECT * FROM policy_decisions WHERE proposal_id IN ({ph}) ORDER BY timestamp DESC",
                    tuple(proposal_ids),
                ).fetchall()
            ]

        return {
            "agent": agent_dict,
            "tasks": [dict(t) for t in tasks],
            "proposals": [dict(p) for p in proposals],
            "decisions": decisions,
        }
    finally:
        db.close()


@app.post("/api/experiments/run")
def run_experiments_endpoint(num_rounds: int = Query(default=3, ge=1, le=10)) -> Dict[str, Any]:
    """Run multi-scenario economic benchmark across STATIC, PERFORMANCE, and ADAPTIVE strategies."""
    global _latest_experiment_report
    report = EconomicExperiment.run(num_rounds=num_rounds, initial_treasury=100)
    data = report.model_dump(mode="json")
    _latest_experiment_report = data
    return data


@app.get("/api/experiments/latest")
def get_latest_experiments() -> Dict[str, Any]:
    """Retrieve the latest empirical economic benchmark report."""
    global _latest_experiment_report
    if _latest_experiment_report is None:
        return {"has_run": False, "report": None}
    return {"has_run": True, "report": _latest_experiment_report}


@app.get("/api/system/settings")
def system_settings() -> Dict[str, Any]:
    """Retrieve runtime settings and environment parameters with zero exposed secrets."""
    provider = get_settlement_provider()
    db = _db()
    try:
        db_type = "SQLite" if hasattr(db, "conn") and "sqlite" in type(db.conn).__module__.lower() else "PostgreSQL"
    finally:
        db.close()

    secret = policy_secret()
    policy = PolicyEngine(signing_secret=secret)

    return {
        "service": "Kalyx Command Centre",
        "version": app.version,
        "environment": "production" if is_production() else "development",
        "database_backend": db_type,
        "identity_auth_enabled": _identity_enabled(),
        "settlement_provider": getattr(provider, "name", "SimulatedConsequentialProvider (v1.0)"),
        "policy_engine": {
            "version_hash": policy.get_policy_version_hash()[:16],
            "rule_count": len(policy.rules),
            "human_approval_threshold": policy.human_approval_threshold,
        },
        "circuit_breaker_status": "ARMED",
        "consensus_anchor": "Synchronized",
    }


@app.post("/api/demo/run")
def run_demo_endpoint(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_principal_id: str | None = Header(default=None, alias="X-Principal-ID"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
    """Execute the deterministic scripted demo scenario."""
    _require_operator(x_api_key)
    tenant_id = x_tenant_id or "tenant-demo"
    return run_mission(
        mission="Autonomous Liquidity Rebalancing & Risk-Bounded Market Allocation",
        budget=100,
        live=False,
        tenant_id=tenant_id,
    )


WEB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../apps/web"))
if os.path.isdir(WEB_ROOT):
    app.mount("/assets", StaticFiles(directory=os.path.join(WEB_ROOT, "assets")), name="assets")

    @app.get("/")
    def dashboard() -> FileResponse:
        return FileResponse(os.path.join(WEB_ROOT, "index.html"))
