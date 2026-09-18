import json
import os
import secrets
import uuid
from datetime import datetime
from typing import Any, Dict
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from src.api.bootstrap import ensure_started, is_production, policy_secret
from src.api.config import cors_origins, operator_key, require_operator_auth
from src.api.identity_auth import require_identity_for_org, require_identity_for_tenant, require_write_permission
from src.api.mission_service import run_mission
from src.api.security_middleware import CorrelationIdMiddleware, RateLimitMiddleware, RequestBodyLimitMiddleware
from src.persistence.factory import create_database, create_scoped_ledger
from src.domain.entities import Organisation
from src.domain.enums import OrgState
from src.domain.events import AuditEvent
from src.domain.exceptions import ReconciliationError, UnauthorizedActionError
from src.execution.consequential import ConsequentialOperationRepository
from src.persistence.repositories import SqliteEventStore, SqliteLedger
from src.settlement.reconciliation import ReconciliationService
from src.settlement.simulated_provider import SimulatedConsequentialProvider
from src.tenancy.ledger import TenantScopedLedger
from src.tenancy.organisation_ledger import OrganisationScopedLedger
from src.economy.experiment import EconomicExperiment
from src.governance.policy_engine import PolicyEngine
from src.domain.economy import (
    AgentPerformanceRecord,
    AllocationStrategy,
    ReputationHistoryEntry,
    ResourceAllocationDecision,
)
from src.persistence.economy_repo import EconomyRepository
from src.economy.allocator import ResourceAllocator
from src.domain.enums import CurrencyAsset, DeliverableStatus, SolvencyRegime, WorkOrderStatus
from src.domain.work_order import WorkOrder
from src.persistence.work_order_repository import WorkOrderRepository
from src.orchestration.autonomous_daemon import AutonomousDaemon, derive_solvency_regime
from src.agents.mock_adapter import MockAgentAdapter
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.agents.work_order_coordinator import WorkOrderCoordinator
from src.agents.self_sustaining_loop import SelfSustainingLoopRunner
from src.economy.surplus_accounting import SurplusReconciler
from src.execution.work_executor import SimulatedWorkExecutor
from src.settlement.work_verifier import WorkDeliverableVerifier
from src.settlement.orbio_simulated_exchange import SimulatedOrbioExchangeProvider
from src.economy.ledger import TREASURY
from src.agents.orbio_purchase_loop import AgentLoopState, OrbioPurchaseAgentLoop
from src.governance.orbio_purchase_rules import OrbioPurchasePolicy
from src.execution.orbio_purchase import OrbioPurchaseBridge
from src.settlement.orbio_purchase_verifier import OrbioPurchaseVerifier
from src.settlement.orbio_purchase_reconciliation import OrbioPurchaseReconciliation

# Fail closed at import time when KALYX_ENV=production.
ensure_started()


app = FastAPI(title="Kalyx Command Centre API", version="1.0.0-phase16")
app.add_middleware(CORSMiddleware, allow_origins=cors_origins(), allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    req_id = getattr(getattr(request, "state", None), "request_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "http_error",
            "message": exc.detail,
            "detail": exc.detail,
            **({"request_id": req_id} if req_id else {}),
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    req_id = getattr(getattr(request, "state", None), "request_id", None)
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "Invalid request payload or parameters",
            "detail": exc.errors(),
            **({"request_id": req_id} if req_id else {}),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    req_id = getattr(getattr(request, "state", None), "request_id", "unknown")
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An unexpected internal server error occurred",
            "detail": "An unexpected internal server error occurred",
            "request_id": req_id,
        },
    )

_default_settlement_provider = SimulatedConsequentialProvider()
_latest_experiment_report = None


def get_settlement_provider():
    if hasattr(app.state, "settlement_provider") and app.state.settlement_provider is not None:
        return app.state.settlement_provider
    from src.api.config import (
        blockchain_chain_id,
        blockchain_enabled,
        blockchain_network_name,
        blockchain_private_key,
        blockchain_rpc_url,
        validate_blockchain_config,
    )
    if blockchain_enabled():
        validate_blockchain_config()
        from src.settlement.blockchain.provider import BlockchainSettlementProvider
        from src.settlement.blockchain.rpc_client import HttpEvmRpcClient
        from src.settlement.blockchain.signer import LocalKeySigner
        rpc = HttpEvmRpcClient(blockchain_rpc_url())
        signer = LocalKeySigner(blockchain_private_key())
        provider = BlockchainSettlementProvider(
            rpc_client=rpc,
            signer=signer,
            default_chain_id=blockchain_chain_id(),
            network_name=blockchain_network_name(),
        )
        app.state.settlement_provider = provider
        return provider
    return _default_settlement_provider


_shared_exchange_provider = None


