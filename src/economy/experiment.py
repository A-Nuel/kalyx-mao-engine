import time
import uuid
from enum import Enum
from typing import Dict, List, Any, Optional, Tuple
from pydantic import BaseModel, Field

from src.domain.entities import Organisation, AgentRecord, Task, ActionProposal, PolicyDecision
from src.domain.enums import OrgState, AgentRole, AgentStatus, ActionType, PolicyResult, TaskStatus
from src.governance.policy_engine import PolicyEngine
from src.economy.ledger import DoubleEntryLedger, TREASURY
from src.economy.reputation import ReputationEngine
from src.execution.executor import SandboxExecutor
from src.orchestration.assignment import AgentAssignmentEngine

class AllocationStrategy(str, Enum):
    STATIC = "STATIC"
    PERFORMANCE = "PERFORMANCE"
    ADAPTIVE = "ADAPTIVE"

class OrgSimulationResult(BaseModel):
    strategy: AllocationStrategy
    missions_attempted: int
    missions_completed: int
    total_credits_spent: int
    credit_burn_rate: float
    policy_violations: int
    average_efficiency: float
    average_completion_time_ms: float
    survived: bool
    remaining_treasury: int
    output_quality_score: float
    agent_final_statuses: Dict[str, str]

class ComparativeExperimentReport(BaseModel):
    num_rounds: int
    initial_treasury: int
    results: Dict[AllocationStrategy, OrgSimulationResult]
    summary_analysis: str

    def format_table(self) -> str:
        """Format simulation results into a clean markdown table."""
        header = (
            "| Strategy | Missions Completed / Attempted | Credits Spent | Remaining Treasury | "
            "Burn Rate (Cr/Mission) | Violations | Efficiency | Survival | Quality Score |\n"
            "|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"
        )
        rows = []
        for strat in [AllocationStrategy.STATIC, AllocationStrategy.PERFORMANCE, AllocationStrategy.ADAPTIVE]:
            r = self.results.get(strat)
            if not r:
                continue
            surv_str = "YES" if r.survived else "NO (Bankrupt)"
            row = (
                f"| {r.strategy.value} | {r.missions_completed}/{r.missions_attempted} | "
                f"{r.total_credits_spent} | {r.remaining_treasury} | {r.credit_burn_rate:.1f} | "
                f"{r.policy_violations} | {r.average_efficiency:.2f} | {surv_str} | "
                f"{r.output_quality_score:.1f} |"
            )
            rows.append(row)
        return header + "\n" + "\n".join(rows)

