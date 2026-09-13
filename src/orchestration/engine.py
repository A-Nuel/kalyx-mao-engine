import uuid
from typing import Dict, List, Optional, Tuple, Any
from src.domain.entities import Organisation, Task, ActionProposal, PolicyDecision, ExecutionReceipt
from src.domain.enums import OrgState, TaskStatus, PolicyResult
from src.domain.exceptions import PolicyViolationError, NoEligibleAgentException
from src.governance.policy_engine import PolicyEngine
from src.governance.human_gate import HumanGate
from src.economy.reputation import ReputationEngine
from src.execution.base import BaseExecutor
from src.audit.auditor import Auditor, VerificationReceipt, AuditVerificationError
from src.orchestration.state_machine import StateMachine
from src.orchestration.assignment import AgentAssignmentEngine
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.researcher import ResearcherAgent
from src.agents.roles.strategist import StrategistAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.agents.schemas import ResearchOutput, StrategyOutput, FinancialProposalOutput, MissionReviewOutput


class TaskRegistry(dict):
    """Organisation-scoped task storage with backwards-compatible logical lookup."""
    def __init__(self):
        super().__init__()
        self.aliases: Dict[str, str] = {}

    def __getitem__(self, key: str) -> Task:
        return super().__getitem__(self.aliases.get(key, key))

    def get(self, key: str, default=None):
        return super().get(self.aliases.get(key, key), default)

    def bind(self, logical_id: str, actual_id: str) -> None:
        self.aliases[logical_id] = actual_id