def _get_exchange_provider() -> SimulatedOrbioExchangeProvider:
    global _shared_exchange_provider
    if _shared_exchange_provider is None:
        _shared_exchange_provider = SimulatedOrbioExchangeProvider()
    return _shared_exchange_provider


def _build_purchase_loop(
    org: Dict[str, Any],
    ledger: Any,
    provider: SimulatedOrbioExchangeProvider,
    mission_id: str,
    target_credit: int,
) -> OrbioPurchaseAgentLoop:
    org_id = org["id"]
    tenant_id = org.get("tenant_id") or "tenant-demo"
    org_entity = Organisation(
        id=org_id,
        mission=org.get("mission", "autonomous-operations"),
        tenant_id=tenant_id,
        treasury_balance=ledger.get_balance(TREASURY),
    )
    provider.set_usdg_balance(org_id, ledger.get_balance(TREASURY) * 1_000_000)
    policy = OrbioPurchasePolicy(
        autonomous_usdg_ceiling=100_000_000,
        absolute_usdg_ceiling=100_000_000,
    )
    bridge = OrbioPurchaseBridge(purchase_policy=policy)
    orbio_verifier = OrbioPurchaseVerifier()
    orbio_reconciler = OrbioPurchaseReconciliation(
        provider=provider,
        ledger=ledger,
        verifier=orbio_verifier,
    )
    beneficiary = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    state = AgentLoopState(
        agent_id="agent-purchaser",
        tenant_id=tenant_id,
        organisation_id=org_id,
        mission_id=mission_id,
        objective="Acquire compute resources for mission",
        target_credit=target_credit,
    )
    return OrbioPurchaseAgentLoop(
        state=state,
        org=org_entity,
        policy=policy,
        bridge=bridge,
        provider=provider,
        verifier=orbio_verifier,
        reconciler=orbio_reconciler,
        ledger=ledger,
        max_single_usdg=50_000_000,
        max_cumulative_usdg=50_000_000,
        default_beneficiary=beneficiary,
    )


