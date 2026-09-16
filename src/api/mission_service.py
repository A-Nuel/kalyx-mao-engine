import os
import uuid
from datetime import datetime
from typing import Any, Dict

from src.agents.mock_adapter import MockAgentAdapter
from src.agents.openrouter_adapter import OpenRouterAgentAdapter
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.agents.roles.researcher import ResearcherAgent
from src.agents.roles.strategist import StrategistAgent
from src.api.bootstrap import policy_secret
from src.audit.auditor import Auditor
from src.domain.economy import (
    AgentPerformanceRecord,
    AllocationStrategy,
    ReputationHistoryEntry,
)
from src.domain.entities import AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole
from src.economy.allocator import ResourceAllocator
from src.economy.reputation import ReputationEngine
from src.governance.human_gate import HumanGate
from src.governance.policy_engine import PolicyEngine
from src.orchestration.engine import OrchestrationEngine
from src.persistence.economy_repo import EconomyRepository
from src.persistence.factory import create_database, create_sqlite, create_scoped_ledger
from src.persistence.repositories import SqliteEventStore, SqliteRepository
from src.security.atomic_ledger import AtomicSqliteLedger
from src.security.durable_executor import DurableControlledExternalExecutor
from src.tenancy.ledger import TenantScopedLedger
from src.tenancy.organisation_ledger import OrganisationScopedLedger