class OrchestrationEngine:
    """Deterministic coordinator whose persisted resources are scoped to one organisation."""
    def __init__(self, org: Organisation, ledger: Any, policy_engine: PolicyEngine, executor: BaseExecutor,
                 event_store: Any, human_gate: HumanGate, ceo: CEOAgent, researcher: ResearcherAgent,
                 strategist: StrategistAgent, financial_analyst: FinancialAnalystAgent,
                 auditor: Optional[Auditor] = None, repository: Optional[Any] = None, max_replan_attempts: int = 3):
        self.org, self.ledger, self.policy_engine, self.executor = org, ledger, policy_engine, executor
        self.event_store, self.human_gate = event_store, human_gate
        self.ceo, self.researcher, self.strategist, self.financial_analyst = ceo, researcher, strategist, financial_analyst
        self.auditor, self.repository, self.max_replan_attempts = auditor or Auditor(), repository, max_replan_attempts
        self.tasks: TaskRegistry = TaskRegistry(); self._task_aliases: Dict[str, str] = self.tasks.aliases; self.replan_counts: Dict[str, int] = {}
        self.execution_receipts: List[ExecutionReceipt] = []; self.verification_receipts: List[VerificationReceipt] = []

    def _task_id(self, logical_id: str) -> str: return self._task_aliases.get(logical_id, logical_id)
    def _sync_state(self) -> None:
        if self.repository:
            self.repository.save_organisation(self.org)
            for t in self.tasks.values(): self.repository.save_task(t, self.org.id)
            for a in self.org.agents.values(): self.repository.save_agent(a, self.org.id)

    def start_mission(self) -> None:
        StateMachine.transition_org(self.org, OrgState.PLANNING)
        self.event_store.append_event(actor_id="ORCHESTRATOR", event_type="MISSION_STARTED", entity_id=self.org.id,
                                      payload={"mission": self.org.mission, "treasury_balance": self.org.treasury_balance})
        self._sync_state()

    def decompose_and_plan(self) -> List[Task]:
        plan = self.ceo.create_initial_plan(self.org.mission, self.org.treasury_balance)
        self.event_store.append_event(actor_id=self.ceo.agent_id, event_type="MISSION_PLAN_CREATED", entity_id=self.org.id, payload=plan.model_dump())
        created = []
        for item in plan.tasks:
            logical_id = item.task_id
            actual_id = f"{self.org.id}-{logical_id}-{uuid.uuid4().hex[:8]}"
            self._task_aliases[logical_id] = actual_id
            task = Task(
                id=actual_id, mission_id=self.org.id, assigned_agent_id=None,
                objective=item.objective, allocated_credits=item.allocated_credits, status=TaskStatus.PENDING,
            )
            try:
                AgentAssignmentEngine.assign_and_allocate(
                    task=task, org=self.org, required_role=item.assigned_role, requested_credits=item.allocated_credits,
                )
            except NoEligibleAgentException as exc:
                # Never invent a synthetic agent ID. Keep the task PENDING with no assignee;
                # later stages may still run with explicit role agents registered on the engine.
                task.assigned_agent_id = None
                task.status = TaskStatus.PENDING
                self.event_store.append_event(
                    actor_id="ORCHESTRATOR",
                    event_type="TASK_ASSIGNMENT_FAILED",
                    entity_id=task.id,
                    payload={"reason": str(exc), "required_role": getattr(item.assigned_role, "value", None)},
                )
            self.tasks[actual_id] = task
            self.tasks.bind(logical_id, actual_id)
            created.append(task)
        StateMachine.transition_org(self.org, OrgState.EXECUTING)
        self._sync_state()
        return created

    def run_intelligence_pipeline(self) -> Tuple[ResearchOutput, StrategyOutput, FinancialProposalOutput]:
        rt = self.tasks.get(self._task_id("task-01"))
        if rt and rt.status not in {TaskStatus.FAILED, TaskStatus.COMPLETED}:
            StateMachine.transition_task(rt, TaskStatus.IN_PROGRESS)
        research = self.researcher.conduct_research("Analyze available investment options")
        self.event_store.append_event(actor_id=self.researcher.agent_id, event_type="RESEARCH_COMPLETED", entity_id=rt.id if rt else self._task_id("task-01"), payload=research.model_dump())
        if rt and rt.status not in {TaskStatus.FAILED, TaskStatus.COMPLETED}:
            rt.output_evidence = research.model_dump(); StateMachine.transition_task(rt, TaskStatus.COMPLETED)
            if (a := self.org.agents.get(self.researcher.agent_id)): ReputationEngine.record_task_success(a)
        st = self.tasks.get(self._task_id("task-02"))
        if st and st.status not in {TaskStatus.FAILED, TaskStatus.COMPLETED}:
            StateMachine.transition_task(st, TaskStatus.IN_PROGRESS)
        strategy = self.strategist.evaluate_strategy(research)
        self.event_store.append_event(actor_id=self.strategist.agent_id, event_type="STRATEGY_EVALUATED", entity_id=st.id if st else self._task_id("task-02"), payload=strategy.model_dump())
        if st and st.status not in {TaskStatus.FAILED, TaskStatus.COMPLETED}:
            st.output_evidence = strategy.model_dump(); StateMachine.transition_task(st, TaskStatus.COMPLETED)
            if (a := self.org.agents.get(self.strategist.agent_id)): ReputationEngine.record_task_success(a)
        ft = self.tasks.get(self._task_id("task-03"))
        if ft and ft.status not in {TaskStatus.FAILED, TaskStatus.COMPLETED}:
            StateMachine.transition_task(ft, TaskStatus.IN_PROGRESS)
        finance = self.financial_analyst.formulate_proposal(strategy)
        self.event_store.append_event(actor_id=self.financial_analyst.agent_id, event_type="FINANCIAL_PROPOSAL_FORMULATED", entity_id=ft.id if ft else self._task_id("task-03"), payload=finance.model_dump())
        self._sync_state(); return research, strategy, finance

    def process_action_proposal(self, task_id: str, proposal: ActionProposal) -> Tuple[PolicyDecision, Optional[ExecutionReceipt]]:
        actual = self._task_id(task_id); task = self.tasks[actual]; proposal.task_id = task.id
        if task.status == TaskStatus.FAILED:
            raise PolicyViolationError(f"Task {actual} is already FAILED and cannot accept proposals")
        # Allow PENDING / IN_PROGRESS / REJECTED recovery paths into SUBMITTED.
        if task.status != TaskStatus.SUBMITTED:
            StateMachine.transition_task(task, TaskStatus.SUBMITTED)
        task.proposals.append(proposal)
        if self.repository: self.repository.save_proposal(proposal)
        self.event_store.append_event(actor_id=proposal.proposing_agent_id, event_type="PROPOSAL_SUBMITTED", entity_id=proposal.id, payload=proposal.model_dump())
        decision = self.policy_engine.evaluate(proposal, self.org, ledger=self.ledger)
        if self.repository: self.repository.save_policy_decision(decision)
        self.event_store.append_event(actor_id="POLICY_ENGINE", event_type="POLICY_EVALUATED", entity_id=decision.id, payload=decision.model_dump())
        agent = self.org.agents.get(proposal.proposing_agent_id)
        if decision.result == PolicyResult.APPROVED:
            StateMachine.transition_task(task, TaskStatus.APPROVED); receipt = self.executor.execute(proposal, decision, self.org); self.execution_receipts.append(receipt)
            if self.repository: self.repository.save_execution_receipt(receipt)
            self.event_store.append_event(actor_id="EXECUTOR", event_type="ACTION_EXECUTED", entity_id=receipt.id, payload=receipt.model_dump())
            try:
                self.verification_receipts.append(self.auditor.verify_execution(proposal=proposal, decision=decision, receipt=receipt, org=self.org, task=task, ledger=self.ledger, event_store=self.event_store, policy_engine=self.policy_engine))
            except AuditVerificationError as ave:
                StateMachine.transition_task(task, TaskStatus.FAILED); StateMachine.transition_org(self.org, OrgState.FAILED)
                self.event_store.append_event(actor_id="AUDITOR", event_type="AUDIT_FAILED", entity_id=receipt.id, payload={"failures": ave.failures}); self._sync_state(); raise
            if agent: ReputationEngine.record_task_success(agent, credits_allocated=task.allocated_credits, credits_used=receipt.cost_credits, value_score=proposal.expected_value_score, task_id=task.id)
            StateMachine.transition_task(task, TaskStatus.COMPLETED); self._sync_state(); return decision, receipt
        if decision.result == PolicyResult.REJECTED:
            StateMachine.transition_task(task, TaskStatus.REJECTED)
            if agent: ReputationEngine.record_policy_violation(agent, details=f"{decision.violated_rule_id}: {decision.violated_rule_description}", task_id=task.id)
            attempts = self.replan_counts.get(actual, 0) + 1; self.replan_counts[actual] = attempts; self.replan_counts[task_id] = attempts
            if attempts > self.max_replan_attempts:
                if agent: ReputationEngine.record_task_failure(agent, credits_allocated=task.allocated_credits, credits_used=0, reason="Max replan attempts exceeded", task_id=task.id)
                StateMachine.transition_task(task, TaskStatus.FAILED); StateMachine.transition_org(self.org, OrgState.FAILED); self._sync_state()
                raise PolicyViolationError(f"Task {actual} aborted: Max replan attempts ({self.max_replan_attempts}) exceeded. Last violation: {decision.violated_rule_id} - {decision.violated_rule_description}")
            self.event_store.append_event(actor_id="ORCHESTRATOR", event_type="REPLAN_TRIGGERED", entity_id=task.id, payload={"attempt": attempts, "max_attempts": self.max_replan_attempts, "violated_rule_id": decision.violated_rule_id, "reason": decision.violated_rule_description})
            StateMachine.transition_task(task, TaskStatus.IN_PROGRESS)
            replanned = self.ceo.replan_after_rejection(task_id=task_id, rejected_proposal=proposal, violated_rule_id=decision.violated_rule_id or "RULE-UNKNOWN", violated_rule_description=decision.violated_rule_description or "Rejected", attempt_number=attempts)
            self._sync_state(); return self.process_action_proposal(actual, replanned)
        if decision.result == PolicyResult.ESCALATE_TO_HUMAN:
            # Deterministic pause: no spend, no silent continuation, audit the escalation.
            if self.org.state != OrgState.PAUSED:
                StateMachine.transition_org(self.org, OrgState.PAUSED)
            self.event_store.append_event(
                actor_id="POLICY_ENGINE",
                event_type="ESCALATION_REQUIRED",
                entity_id=task.id,
                payload={
                    "proposal_id": proposal.id,
                    "decision_id": decision.id,
                    "reason": decision.violated_rule_description,
                    "requested_credits": proposal.requested_credits,
                    "org_state": self.org.state.value,
                },
            )
            self._sync_state()
            return decision, None
        raise PolicyViolationError(f"Unhandled policy decision result: {decision.result}")

    def complete_mission(self) -> MissionReviewOutput:
        if self.org.state == OrgState.PAUSED:
            # Escalation paused missions are not auto-completed.
            review = self.ceo.review_mission(self.org.mission, [r.model_dump() for r in self.execution_receipts])
            self.event_store.append_event(
                actor_id=self.ceo.agent_id, event_type="MISSION_PAUSED_ESCALATION",
                entity_id=self.org.id, payload=review.model_dump(),
            )
            self._sync_state()
            return review
        StateMachine.transition_org(self.org, OrgState.COMPLETED)
        review = self.ceo.review_mission(self.org.mission, [r.model_dump() for r in self.execution_receipts])
        self.event_store.append_event(actor_id=self.ceo.agent_id, event_type="MISSION_COMPLETED", entity_id=self.org.id, payload=review.model_dump())
        self._sync_state(); return review