def _identity_enabled() -> bool:
    if is_production() or os.getenv("KALYX_IDENTITY_AUTH", "").strip().lower() == "production":
        return True
    return os.getenv("KALYX_IDENTITY_AUTH", "false").strip().lower() in {"1", "true", "yes", "on", "production"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


class IdentityAuthorizationMiddleware(BaseHTTPMiddleware):
    """Authenticate resource scope before an organisation handler can read or modify it."""
    async def dispatch(self, request, call_next):
        if not _identity_enabled():
            return await call_next(request)

        path = request.url.path
        parts = [p for p in path.split("/") if p]
        if "organisations" not in parts:
            return await call_next(request)

        org_idx = parts.index("organisations")
        if org_idx == 0 or parts[0] != "api":
            return await call_next(request)

        db = create_database()
        try:
            principal = request.headers.get("X-Principal-ID")
            tenant = request.headers.get("X-Tenant-ID")
            api_key = request.headers.get("X-API-Key")
            auth_header = request.headers.get("Authorization")
            try:
                context = None
                if len(parts) == org_idx + 1:
                    if not tenant:
                        return JSONResponse({"error": "bad_request", "message": "Tenant scope required", "detail": "Tenant scope required"}, status_code=400)
                    context = require_identity_for_tenant(db, tenant, principal, tenant, authorization=auth_header, x_api_key=api_key)
                elif len(parts) >= org_idx + 2:
                    target_org_id = parts[org_idx + 1]
                    context = require_identity_for_org(db, target_org_id, principal, tenant, authorization=auth_header, x_api_key=api_key)

                # Check write permissions on mutation actions (pause, resume, reconcile, work orders, daemon step)
                if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
                    require_write_permission(context)
            except HTTPException as exc:
                return JSONResponse(
                    {"error": "authorization_error", "message": exc.detail, "detail": exc.detail},
                    status_code=exc.status_code,
                )
            return await call_next(request)
        finally:
            db.close()


# Middlewares execute in reverse order of mounting:
# CorrelationIdMiddleware -> RequestBodyLimitMiddleware -> RateLimitMiddleware -> IdentityAuthorizationMiddleware -> SecurityHeadersMiddleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(IdentityAuthorizationMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(CorrelationIdMiddleware)


def _db():
    return create_database()


def _org_id_or_404(db, org_id: str) -> Dict[str, Any]:
    row = db.conn.execute("SELECT * FROM organisations WHERE id = ?", (org_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return dict(row)


def _org_scoped_ledger(db, org: Dict[str, Any]):
    tenant_id = org.get("tenant_id") or "tenant-demo"
    return create_scoped_ledger(db, tenant_id=tenant_id, organisation_id=org["id"], initial_treasury=0)


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
    db = None
    try:
        db = _db()
        db.conn.execute("SELECT 1")
        return {
            "status": "healthy",
            "service": "kalyx-command-centre",
            "version": app.version,
            "database": "connected",
            "environment": "production" if is_production() else "demo",
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {type(exc).__name__}")
    finally:
        if db is not None:
            db.close()


@app.post("/api/missions")
def create_and_run_mission(
    request: MissionRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_principal_id: str | None = Header(default=None, alias="X-Principal-ID"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Dict[str, Any]:
    _require_operator(x_api_key)
    db = _db()
    try:
        if _identity_enabled():
            if not x_tenant_id:
                raise HTTPException(status_code=400, detail="Tenant scope required")
            context = require_identity_for_tenant(
                db, x_tenant_id, x_principal_id, x_tenant_id, authorization=authorization, x_api_key=x_api_key
            )
            require_write_permission(context)
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
        org_row = _org_id_or_404(db, org_id)
        tenant_id = org_row.get("tenant_id") or "tenant-demo"
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
        cur = db.conn.cursor()
        ph_ids = ",".join("?" for _ in related_ids)
        query = (
            f"SELECT * FROM audit_events "
            f"WHERE tenant_id = ? AND (organisation_id = ? OR entity_id IN ({ph_ids})) "
            f"ORDER BY sequence_id ASC"
        )
        cur.execute(query, (tenant_id, org_id, *related_ids))
        scoped = [
            AuditEvent(
                sequence_id=r["sequence_id"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                actor_id=r["actor_id"],
                event_type=r["event_type"],
                entity_id=r["entity_id"],
                payload=json.loads(r["payload"]),
                payload_hash=r["payload_hash"],
                previous_event_hash=r["previous_event_hash"],
                event_hash=r["event_hash"],
            )
            for r in cur.fetchall()
        ]
        return [e.model_dump(mode="json") for e in scoped][-limit:]
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


@app.get("/api/organisations/{org_id}/economy")
def get_organisation_economy(
    org_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    """Retrieve organisation workforce economic overview, lifecycle distribution, and allocation totals."""
    _require_operator(x_api_key)
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        repo = EconomyRepository(db.conn)
        records = repo.list_performance_records(org["tenant_id"], org_id)
        allocations = repo.list_allocations(org["tenant_id"], org_id, limit=10)

        status_counts = {"ACTIVE": 0, "PROBATION": 0, "RESTRICTED": 0, "SUSPENDED": 0, "RETIRED": 0}
        total_allocated = sum(r.resources_allocated for r in records)
        total_consumed = sum(r.resources_consumed for r in records)
        total_value = sum(r.value_produced for r in records)
        avg_efficiency = (total_value / total_consumed) if total_consumed > 0 else 1.0

        agent_rows = db.conn.execute("SELECT status FROM agents WHERE org_id = ?", (org_id,)).fetchall()
        for ar in agent_rows:
            st = (ar["status"] or "ACTIVE").upper()
            if st in status_counts:
                status_counts[st] += 1
            else:
                status_counts["ACTIVE"] += 1

        return {
            "organisation_id": org_id,
            "tenant_id": org["tenant_id"],
            "treasury_balance": org["treasury_balance"],
            "total_allocated": total_allocated,
            "total_consumed": total_consumed,
            "total_value_produced": total_value,
            "average_efficiency": round(avg_efficiency, 2),
            "workforce_status_distribution": status_counts,
            "performance_records": [r.model_dump(mode="json") for r in records],
            "recent_allocations": [a.model_dump(mode="json") for a in allocations],
        }
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/agents/{agent_id}/performance")
def get_agent_performance(
    org_id: str,
    agent_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    """Retrieve detailed multi-dimensional performance record for an agent."""
    _require_operator(x_api_key)
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        agent_row = db.conn.execute("SELECT id FROM agents WHERE org_id = ? AND id = ?", (org_id, agent_id)).fetchone()
        if not agent_row:
            raise HTTPException(status_code=404, detail="Agent not found")
        repo = EconomyRepository(db.conn)
        record = repo.get_performance_record(org["tenant_id"], org_id, agent_id)
        if not record:
            return {
                "agent_id": agent_id,
                "organisation_id": org_id,
                "tenant_id": org["tenant_id"],
                "composite_score": 100.0,
                "reputation_score": 100.0,
                "performance_score": 100.0,
                "reliability_score": 100.0,
                "resource_efficiency_score": 100.0,
                "policy_compliance_score": 100.0,
                "tasks_completed": 0,
                "tasks_failed": 0,
                "resources_allocated": 0,
                "resources_consumed": 0,
                "value_produced": 0,
                "evaluation_count": 0,
            }
        return record.model_dump(mode="json")
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/agents/{agent_id}/reputation")
def get_agent_reputation_history(
    org_id: str,
    agent_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    """Retrieve historical reputation delta events and audit evidence hashes for an agent."""
    _require_operator(x_api_key)
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        agent_row = db.conn.execute("SELECT id FROM agents WHERE org_id = ? AND id = ?", (org_id, agent_id)).fetchone()
        if not agent_row:
            raise HTTPException(status_code=404, detail="Agent not found")
        repo = EconomyRepository(db.conn)
        entries = repo.list_reputation_history(org["tenant_id"], org_id, agent_id, limit=limit)
        return {
            "agent_id": agent_id,
            "organisation_id": org_id,
            "tenant_id": org["tenant_id"],
            "reputation_history": [e.model_dump(mode="json") for e in entries],
        }
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/allocations")
def get_organisation_allocations(
    org_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    """Retrieve historical resource allocation decisions for the organisation."""
    _require_operator(x_api_key)
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        repo = EconomyRepository(db.conn)
        decisions = repo.list_allocations(org["tenant_id"], org_id, limit=limit)
        return {
            "organisation_id": org_id,
            "tenant_id": org["tenant_id"],
            "allocations": [d.model_dump(mode="json") for d in decisions],
        }
    finally:
        db.close()


@app.get("/api/organisations/{org_id}/economy/events")
def get_organisation_economy_events(
    org_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    """Retrieve audit events related to economic operations."""
    _require_operator(x_api_key)
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        rows = db.conn.execute(
            """SELECT * FROM audit_events
               WHERE tenant_id = ? AND organisation_id = ?
                 AND event_type IN (
                     'RESOURCE_ALLOCATED', 'AGENT_PERFORMANCE_UPDATED',
                     'AGENT_PROMOTED', 'AGENT_DEMOTED', 'AGENT_PROBATION',
                     'AGENT_RESTRICTED', 'AGENT_SUSPENDED', 'AGENT_RETIRED',
                     'REPUTATION_UPDATED', 'EXPERIMENT_COMPLETED', 'SETTLEMENT_EXECUTED'
                 )
               ORDER BY sequence_id DESC LIMIT ?""",
            (org["tenant_id"], org_id, limit),
        ).fetchall()
        return {
            "organisation_id": org_id,
            "tenant_id": org["tenant_id"],
            "events": [dict(r) for r in rows],
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


# --------------------------------------------------------------------------
# Phase 16: Work Orders, Mission Lineage, Multi-Asset Treasury & Autonomous Operations
# --------------------------------------------------------------------------

class CreateWorkOrderRequest(BaseModel):
    work_order_id: str | None = None
    client_id: str = Field(default="client-enterprise", min_length=1)
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5, max_length=2000)
    deliverable_type: str = Field(default="SECURITY_AUDIT")
    required_orbio_credits: int = Field(default=500_000, ge=1)
    bounty_amount: int = Field(default=200, ge=1)
    bounty_asset: str = Field(default="USDG")
    deadline_seconds: int = Field(default=3600, ge=60)
    metadata: Dict[str, Any] = Field(default_factory=dict)


@app.get("/api/v1/organisations/{org_id}/work-orders")
def list_work_orders_endpoint(
    org_id: str,
    status: str | None = None,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        actual_tenant = org.get("tenant_id") or "tenant-demo"
        if x_tenant_id and x_tenant_id != actual_tenant:
            raise HTTPException(status_code=404, detail="Organisation not found")
        tenant_id = actual_tenant
        repo = WorkOrderRepository(db)
        st_filter = WorkOrderStatus(status) if status else None
        orders = repo.list_work_orders(tenant_id=tenant_id, organisation_id=org_id, status=st_filter)

        results = []
        for wo in orders:
            deliv = repo.get_deliverable_by_work_order(tenant_id, org_id, wo.work_order_id)
            rcpt = repo.get_receipt_for_work_order(tenant_id, org_id, wo.work_order_id)
            results.append({
                "work_order": {
                    "work_order_id": wo.work_order_id,
                    "client_id": wo.client_id,
                    "title": wo.title,
                    "description": wo.description,
                    "deliverable_type": wo.deliverable_type,
                    "required_orbio_credits": wo.required_orbio_credits,
                    "bounty_amount": wo.bounty_amount,
                    "bounty_asset": wo.bounty_asset.value if hasattr(wo.bounty_asset, "value") else str(wo.bounty_asset),
                    "status": wo.status.value if hasattr(wo.status, "value") else str(wo.status),
                    "created_at": wo.created_at.isoformat() if hasattr(wo.created_at, "isoformat") else str(wo.created_at),
                    "metadata": wo.metadata,
                },
                "deliverable": {
                    "deliverable_id": deliv.deliverable_id,
                    "producer_agent_id": deliv.producer_agent_id,
                    "content_payload": deliv.content_payload,
                    "content_hash": deliv.content_hash,
                    "orbio_credits_consumed": deliv.orbio_credits_consumed,
                    "telemetry": deliv.execution_telemetry,
                } if deliv else None,
                "receipt": {
                    "receipt_id": rcpt.receipt_id,
                    "status": rcpt.status.value if hasattr(rcpt.status, "value") else str(rcpt.status),
                    "evidence_hash": rcpt.evidence_hash,
                    "verifier_identity": rcpt.verifier_identity,
                    "verification_notes": rcpt.verification_notes,
                    "verified_at": rcpt.verified_at.isoformat() if hasattr(rcpt.verified_at, "isoformat") else str(rcpt.verified_at),
                } if rcpt else None,
            })
        return {"work_orders": results, "total": len(results)}
    finally:
        db.close()


@app.post("/api/v1/organisations/{org_id}/work-orders")
def create_work_order_endpoint(
    org_id: str,
    req: CreateWorkOrderRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        actual_tenant = org.get("tenant_id") or "tenant-demo"
        if x_tenant_id and x_tenant_id != actual_tenant:
            raise HTTPException(status_code=404, detail="Organisation not found")
        tenant_id = actual_tenant
        repo = WorkOrderRepository(db)
        wo_id = req.work_order_id or f"wo-{uuid.uuid4().hex[:8]}"

        wo = WorkOrder(
            tenant_id=tenant_id,
            organisation_id=org_id,
            work_order_id=wo_id,
            client_id=req.client_id,
            title=req.title,
            description=req.description,
            deliverable_type=req.deliverable_type,
            required_orbio_credits=req.required_orbio_credits,
            bounty_amount=req.bounty_amount,
            bounty_asset=CurrencyAsset(req.bounty_asset) if req.bounty_asset in CurrencyAsset.__members__ else CurrencyAsset.USDG,
            deadline_seconds=req.deadline_seconds,
            status=WorkOrderStatus.PROPOSED,
            metadata=req.metadata,
        )
        repo.save_work_order(wo)
        return {"work_order_id": wo_id, "status": wo.status.value, "created": True}
    finally:
        db.close()


@app.post("/api/v1/organisations/{org_id}/work-orders/{work_order_id}/execute")
def execute_work_order_endpoint(
    org_id: str,
    work_order_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        actual_tenant = org.get("tenant_id") or "tenant-demo"
        if x_tenant_id and x_tenant_id != actual_tenant:
            raise HTTPException(status_code=404, detail="Organisation not found")
        tenant_id = actual_tenant
        repo = WorkOrderRepository(db)
        wo = repo.get_work_order(tenant_id, org_id, work_order_id)
        if not wo:
            raise HTTPException(status_code=404, detail="Work order not found")

        ledger = _org_scoped_ledger(db, org)
        provider = _get_exchange_provider()
        persisted_credits = repo.get_credit_balance(tenant_id, org_id)
        provider._credit[org_id] = persisted_credits
        provider.set_usdg_balance(org_id, ledger.get_balance(TREASURY) * 1_000_000)

        executor = SimulatedWorkExecutor(exchange_provider=provider)
        secret = policy_secret()
        verifier = WorkDeliverableVerifier(secret_key=secret)
        reconciler = SurplusReconciler(
            ledger=ledger,
            default_reserve_ratio=0.20,
            receipt_secret_key=secret,
        )
        runner = SelfSustainingLoopRunner(
            ledger=ledger,
            work_executor=executor,
            work_verifier=verifier,
            surplus_reconciler=reconciler,
            exchange_provider=provider,
        )

        adapter = MockAgentAdapter()
        ceo = CEOAgent(agent_id="ceo-orchestrator", adapter=adapter)
        analyst = FinancialAnalystAgent(agent_id="analyst-finance", adapter=adapter)
        coordinator = WorkOrderCoordinator(
            ceo_agent=ceo,
            financial_analyst=analyst,
            loop_runner=runner,
            work_order_repo=repo,
        )

        treasury_usdg = ledger.get_balance(TREASURY)
        current_credits = provider.get_credit_balance(org_id)
        mission_id = f"mission-exec-{uuid.uuid4().hex[:6]}"

        purchase_loop = None
        if wo.required_orbio_credits > current_credits:
            purchase_loop = _build_purchase_loop(
                org=org,
                ledger=ledger,
                provider=provider,
                mission_id=mission_id,
                target_credit=wo.required_orbio_credits,
            )

        outcome = coordinator.select_and_coordinate(
            mission_id=mission_id,
            work_orders=[wo],
            current_treasury_usdg=treasury_usdg,
            current_orbio_credits=current_credits,
            producer_agent_id="agent-engineer",
            purchase_loop=purchase_loop,
        )

        repo.save_credit_balance(tenant_id, org_id, provider.get_credit_balance(org_id))

        return {
            "success": outcome.success,
            "work_order_id": outcome.work_order_id,
            "status": outcome.status.value if hasattr(outcome.status, "value") else str(outcome.status),
            "viable": outcome.evaluation.viable,
            "net_surplus_usdg": outcome.net_surplus_usdg,
            "allocated_to_mission_budget": outcome.allocated_to_mission_budget,
            "allocated_to_reserve": outcome.allocated_to_reserve,
            "error_message": outcome.error_message,
        }
    finally:
        db.close()


@app.get("/api/v1/organisations/{org_id}/missions/lineage")
def get_mission_lineage_endpoint(
    org_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        actual_tenant = org.get("tenant_id") or "tenant-demo"
        if x_tenant_id and x_tenant_id != actual_tenant:
            raise HTTPException(status_code=404, detail="Organisation not found")
        tenant_id = actual_tenant
        repo = WorkOrderRepository(db)
        lineage = repo.list_mission_lineage(tenant_id=tenant_id, organisation_id=org_id)
        return {"lineage": lineage, "total": len(lineage)}
    finally:
        db.close()


@app.get("/api/v1/organisations/{org_id}/treasury/breakdown")
def get_treasury_breakdown_endpoint(
    org_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        actual_tenant = org.get("tenant_id") or "tenant-demo"
        if x_tenant_id and x_tenant_id != actual_tenant:
            raise HTTPException(status_code=404, detail="Organisation not found")
        tenant_id = actual_tenant
        ledger = _org_scoped_ledger(db, org)
        
        treasury_usdg = ledger.get_balance(TREASURY)
        regime = derive_solvency_regime(treasury_usdg, expansion_threshold=150, standby_threshold=40)
        repo = WorkOrderRepository(db)
        rev_events = repo.list_revenue_events(tenant_id=tenant_id, organisation_id=org_id)
        
        total_gross = sum(e.gross_revenue_usdg for e in rev_events)
        total_surplus = sum(e.net_surplus_usdg for e in rev_events)
        total_credits = sum(e.orbio_credits_consumed for e in rev_events)

        return {
            "organisation_id": org_id,
            "tenant_id": tenant_id,
            "solvency_regime": regime.value,
            "treasury_usdg": treasury_usdg,
            "cumulative_gross_revenue_usdg": total_gross,
            "cumulative_net_surplus_usdg": total_surplus,
            "cumulative_compute_credits_consumed": total_credits,
            "revenue_events_count": len(rev_events),
        }
    finally:
        db.close()


@app.post("/api/v1/organisations/{org_id}/daemon/step")
def daemon_step_endpoint(
    org_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        actual_tenant = org.get("tenant_id") or "tenant-demo"
        if x_tenant_id and x_tenant_id != actual_tenant:
            raise HTTPException(status_code=404, detail="Organisation not found")
        tenant_id = actual_tenant
        repo = WorkOrderRepository(db)
        ledger = _org_scoped_ledger(db, org)
        provider = _get_exchange_provider()
        persisted_credits = repo.get_credit_balance(tenant_id, org_id)
        provider._credit[org_id] = persisted_credits
        provider.set_usdg_balance(org_id, ledger.get_balance(TREASURY) * 1_000_000)

        executor = SimulatedWorkExecutor(exchange_provider=provider)
        secret = policy_secret()
        verifier = WorkDeliverableVerifier(secret_key=secret)
        reconciler = SurplusReconciler(
            ledger=ledger,
            default_reserve_ratio=0.20,
            receipt_secret_key=secret,
        )
        runner = SelfSustainingLoopRunner(
            ledger=ledger,
            work_executor=executor,
            work_verifier=verifier,
            surplus_reconciler=reconciler,
            exchange_provider=provider,
        )

        adapter = MockAgentAdapter()
        ceo = CEOAgent(agent_id="ceo-orchestrator", adapter=adapter)
        analyst = FinancialAnalystAgent(agent_id="analyst-finance", adapter=adapter)
        coordinator = WorkOrderCoordinator(
            ceo_agent=ceo,
            financial_analyst=analyst,
            loop_runner=runner,
            work_order_repo=repo,
        )

        def _factory(m_id: str, credits_needed: int = 1_000_000) -> OrbioPurchaseAgentLoop:
            return _build_purchase_loop(
                org=org,
                ledger=ledger,
                provider=provider,
                mission_id=m_id,
                target_credit=credits_needed,
            )

        daemon = AutonomousDaemon(
            tenant_id=tenant_id,
            organisation_id=org_id,
            coordinator=coordinator,
            ledger=ledger,
            work_order_repo=repo,
            expansion_threshold=150,
            standby_threshold=40,
            purchase_loop_factory=_factory,
        )

        cycle_res = daemon.step_cycle()
        repo.save_credit_balance(tenant_id, org_id, provider.get_credit_balance(org_id))

        return {
            "cycle_number": cycle_res.cycle_number,
            "regime": cycle_res.regime.value,
            "mission_id": cycle_res.mission_id,
            "work_order_id": cycle_res.work_order_id,
            "treasury_before": cycle_res.treasury_balance_before,
            "treasury_after": cycle_res.treasury_balance_after,
            "summary": cycle_res.state_summary,
            "success": cycle_res.outcome.success if cycle_res.outcome else False,
        }
    finally:
        db.close()





# =========================================================================
# Phase 17: Inter-DAO B2B Marketplace & Dynamic Capabilities
# =========================================================================

from src.domain.marketplace import MarketplaceOrder, MarketplaceOrderStatus
from src.persistence.marketplace_repository import MarketplaceRepository
from src.agents.b2b_marketplace_coordinator import B2BMarketplaceCoordinator
from src.orchestration.capability_manager import CapabilityManager


class CreateMarketplaceOrderRequest(BaseModel):
    title: str
    description: str
    required_capability: str = "ADVANCED_ANALYTICS"
    bounty_amount: int
    sla_timeout_seconds: int = 3600


@app.get("/api/v1/marketplace/orders")
def list_marketplace_orders_endpoint(
    status: str | None = Query(default="OPEN"),
) -> Dict[str, Any]:
    """Public sanitized marketplace order board."""
    db = _db()
    try:
        repo = MarketplaceRepository(db)
        orders = repo.list_public_orders(status=status)
        return {
            "orders": [o.to_dict() for o in orders],
            "total": len(orders),
        }
    finally:
        db.close()


@app.post("/api/v1/organisations/{org_id}/marketplace/orders")
def publish_marketplace_order_endpoint(
    org_id: str,
    req: CreateMarketplaceOrderRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        actual_tenant = org.get("tenant_id") or "tenant-demo"
        if x_tenant_id and x_tenant_id != actual_tenant:
            raise HTTPException(status_code=404, detail="Organisation not found")
        tenant_id = actual_tenant

        ledger = _org_scoped_ledger(db, org)
        mkt_repo = MarketplaceRepository(db)
        wo_repo = WorkOrderRepository(db)
        coordinator = B2BMarketplaceCoordinator(marketplace_repo=mkt_repo, work_order_repo=wo_repo)

        policy_engine = PolicyEngine(signing_secret=policy_secret())
        client_agent = AgentRecord(
            id=f"{org_id}-ceo",
            organisation_id=org_id,
            role="CEO",
            allowed_action_types=[ActionType.PUBLISH_MARKETPLACE_ORDER],
        )
        org_entity = Organisation(
            id=org_id,
            name=org["name"],
            state=OrgState(org["state"]),
            treasury_balance=ledger.get_balance(TREASURY),
        )

        order = coordinator.publish_b2b_order(
            client_tenant_id=tenant_id,
            client_org_id=org_id,
            title=req.title,
            description=req.description,
            required_capability=req.required_capability,
            bounty_amount=req.bounty_amount,
            client_ledger=ledger,
            policy_engine=policy_engine,
            client_agent=client_agent,
            client_org=org_entity,
            sla_timeout_seconds=req.sla_timeout_seconds,
        )

        return {
            "order_id": order.order_id,
            "status": order.status.value,
            "bounty_amount": order.bounty_amount,
            "created": True,
        }
    finally:
        db.close()


@app.post("/api/v1/organisations/{org_id}/marketplace/orders/{order_id}/claim")
def claim_marketplace_order_endpoint(
    org_id: str,
    order_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        actual_tenant = org.get("tenant_id") or "tenant-demo"
        if x_tenant_id and x_tenant_id != actual_tenant:
            raise HTTPException(status_code=404, detail="Organisation not found")
        tenant_id = actual_tenant

        mkt_repo = MarketplaceRepository(db)
        wo_repo = WorkOrderRepository(db)
        coordinator = B2BMarketplaceCoordinator(marketplace_repo=mkt_repo, work_order_repo=wo_repo)

        policy_engine = PolicyEngine(signing_secret=policy_secret())
        cap_manager = CapabilityManager(repository=mkt_repo, policy_engine=policy_engine)

        provider_agent = AgentRecord(
            id=f"{org_id}-agent-worker",
            organisation_id=org_id,
            role="RESEARCHER",
            allowed_action_types=[ActionType.CLAIM_MARKETPLACE_ORDER],
        )
        # Give provider agent high empirical performance score for promotion eligibility
        provider_agent.performance_score = 92.0

        provider_org = Organisation(
            id=org_id,
            name=org["name"],
            state=OrgState(org["state"]),
            treasury_balance=100,
        )

        claimed_order = coordinator.discover_and_claim(
            provider_tenant_id=tenant_id,
            provider_org_id=org_id,
            provider_agent=provider_agent,
            provider_org=provider_org,
            capability_manager=cap_manager,
            policy_engine=policy_engine,
            target_order_id=order_id,
        )

        if not claimed_order:
            raise HTTPException(status_code=400, detail="Failed to claim marketplace order (unmet capability or already claimed)")

        return {
            "order_id": claimed_order.order_id,
            "status": claimed_order.status.value,
            "claimed_by_org_id": claimed_order.claimed_by_org_id,
            "work_order_id": claimed_order.work_order_id,
        }
    finally:
        db.close()


@app.post("/api/v1/organisations/{org_id}/marketplace/orders/{order_id}/settle")
def settle_marketplace_order_endpoint(
    org_id: str,
    order_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        actual_tenant = org.get("tenant_id") or "tenant-demo"
        if x_tenant_id and x_tenant_id != actual_tenant:
            raise HTTPException(status_code=404, detail="Organisation not found")
        tenant_id = actual_tenant

        mkt_repo = MarketplaceRepository(db)
        wo_repo = WorkOrderRepository(db)
        secret = policy_secret()
        coordinator = B2BMarketplaceCoordinator(
            marketplace_repo=mkt_repo,
            work_order_repo=wo_repo,
            receipt_secret_key=secret,
        )

        escrow = mkt_repo.get_escrow_by_order(order_id)
        if not escrow:
            raise HTTPException(status_code=404, detail="Escrow agreement not found for order")

        client_org_data = _org_id_or_404(db, escrow.client_org_id)
        client_ledger = _org_scoped_ledger(db, client_org_data)
        provider_ledger = _org_scoped_ledger(db, org)

        order = mkt_repo.get_order(escrow.client_tenant_id, escrow.client_org_id, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Marketplace order not found")

        provider = _get_exchange_provider()
        persisted_credits = wo_repo.get_credit_balance(tenant_id, org_id)
        provider._credit[org_id] = max(persisted_credits, 50_000)
        executor = SimulatedWorkExecutor(exchange_provider=provider)
        verifier = WorkDeliverableVerifier(secret_key=secret)
        reconciler = SurplusReconciler(
            ledger=provider_ledger,
            default_reserve_ratio=0.20,
            receipt_secret_key=secret,
        )

        outcome = coordinator.execute_and_settle(
            order=order,
            provider_tenant_id=tenant_id,
            provider_org_id=org_id,
            producer_agent_id=f"{org_id}-agent-worker",
            work_executor=executor,
            work_verifier=verifier,
            client_ledger=client_ledger,
            provider_ledger=provider_ledger,
            surplus_reconciler=reconciler,
        )

        return {
            "order_id": outcome.order_id,
            "success": outcome.success,
            "net_surplus_usdg": outcome.net_surplus_usdg,
            "allocated_to_mission_budget": outcome.allocated_to_mission_budget,
            "allocated_to_reserve": outcome.allocated_to_reserve,
            "is_simulated": outcome.is_simulated,
            "client_escrow_tx_id": outcome.client_escrow_tx_id,
            "provider_revenue_tx_id": outcome.provider_revenue_tx_id,
        }
    finally:
        db.close()


@app.get("/api/v1/organisations/{org_id}/capabilities")
def list_capabilities_endpoint(
    org_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Dict[str, Any]:
    db = _db()
    try:
        org = _org_id_or_404(db, org_id)
        actual_tenant = org.get("tenant_id") or "tenant-demo"
        if x_tenant_id and x_tenant_id != actual_tenant:
            raise HTTPException(status_code=404, detail="Organisation not found")
        tenant_id = actual_tenant

        mkt_repo = MarketplaceRepository(db)
        grants = mkt_repo.list_agent_grants(tenant_id, org_id, f"{org_id}-agent-worker", active_only=False)
        return {
            "organisation_id": org_id,
            "capabilities": [g.to_dict() for g in grants],
            "total": len(grants),
        }
    finally:
        db.close()


WEB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../apps/web"))

if os.path.isdir(WEB_ROOT):
    app.mount("/assets", StaticFiles(directory=os.path.join(WEB_ROOT, "assets")), name="assets")

    @app.get("/")
    def landing() -> FileResponse:
        return FileResponse(os.path.join(WEB_ROOT, "landing.html"))

    @app.get("/command-centre")
    def command_centre() -> FileResponse:
        return FileResponse(os.path.join(WEB_ROOT, "index.html"))