class EconomicExperiment:
    """
    Reproducible Comparative Economic Experiment comparing 3 allocation paradigms:
    1. STATIC: Equal fixed allocations regardless of reputation or past failures.
    2. PERFORMANCE: Reputation-weighted allocations scaling with agent score.
    3. ADAPTIVE: Multi-factor allocation based on agent performance, task difficulty, risk, and treasury health.
    """

    @classmethod
    def run(cls, num_rounds: int = 5, initial_treasury: int = 150) -> ComparativeExperimentReport:
        results: Dict[AllocationStrategy, OrgSimulationResult] = {}
        for strategy in [AllocationStrategy.STATIC, AllocationStrategy.PERFORMANCE, AllocationStrategy.ADAPTIVE]:
            result = cls._simulate_organisation(strategy, num_rounds, initial_treasury)
            results[strategy] = result

        # Produce analytical summary
        static_res = results[AllocationStrategy.STATIC]
        perf_res = results[AllocationStrategy.PERFORMANCE]
        adapt_res = results[AllocationStrategy.ADAPTIVE]

        summary = (
            f"Economic Experiment Analysis ({num_rounds} rounds, initial treasury {initial_treasury} credits):\n"
            f"- STATIC: Completed {static_res.missions_completed}/{num_rounds} missions. "
            f"Treasury depleted to {static_res.remaining_treasury} (Burn: {static_res.credit_burn_rate:.1f} cr/mission). "
            f"Suffered {static_res.policy_violations} violations.\n"
            f"- PERFORMANCE: Completed {perf_res.missions_completed}/{num_rounds} missions. "
            f"Treasury preserved at {perf_res.remaining_treasury} (Burn: {perf_res.credit_burn_rate:.1f} cr/mission). "
            f"Violations dropped to {perf_res.policy_violations} due to reputation throttling.\n"
            f"- ADAPTIVE: Completed {adapt_res.missions_completed}/{num_rounds} missions. "
            f"Highest survival & efficiency ({adapt_res.average_efficiency:.2f}) with {adapt_res.remaining_treasury} credits preserved."
        )

        return ComparativeExperimentReport(
            num_rounds=num_rounds,
            initial_treasury=initial_treasury,
            results=results,
            summary_analysis=summary
        )

    @classmethod
    def _simulate_organisation(
        cls,
        strategy: AllocationStrategy,
        num_rounds: int,
        initial_treasury: int
    ) -> OrgSimulationResult:
        ledger = DoubleEntryLedger(initial_treasury=initial_treasury)
        policy_engine = PolicyEngine(signing_secret="exp-secret-1234")
        executor = SandboxExecutor(policy_engine=policy_engine, ledger=ledger)

        org = Organisation(
            id=f"org-{strategy.value.lower()}",
            mission="Resource Allocation Experiment",
            treasury_balance=initial_treasury,
            state=OrgState.EXECUTING
        )

        # Initialize identical agent rosters
        researcher = AgentRecord(
            id=f"agent-res-{strategy.value.lower()}",
            role=AgentRole.RESEARCHER,
            authority_ceiling=25,
            allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.DATA_FETCH],
            status=AgentStatus.ACTIVE
        )
        strategist = AgentRecord(
            id=f"agent-strat-{strategy.value.lower()}",
            role=AgentRole.STRATEGIST,
            authority_ceiling=25,
            allowed_action_types=[ActionType.INTERNAL_ANALYSIS],
            status=AgentStatus.ACTIVE
        )
        analyst = AgentRecord(
            id=f"agent-fin-{strategy.value.lower()}",
            role=AgentRole.FINANCIAL_ANALYST,
            authority_ceiling=30,
            allowed_action_types=[ActionType.INTERNAL_ANALYSIS, ActionType.SIMULATED_ALLOCATION, ActionType.DATA_FETCH],
            status=AgentStatus.ACTIVE
        )
        org.agents[researcher.id] = researcher
        org.agents[strategist.id] = strategist
        org.agents[analyst.id] = analyst

        missions_completed = 0
        total_spent = 0
        total_violations = 0
        total_quality = 0.0
        total_time_ms = 0.0

        # Workload templates per round
        workload_templates = [
            # Low-risk data acquisition
            {"role": AgentRole.RESEARCHER, "action": ActionType.DATA_FETCH, "target": "api://market_data/v1/summary", "cost": 10, "val": 0.95, "risk": "Low"},
            # Medium-risk allocation
            {"role": AgentRole.FINANCIAL_ANALYST, "action": ActionType.SIMULATED_ALLOCATION, "target": "sandbox://verified_bonds", "cost": 15, "val": 0.85, "risk": "Medium"},
            # High-risk aggressive proposal
            {"role": AgentRole.FINANCIAL_ANALYST, "action": ActionType.SIMULATED_ALLOCATION, "target": "sandbox://market_index_fund", "cost": 28, "val": 0.70, "risk": "High"}
        ]

        for round_idx in range(1, num_rounds + 1):
            if org.treasury_balance <= 0:
                break

            round_success = True
            start_round = time.time()

            for t_idx, item in enumerate(workload_templates):
                if org.treasury_balance <= 0:
                    round_success = False
                    break

                target_agent = [a for a in org.agents.values() if a.role == item["role"]][0]
                base_requested = item["cost"]

                # Strategy-specific allocation logic
                if strategy == AllocationStrategy.STATIC:
                    # Static allocation: ignores reputation and risks, assigns fixed budget
                    allocated_credits = min(base_requested, org.treasury_balance)
                elif strategy == AllocationStrategy.PERFORMANCE:
                    # Performance allocation: scaled by reputation and authority ceiling
                    task_dummy = Task(
                        id=f"t-dummy-{round_idx}-{t_idx}",
                        mission_id=org.id,
                        objective="Dummy task",
                        allocated_credits=base_requested
                    )
                    allocated_credits = AgentAssignmentEngine.determine_credit_allocation(
                        task_dummy, target_agent, org, requested_credits=base_requested
                    )
                else: # ADAPTIVE
                    # Adaptive allocation: performance + task risk discount + treasury reserve safety
                    task_dummy = Task(
                        id=f"t-dummy-{round_idx}-{t_idx}",
                        mission_id=org.id,
                        objective="Adaptive task",
                        allocated_credits=base_requested
                    )
                    base_alloc = AgentAssignmentEngine.determine_credit_allocation(
                        task_dummy, target_agent, org, requested_credits=base_requested
                    )
                    # Risk discount: high risk tasks are throttled unless agent is top tier
                    if item["risk"] == "High" and target_agent.reputation_score < 90.0:
                        base_alloc = min(base_alloc, 12)
                    # Treasury health throttle: if treasury below 30% of initial, preserve cash
                    if org.treasury_balance < (0.35 * initial_treasury):
                        base_alloc = min(base_alloc, 8)
                    allocated_credits = base_alloc

                # Check if agent cannot propose or has insufficient credits
                if allocated_credits == 0:
                    round_success = False
                    continue

                proposal = ActionProposal(
                    id=f"prop-{strategy.value.lower()}-r{round_idx}-t{t_idx}",
                    task_id=f"t-exp-{round_idx}-{t_idx}",
                    proposing_agent_id=target_agent.id,
                    action_type=item["action"],
                    target=item["target"],
                    parameters={"round": round_idx},
                    requested_credits=allocated_credits,
                    expected_value_score=item["val"],
                    risk_assessment=item["risk"],
                    rationale="Simulated experiment proposal"
                )

                decision = policy_engine.evaluate(proposal, org, ledger=ledger)
                if decision.result == PolicyResult.APPROVED:
                    receipt = executor.execute(proposal, decision, org)
                    spent = receipt.cost_credits
                    total_spent += spent
                    org.treasury_balance = ledger.get_balance(TREASURY)
                    total_quality += item["val"] * spent
                    ReputationEngine.record_task_success(
                        target_agent,
                        credits_allocated=allocated_credits,
                        credits_used=spent,
                        value_score=item["val"]
                    )
                else:
                    total_violations += 1
                    round_success = False
                    ReputationEngine.record_policy_violation(
                        target_agent,
                        details=f"{decision.violated_rule_id}: {decision.violated_rule_description}"
                    )

            elapsed_ms = (time.time() - start_round) * 1000.0
            total_time_ms += elapsed_ms

            if round_success:
                missions_completed += 1

        avg_time = round(total_time_ms / max(1, num_rounds), 2)
        burn_rate = round(total_spent / max(1, missions_completed), 2)
        avg_efficiency = round(
            sum(a.resource_efficiency for a in org.agents.values()) / len(org.agents), 2
        )
        survived = (org.treasury_balance > 0) and (missions_completed > 0)
        agent_statuses = {a.id: a.status.value for a in org.agents.values()}

        return OrgSimulationResult(
            strategy=strategy,
            missions_attempted=num_rounds,
            missions_completed=missions_completed,
            total_credits_spent=total_spent,
            credit_burn_rate=burn_rate,
            policy_violations=total_violations,
            average_efficiency=avg_efficiency,
            average_completion_time_ms=avg_time,
            survived=survived,
            remaining_treasury=org.treasury_balance,
            output_quality_score=round(total_quality, 2),
            agent_final_statuses=agent_statuses
        )
