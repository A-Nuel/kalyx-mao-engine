import uuid
from typing import Dict, List, Optional, Tuple, Any
from src.domain.entities import Organisation, Task, ActionProposal, PolicyDecision, ExecutionReceipt, AgentRecord
from src.domain.enums import OrgState, TaskStatus, PolicyResult, AgentRole, ActionType
from src.domain.exceptions import PolicyViolationError
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
from src.domain.exceptions import NoEligibleAgentException


class OrchestrationEngine:
    """Deterministic coordinator whose persisted resources are scoped to one organisation."""

    def __init__(self, org: Organisation, ledger: Any, policy_engine: PolicyEngine, executor: BaseExecutor,
                 event_store: Any, human_gate: HumanGate, ceo: CEOAgent, researcher: ResearcherAgent,
                 strategist: StrategistAgent, financial_analyst: FinancialAnalystAgent,
                 auditor: Optional[Auditor] = None, repository: Optional[Any] = None,
                 max_replan_attempts: int = 3):
        self.org = org
        self.ledger = ledger
        self.policy_engine = policy_engine
        self.executor = executor
        self.event_store = event_store
        self.human_gate = human_gate
        self.ceo = ceo
        self.researcher = researcher
        self.strategist = strategist
        self.financial_analyst = financial_analyst
        self.auditor = auditor or Auditor()
        self.repository = repository
        self.max_replan_attempts = max_replan_attempts
        self.tasks: Dict[str, Task] = {}
        self._task_aliases: Dict[str, str] = {}
        self.replan_counts: Dict[str, int] = {}
        self.execution_receipts: List[ExecutionReceipt] = []
        self.verification_receipts: List[VerificationReceipt] = []

    def _task_id(self, logical_id: str) -> str:
        return self._task_aliases.get(logical_id, logical_id)

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
        plan_output = self.ceo.create_initial_plan(self.org.mission, self.org.treasury_balance)
        self.event_store.append_event(actor_id=self.ceo.agent_id, event_type="MISSION_PLAN_CREATED", entity_id=self.org.id,
                                      payload=plan_output.model_dump())
        created_tasks: List[Task] = []
        for item in plan_output.tasks:
            logical_id = item.task_id
            actual_id = f"{self.org.id}-{logical_id}-{uuid.uuid4().hex[:8]}"
            self._task_aliases[logical_id] = actual_id
            task = Task(id=actual_id, mission_id=self.org.id, assigned_agent_id=None,
                        objective=item.objective, allocated_credits=item.allocated_credits, status=TaskStatus.PENDING)
            try:
                AgentAssignmentEngine.assign_and_allocate(task=task, org=self.org,
                                                          required_role=item.assigned_role,
                                                          requested_credits=item.allocated_credits)
            except NoEligibleAgentException:
                raise NoEligibleAgentException(f"No eligible agent for role {item.assigned_role.value} in {self.org.id}")
            self.tasks[actual_id] = task
            created_tasks.append(task)
        StateMachine.transition_org(self.org, OrgState.EXECUTING)
        self._sync_state()
        return created_tasks

    def run_intelligence_pipeline(self) -> Tuple[ResearchOutput, StrategyOutput, FinancialProposalOutput]:
        research_task = self.tasks.get(self._task_id("task-01"))
        if research_task: StateMachine.transition_task(research_task, TaskStatus.IN_PROGRESS)
        research = self.researcher.conduct_research("Analyze available investment options")
        self.event_store.append_event(actor_id=self.researcher.agent_id, event_type="RESEARCH_COMPLETED",
                                      entity_id=research_task.id if research_task else self._task_id("task-01"), payload=research.model_dump())
        if research_task:
            research_task.output_evidence = research.model_dump(); StateMachine.transition_task(research_task, TaskStatus.COMPLETED)
            agent = self.org.agents.get(self.researcher.agent_id)
            if agent: ReputationEngine.record_task_success(agent)

        strategy_task = self.tasks.get(self._task_id("task-02"))
        if strategy_task: StateMachine.transition_task(strategy_task, TaskStatus.IN_PROGRESS)
        strategy = self.strategist.evaluate_strategy(research)
        self.event_store.append_event(actor_id=self.strategist.agent_id, event_type="STRATEGY_EVALUATED",
                                      entity_id=strategy_task.id if strategy_task else self._task_id("task-02"), payload=strategy.model_dump())
        if strategy_task:
            strategy_task.output_evidence = strategy.model_dump(); StateMachine.transition_task(strategy_task, TaskStatus.COMPLETED)
            agent = self.org.agents.get(self.strategist.agent_id)
            if agent: ReputationEngine.record_task_success(agent)

        finance_task = self.tasks.get(self._task_id("task-03"))
        if finance_task: StateMachine.transition_task(finance_task, TaskStatus.IN_PROGRESS)
        finance = self.financial_analyst.formulate_proposal(strategy)
        self.event_store.append_event(actor_id=self.financial_analyst.agent_id, event_type="FINANCIAL_PROPOSAL_FORMULATED",
                                      entity_id=finance_task.id if finance_task else self._task_id("task-03"), payload=finance.model_dump())
        self._sync_state()
        return research, strategy, finance

    def process_action_proposal(self, task_id: str, proposal: ActionProposal) -> Tuple[PolicyDecision, Optional[ExecutionReceipt]]:
        actual_task_id = self._task_id(task_id)
        task = self.tasks[actual_task_id]
        # Bind the persisted proposal to the actual organisation-scoped task before hashing/authorization.
        proposal.task_id = actual_task_id
        StateMachine.transition_task(task, TaskStatus.SUBMITTED)
        task.proposals.append(proposal)
        if self.repository: self.repository.save_proposal(proposal)
        self.event_store.append_event(actor_id=proposal.proposing_agent_id, event_type="PROPOSAL_SUBMITTED",
                                      entity_id=proposal.id, payload=proposal.model_dump())
        decision = self.policy_engine.evaluate(proposal, self.org, ledger=self.ledger)
        if self.repository: self.repository.save_policy_decision(decision)
        self.event_store.append_event(actor_id="POLICY_ENGINE", event_type="POLICY_EVALUATED",
                                      entity_id=decision.id, payload=decision.model_dump())
        proposing_agent = self.org.agents.get(proposal.proposing_agent_id)
        if decision.result == PolicyResult.APPROVED:
            StateMachine.transition_task(task, TaskStatus.APPROVED)
            receipt = self.executor.execute(proposal, decision, self.org)
            self.execution_receipts.append(receipt)
            if self.repository: self.repository.save_execution_receipt(receipt)
            self.event_store.append_event(actor_id="EXECUTOR", event_type="ACTION_EXECUTED", entity_id=receipt.id,
                                          payload=receipt.model_dump())
            try:
                verification = self.auditor.verify_execution(proposal=proposal, decision=decision, receipt=receipt,
                    org=self.org, task=task, ledger=self.ledger, event_store=self.event_store, policy_engine=self.policy_engine)
                self.verification_receipts.append(verification)
            except AuditVerificationError as ave:
                StateMachine.transition_task(task, TaskStatus.FAILED); StateMachine.transition_org(self.org, OrgState.FAILED)
                self.event_store.append_event(actor_id="AUDITOR", event_type="AUDIT_FAILED", entity_id=receipt.id,
                                              payload={"failures": ave.failures})
                self._sync_state(); raise
            if proposing_agent:
                ReputationEngine.record_task_success(proposing_agent, credits_allocated=task.allocated_credits,
                    credits_used=receipt.cost_credits, value_score=proposal.expected_value_score, task_id=task.id)
            StateMachine.transition_task(task, TaskStatus.COMPLETED); self._sync_state()
            return decision, receipt
        if decision.result == PolicyResult.REJECTED:
            StateMachine.transition_task(task, TaskStatus.REJECTED)
            if proposing_agent:
                ReputationEngine.record_policy_violation(proposing_agent,
                    details=f"{decision.violated_rule_id}: {decision.violated_rule_description}", task_id=task.id)
            attempts = self.replan_counts.get(actual_task_id, 0) + 1; self.replan_counts[actual_task_id] = attempts
            if attempts > self.max_replan_attempts:
                if proposing_agent: ReputationEngine.record_task_failure(proposing_agent, credits_allocated=task.allocated_credits, credits_used=0,
                    reason="Max replan attempts exceeded", task_id=task.id)
                StateMachine.transition_task(task, TaskStatus.FAILED); StateMachine.transition_org(self.org, OrgState.FAILED); self._sync_state()
                raise PolicyViolationError(f"Task {actual_task_id} aborted: Max replan attempts ({self.max_replan_attempts}) exceeded. Last violation: {decision.violated_rule_id} - {decision.violated_rule_description}")
            self.event_store.append_event(actor_id="ORCHESTRATOR", event_type="REPLAN_TRIGGERED", entity_id=task.id,
                payload={"attempt": attempts, "max_attempts": self.max_replan_attempts, "violated_rule_id": decision.violated_rule_id,
                         "reason": decision.violated_rule_description})
            StateMachine.transition_task(task, TaskStatus.IN_PROGRESS)
            replanned = self.ceo.replan_after_rejection(task_id=task_id, rejected_proposal=proposal,
                violated_rule_id=decision.violated_rule_id or "RULE-UNKNOWN",
                violated_rule_description=decision.violated_rule_description or "Rejected", attempt_number=attempts)
            self._sync_state(); return self.process_action_proposal(actual_task_id, replanned)
        raise NotImplementedError(f"Handling for decision result {decision.result} not implemented")

    def complete_mission(self) -> MissionReviewOutput:
        StateMachine.transition_org(self.org, OrgState.COMPLETED)
        review = self.ceo.review_mission(self.org.mission, [r.model_dump() for r in self.execution_receipts])
        self.event_store.append_event(actor_id=self.ceo.agent_id, event_type="MISSION_COMPLETED", entity_id=self.org.id,
                                      payload=review.model_dump())
        self._sync_state(); return review
