import uuid
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime

from src.domain.entities import Organisation, Task, ActionProposal, PolicyDecision, ExecutionReceipt, AgentRecord
from src.domain.enums import OrgState, TaskStatus, PolicyResult, AgentRole, ActionType
from src.domain.exceptions import PolicyViolationError, InvalidStateTransitionError, UnauthorizedActionError
from src.governance.policy_engine import PolicyEngine
from src.governance.human_gate import HumanGate
from src.economy.ledger import DoubleEntryLedger, TREASURY
from src.economy.reputation import ReputationEngine
from src.execution.base import BaseExecutor
from src.audit.event_store import AppendOnlyEventStore
from src.audit.auditor import Auditor, VerificationReceipt, AuditVerificationError
from src.orchestration.state_machine import StateMachine
from src.orchestration.assignment import AgentAssignmentEngine
from src.agents.roles.ceo import CEOAgent
from src.agents.roles.researcher import ResearcherAgent
from src.agents.roles.strategist import StrategistAgent
from src.agents.roles.financial_analyst import FinancialAnalystAgent
from src.agents.schemas import ResearchOutput, StrategyOutput, FinancialProposalOutput, MissionReviewOutput
from src.domain.exceptions import LLMOutputValidationError, NoEligibleAgentException

class OrchestrationEngine:
    """
    Deterministic coordinator managing the organizational lifecycle:
    MISSION -> PLAN -> RESEARCH -> PROPOSAL -> POLICY CHECK -> (REPLAN) -> EXECUTE -> AUDIT VERIFY -> REALLOCATE -> COMPLETE
    
    Guarantees:
    - Zero direct execution authority for agents
    - Strict adherence to StateMachine transitions
    - Append-only audit logging for every consequential transition
    - Automatic independent audit verification of every execution
    - Bounded replanning loop (max 3 attempts)
    - Automatic reputation adjustment
    """
    def __init__(
        self,
        org: Organisation,
        ledger: Any,
        policy_engine: PolicyEngine,
        executor: BaseExecutor,
        event_store: Any,
        human_gate: HumanGate,
        ceo: CEOAgent,
        researcher: ResearcherAgent,
        strategist: StrategistAgent,
        financial_analyst: FinancialAnalystAgent,
        auditor: Optional[Auditor] = None,
        repository: Optional[Any] = None,
        max_replan_attempts: int = 3
    ):
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
        self.replan_counts: Dict[str, int] = {}
        self.execution_receipts: List[ExecutionReceipt] = []
        self.verification_receipts: List[VerificationReceipt] = []

    def _sync_state(self) -> None:
        if self.repository:
            self.repository.save_organisation(self.org)
            for t in self.tasks.values():
                self.repository.save_task(t, self.org.id)
            for a in self.org.agents.values():
                self.repository.save_agent(a, self.org.id)

    def start_mission(self) -> None:
        StateMachine.transition_org(self.org, OrgState.PLANNING)
        self.event_store.append_event(
            actor_id="ORCHESTRATOR",
            event_type="MISSION_STARTED",
            entity_id=self.org.id,
            payload={"mission": self.org.mission, "treasury_balance": self.org.treasury_balance}
        )
        self._sync_state()

    def decompose_and_plan(self) -> List[Task]:
        plan_output = self.ceo.create_initial_plan(self.org.mission, self.org.treasury_balance)
        self.event_store.append_event(
            actor_id=self.ceo.agent_id,
            event_type="MISSION_PLAN_CREATED",
            entity_id=self.org.id,
            payload=plan_output.model_dump()
        )

        created_tasks: List[Task] = []
        for item in plan_output.tasks:
            task = Task(
                id=item.task_id,
                mission_id=self.org.id,
                assigned_agent_id=None,
                objective=item.objective,
                allocated_credits=item.allocated_credits,
                status=TaskStatus.PENDING
            )
            # Route and assign via AgentAssignmentEngine if eligible agent exists
            try:
                agent, credits = AgentAssignmentEngine.assign_and_allocate(
                    task=task,
                    org=self.org,
                    required_role=item.assigned_role,
                    requested_credits=item.allocated_credits
                )
            except NoEligibleAgentException:
                # Fallback to static mapping if candidate agent is not registered in org.agents yet
                task.assigned_agent_id = f"agent-{item.assigned_role.value.lower()}"
                StateMachine.transition_task(task, TaskStatus.ASSIGNED)

            self.tasks[task.id] = task
            created_tasks.append(task)

        StateMachine.transition_org(self.org, OrgState.EXECUTING)
        self._sync_state()
        return created_tasks

    def run_intelligence_pipeline(self) -> Tuple[ResearchOutput, StrategyOutput, FinancialProposalOutput]:
        research_task = self.tasks.get("task-01")
        if research_task:
            StateMachine.transition_task(research_task, TaskStatus.IN_PROGRESS)
        
        research = self.researcher.conduct_research("Analyze available investment options")
        self.event_store.append_event(
            actor_id=self.researcher.agent_id,
            event_type="RESEARCH_COMPLETED",
            entity_id="task-01",
            payload=research.model_dump()
        )
        if research_task:
            research_task.output_evidence = research.model_dump()
            StateMachine.transition_task(research_task, TaskStatus.COMPLETED)
            research_agent = self.org.agents.get(self.researcher.agent_id)
            if research_agent:
                ReputationEngine.record_task_success(research_agent)

        strategy_task = self.tasks.get("task-02")
        if strategy_task:
            StateMachine.transition_task(strategy_task, TaskStatus.IN_PROGRESS)

        strategy = self.strategist.evaluate_strategy(research)
        self.event_store.append_event(
            actor_id=self.strategist.agent_id,
            event_type="STRATEGY_EVALUATED",
            entity_id="task-02",
            payload=strategy.model_dump()
        )
        if strategy_task:
            strategy_task.output_evidence = strategy.model_dump()
            StateMachine.transition_task(strategy_task, TaskStatus.COMPLETED)
            strategy_agent = self.org.agents.get(self.strategist.agent_id)
            if strategy_agent:
                ReputationEngine.record_task_success(strategy_agent)

        finance_task = self.tasks.get("task-03")
        if finance_task:
            StateMachine.transition_task(finance_task, TaskStatus.IN_PROGRESS)

        finance = self.financial_analyst.formulate_proposal(strategy)
        self.event_store.append_event(
            actor_id=self.financial_analyst.agent_id,
            event_type="FINANCIAL_PROPOSAL_FORMULATED",
            entity_id="task-03",
            payload=finance.model_dump()
        )

        self._sync_state()
        return research, strategy, finance

    def process_action_proposal(
        self,
        task_id: str,
        proposal: ActionProposal
    ) -> Tuple[PolicyDecision, Optional[ExecutionReceipt]]:
        task = self.tasks[task_id]
        StateMachine.transition_task(task, TaskStatus.SUBMITTED)
        task.proposals.append(proposal)

        if self.repository:
            self.repository.save_proposal(proposal)

        self.event_store.append_event(
            actor_id=proposal.proposing_agent_id,
            event_type="PROPOSAL_SUBMITTED",
            entity_id=proposal.id,
            payload=proposal.model_dump()
        )

        decision = self.policy_engine.evaluate(proposal, self.org, ledger=self.ledger)
        if self.repository:
            self.repository.save_policy_decision(decision)

        self.event_store.append_event(
            actor_id="POLICY_ENGINE",
            event_type="POLICY_EVALUATED",
            entity_id=decision.id,
            payload=decision.model_dump()
        )

        proposing_agent = self.org.agents.get(proposal.proposing_agent_id)

        if decision.result == PolicyResult.APPROVED:
            StateMachine.transition_task(task, TaskStatus.APPROVED)
            
            # Dispatch to deterministic executor
            receipt = self.executor.execute(proposal, decision, self.org)
            self.execution_receipts.append(receipt)

            if self.repository:
                self.repository.save_execution_receipt(receipt)

            self.event_store.append_event(
                actor_id="EXECUTOR",
                event_type="ACTION_EXECUTED",
                entity_id=receipt.id,
                payload=receipt.model_dump()
            )

            # Independent Auditor Verification
            try:
                verification = self.auditor.verify_execution(
                    proposal=proposal,
                    decision=decision,
                    receipt=receipt,
                    org=self.org,
                    task=task,
                    ledger=self.ledger,
                    event_store=self.event_store,
                    policy_engine=self.policy_engine
                )
                self.verification_receipts.append(verification)
            except AuditVerificationError as ave:
                StateMachine.transition_task(task, TaskStatus.FAILED)
                StateMachine.transition_org(self.org, OrgState.FAILED)
                self.event_store.append_event(
                    actor_id="AUDITOR",
                    event_type="AUDIT_FAILED",
                    entity_id=receipt.id,
                    payload={"failures": ave.failures}
                )
                self._sync_state()
                raise ave

            if proposing_agent:
                ReputationEngine.record_task_success(
                    proposing_agent,
                    credits_allocated=task.allocated_credits,
                    credits_used=receipt.cost_credits,
                    value_score=proposal.expected_value_score,
                    task_id=task.id
                )

            StateMachine.transition_task(task, TaskStatus.COMPLETED)
            self._sync_state()
            return decision, receipt

        elif decision.result == PolicyResult.REJECTED:
            StateMachine.transition_task(task, TaskStatus.REJECTED)
            if proposing_agent:
                ReputationEngine.record_policy_violation(
                    proposing_agent,
                    details=f"{decision.violated_rule_id}: {decision.violated_rule_description}",
                    task_id=task.id
                )

            attempts = self.replan_counts.get(task_id, 0) + 1
            self.replan_counts[task_id] = attempts

            if attempts > self.max_replan_attempts:
                if proposing_agent:
                    ReputationEngine.record_task_failure(
                        proposing_agent,
                        credits_allocated=task.allocated_credits,
                        credits_used=0,
                        reason="Max replan attempts exceeded",
                        task_id=task.id
                    )
                StateMachine.transition_task(task, TaskStatus.FAILED)
                StateMachine.transition_org(self.org, OrgState.FAILED)
                self._sync_state()
                raise PolicyViolationError(
                    f"Task {task_id} aborted: Max replan attempts ({self.max_replan_attempts}) exceeded. "
                    f"Last violation: {decision.violated_rule_id} - {decision.violated_rule_description}"
                )

            self.event_store.append_event(
                actor_id="ORCHESTRATOR",
                event_type="REPLAN_TRIGGERED",
                entity_id=task_id,
                payload={
                    "attempt": attempts,
                    "max_attempts": self.max_replan_attempts,
                    "violated_rule_id": decision.violated_rule_id,
                    "reason": decision.violated_rule_description
                }
            )

            # Replan transition: REJECTED -> IN_PROGRESS
            StateMachine.transition_task(task, TaskStatus.IN_PROGRESS)
            replanned_proposal = self.ceo.replan_after_rejection(
                task_id=task_id,
                rejected_proposal=proposal,
                violated_rule_id=decision.violated_rule_id or "RULE-UNKNOWN",
                violated_rule_description=decision.violated_rule_description or "Rejected",
                attempt_number=attempts
            )

            self._sync_state()
            # Re-evaluate replanned proposal recursively
            return self.process_action_proposal(task_id, replanned_proposal)

        else:
            raise NotImplementedError(f"Handling for decision result {decision.result} not implemented")

    def complete_mission(self) -> MissionReviewOutput:
        StateMachine.transition_org(self.org, OrgState.COMPLETED)
        
        history = [r.model_dump() for r in self.execution_receipts]
        review = self.ceo.review_mission(self.org.mission, history)
        
        self.event_store.append_event(
            actor_id=self.ceo.agent_id,
            event_type="MISSION_COMPLETED",
            entity_id=self.org.id,
            payload=review.model_dump()
        )
        self._sync_state()
        return review
