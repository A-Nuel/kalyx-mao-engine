import os
import uuid
from typing import Any, Dict

from src.agents.mock_adapter import MockAgentAdapter
from src.agents.openrouter_adapter import OpenRouterAgentAdapter
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.agents.roles.researcher import ResearcherAgent
from src.agents.roles.strategist import StrategistAgent
from src.audit.auditor import Auditor
from src.domain.entities import AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole
from src.execution.executor import ControlledExternalExecutor
from src.governance.human_gate import HumanGate
from src.governance.policy_engine import PolicyEngine
from src.orchestration.engine import OrchestrationEngine
from src.persistence.database import Database
from src.persistence.repositories import SqliteEventStore, SqliteRepository
from src.security.atomic_ledger import AtomicSqliteLedger
from src.tenancy.ledger import TenantScopedLedger


def run_mission(
    mission: str,
    budget: int,
    *,
    live: bool = False,
    db_path: str | None = None,
    tenant_id: str = "tenant-demo",
) -> Dict[str, Any]:
    """Run one bounded MAO mission through the Phase 7 control loop.

    Phase 8 uses serialized SQLite ledger transfers so concurrent workers cannot
    pass the balance check against the same treasury at the same time.
    """
    if not mission.strip():
        raise ValueError("Mission cannot be empty")
    if budget < 1 or budget > 10_000:
        raise ValueError("Budget must be between 1 and 10,000 ORG Credits")
    if not tenant_id.strip() or ":" in tenant_id:
        raise ValueError("tenant_id must be a non-empty identifier without ':'")

    path = db_path or os.getenv("KALYX_DB", "data/kalyx.db")
    db = Database(path)
    try:
        existing = db.conn.execute("SELECT id FROM organisations LIMIT 1").fetchone()
        if existing:
            raise ValueError("Phase 7 MVP supports one persistent organisation per database; use a new database for a new mission")

        ledger = TenantScopedLedger(AtomicSqliteLedger(db, initial_treasury=0), tenant_id, initial_treasury=budget)
        event_store = SqliteEventStore(db, verify_on_startup=True)
        repo = SqliteRepository(db)
        policy = PolicyEngine(signing_secret=os.getenv("KALYX_POLICY_SECRET", "phase7-demo-policy-secret"))
        executor = ControlledExternalExecutor(
            policy_engine=policy,
            ledger=ledger,
            allowlist={
                "sandbox://market_index_fund",
                "sandbox://verified_bonds",
                "api://market_data/v1/summary",
            },
            mock_handler=lambda target, params: (200, {"status": "success", "target": target, "data": "executed_cleanly"}),
        )
        auditor = Auditor(verification_secret=os.getenv("KALYX_POLICY_SECRET", "phase7-demo-policy-secret"))

        org = Organisation(id=f"mao-{uuid.uuid4().hex[:10]}", tenant_id=tenant_id, mission=mission.strip(), treasury_balance=budget)
        agents = {
            "agent-ceo": AgentRecord(id="agent-ceo", role=AgentRole.CEO, authority_ceiling=min(25, budget), allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION, ActionType.REPLAN]),
            "agent-research": AgentRecord(id="agent-research", role=AgentRole.RESEARCHER, authority_ceiling=min(20, budget), allowed_action_types=[ActionType.DATA_FETCH, ActionType.INTERNAL_ANALYSIS]),
            "agent-strategy": AgentRecord(id="agent-strategy", role=AgentRole.STRATEGIST, authority_ceiling=min(20, budget), allowed_action_types=[ActionType.INTERNAL_ANALYSIS]),
            "agent-finance": AgentRecord(id="agent-finance", role=AgentRole.FINANCIAL_ANALYST, authority_ceiling=min(25, budget), allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.DATA_FETCH, ActionType.EXTERNAL_API_CALL, ActionType.SIMULATED_ALLOCATION]),
        }
        org.agents.update(agents)
        repo.save_organisation(org)
        db.conn.execute("UPDATE organisations SET tenant_id = ? WHERE id = ?", (tenant_id, org.id))
        db.conn.commit()
        for agent in agents.values():
            repo.save_agent(agent, org.id)

        mock = MockAgentAdapter()
        adapter = OpenRouterAgentAdapter(fallback_adapter=mock, fallback_on_error=True) if live and os.getenv("OPENROUTER_API_KEY") else mock
        engine = OrchestrationEngine(
            org=org,
            ledger=ledger,
            policy_engine=policy,
            executor=executor,
            event_store=event_store,
            human_gate=HumanGate(),
            ceo=CEOAgent("agent-ceo", adapter),
            researcher=ResearcherAgent("agent-research", adapter),
            strategist=StrategistAgent("agent-strategy", adapter),
            financial_analyst=FinancialAnalystAgent("agent-finance", adapter),
            auditor=auditor,
            repository=repo,
            max_replan_attempts=3,
        )
        engine.start_mission()
        tasks = engine.decompose_and_plan()
        research, strategy, finance = engine.run_intelligence_pipeline()
        proposal = engine.ceo.formulate_action_proposal("task-03", finance)
        decision, receipt = engine.process_action_proposal("task-03", proposal)
        review = engine.complete_mission()

        return {
            "organisation_id": org.id,
            "tenant_id": tenant_id,
            "mission": org.mission,
            "state": org.state.value,
            "budget": budget,
            "treasury": ledger.get_balance("TREASURY"),
            "tasks": [t.model_dump(mode="json") for t in tasks],
            "research": research.model_dump(mode="json"),
            "strategy": strategy.model_dump(mode="json"),
            "finance": finance.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "receipt": receipt.model_dump(mode="json") if receipt else None,
            "review": review.model_dump(mode="json"),
            "event_count": len(event_store.get_events()),
        }
    finally:
        db.close()