def run_mission(
    mission: str,
    budget: int,
    *,
    live: bool = False,
    db_path: str | None = None,
    tenant_id: str = "tenant-demo",
    strategy: str = "PERFORMANCE",
) -> Dict[str, Any]:
    """Run one bounded MAO mission with organisation-scoped resources."""
    if not mission.strip():
        raise ValueError("Mission cannot be empty")
    if budget < 1 or budget > 10_000:
        raise ValueError("Budget must be between 1 and 10,000 ORG Credits")
    if not tenant_id.strip() or ":" in tenant_id:
        raise ValueError("tenant_id must be a non-empty identifier without ':'")

    # Explicit db_path keeps deterministic tests/demos on SQLite; otherwise use factory.
    if db_path is not None:
        db = create_sqlite(db_path)
    else:
        db = create_database()
    try:
        organisation_id = f"mao-{uuid.uuid4().hex[:12]}"
        ledger = create_scoped_ledger(db, tenant_id=tenant_id, organisation_id=organisation_id, initial_treasury=budget)
        event_store = SqliteEventStore(db, verify_on_startup=True)
        repo = SqliteRepository(db)
        secret = policy_secret()
        policy = PolicyEngine(signing_secret=secret)
        executor = DurableControlledExternalExecutor(
            policy_engine=policy,
            ledger=ledger,
            db_conn=db.conn,
            allowlist={"sandbox://market_index_fund", "sandbox://verified_bonds", "api://market_data/v1/summary"},
            mock_handler=lambda target, params: (200, {"status": "success", "target": target, "data": "executed_cleanly"}),
        )
        auditor = Auditor(verification_secret=secret)
        ids = {role: f"{organisation_id}-agent-{role}" for role in ("ceo", "research", "strategy", "finance")}
        org = Organisation(id=organisation_id, tenant_id=tenant_id, mission=mission.strip(), treasury_balance=budget)
        agents = {
            ids["ceo"]: AgentRecord(
                id=ids["ceo"], role=AgentRole.CEO, authority_ceiling=min(25, budget),
                allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION, ActionType.REPLAN],
            ),
            ids["research"]: AgentRecord(
                id=ids["research"], role=AgentRole.RESEARCHER, authority_ceiling=min(20, budget),
                allowed_action_types=[ActionType.DATA_FETCH, ActionType.INTERNAL_ANALYSIS],
            ),
            ids["strategy"]: AgentRecord(
                id=ids["strategy"], role=AgentRole.STRATEGIST, authority_ceiling=min(20, budget),
                allowed_action_types=[ActionType.INTERNAL_ANALYSIS],
            ),
            ids["finance"]: AgentRecord(
                id=ids["finance"], role=AgentRole.FINANCIAL_ANALYST, authority_ceiling=min(25, budget),
                allowed_action_types=[
                    ActionType.INTERNAL_ANALYSIS, ActionType.DATA_FETCH,
                    ActionType.EXTERNAL_API_CALL, ActionType.SIMULATED_ALLOCATION,
                ],
            ),
        }
        org.agents.update(agents)
        repo.save_organisation(org)
        for agent in agents.values():
            repo.save_agent(agent, org.id)
        mock = MockAgentAdapter()
        adapter = (
            OpenRouterAgentAdapter(fallback_adapter=mock, fallback_on_error=True)
            if live and os.getenv("OPENROUTER_API_KEY")
            else mock
        )
        # Initialize economy repository and run deterministic resource allocation
        economy_repo = EconomyRepository(db.conn)
        resolved_strategy = AllocationStrategy(strategy.upper()) if hasattr(AllocationStrategy, strategy.upper()) else AllocationStrategy.PERFORMANCE
        alloc_decision = ResourceAllocator.allocate(
            org=org,
            strategy=resolved_strategy,
            mission_id=f"mission-{organisation_id}",
            mission_type=mission,
        )
        economy_repo.save_allocation(alloc_decision)
        event_store.append_event(
            actor_id="RESOURCE_ALLOCATOR",
            event_type="RESOURCE_ALLOCATED",
            entity_id=alloc_decision.id,
            payload=alloc_decision.model_dump(mode="json"),
        )

        engine = OrchestrationEngine(
            org=org, ledger=ledger, policy_engine=policy, executor=executor, event_store=event_store,
            human_gate=HumanGate(), ceo=CEOAgent(ids["ceo"], adapter), researcher=ResearcherAgent(ids["research"], adapter),
            strategist=StrategistAgent(ids["strategy"], adapter), financial_analyst=FinancialAnalystAgent(ids["finance"], adapter),
            auditor=auditor, repository=repo, max_replan_attempts=3,
        )
        engine.start_mission()
        tasks = engine.decompose_and_plan()
        research, strategy, finance = engine.run_intelligence_pipeline()
        proposal = engine.ceo.formulate_action_proposal("task-03", finance)
        decision, receipt = engine.process_action_proposal("task-03", proposal)
        review = engine.complete_mission()

        # Update and persist agent performance records post-mission
        for agent in org.agents.values():
            perf_rec = economy_repo.get_performance_record(tenant_id, org.id, agent.id)
            if not perf_rec:
                perf_rec = AgentPerformanceRecord(
                    agent_id=agent.id,
                    organisation_id=org.id,
                    tenant_id=tenant_id,
                    tasks_completed=agent.successful_tasks,
                    tasks_failed=agent.failed_tasks,
                    policy_violations=agent.policy_violations,
                    performance_score=agent.performance_score,
                    reliability_score=agent.reliability_score,
                    resource_efficiency_score=agent.resource_efficiency,
                    reputation_score=agent.reputation_score,
                    composite_score=agent.performance_score,
                    authority_level=min(5, max(1, agent.authority_ceiling // 10)),
                )
            else:
                perf_rec.tasks_completed = agent.successful_tasks
                perf_rec.tasks_failed = agent.failed_tasks
                perf_rec.policy_violations = agent.policy_violations
                perf_rec.performance_score = agent.performance_score
                perf_rec.reliability_score = agent.reliability_score
                perf_rec.resource_efficiency_score = agent.resource_efficiency
                perf_rec.reputation_score = agent.reputation_score
                perf_rec.composite_score = agent.performance_score
                perf_rec.authority_level = min(5, max(1, agent.authority_ceiling // 10))
            perf_rec.missions_contributed += 1
            perf_rec.last_evaluated_at = datetime.utcnow()
            economy_repo.save_performance_record(perf_rec)

        return {
            "organisation_id": org.id,
            "tenant_id": tenant_id,
            "mission": org.mission,
            "state": org.state.value,
            "budget": budget,
            "treasury": ledger.get_balance("TREASURY"),
            "allocation_strategy": resolved_strategy.value,
            "allocations": alloc_decision.allocations,
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
