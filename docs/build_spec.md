AUTONOMOUS ORG — BUILD SPEC

Autonomous Organisation Infrastructure • Build Week Prototype • September 2026

1. Product Thesis

Build an operating layer for autonomous organisations: systems that can receive a mission, coordinate specialised AI agents, allocate scarce resources, execute approved Web2/Web3 actions, and remain accountable through deterministic policy and independent verification.

2. Core Principle

Agents propose. Policies authorize. Executors execute. Auditors verify.

3. MVP Objective

Demonstrate a Minimum Autonomous Organisation (MAO): give it a mission and finite credits; it creates a plan, delegates work, evaluates proposals, enforces policy, executes controlled actions, audits outcomes, and dynamically reallocates resources based on performance.

4. Architecture

Human layer: mission, constraints, intervention, emergency stop.

CEO/Orchestrator: decomposes mission, delegates tasks, replans.

Specialists: Researcher, Strategist, Finance/Allocator, Risk Officer, Executor, Auditor.

Organisation Ledger: agent identity, budgets, reputation, authority, performance, history.

Policy Engine: deterministic limits on spend, tools, destinations, risk, approvals and mission scope.

Execution adapters: Web search/APIs first; controlled/simulated financial actions; Robinhood Chain adapter where useful.

Audit/Event Store: append-only action records, evidence, policy decision, execution result and outcome.

Memory: mission state, decisions, evidence and lessons; never used as an authority bypass.

5. Execution State Machine

MISSION → PLAN → RESEARCH → PROPOSAL → POLICY CHECK → APPROVE/REJECT → EXECUTE → VERIFY → SCORE → REALLOCATE → CONTINUE/COMPLETE

6. Agent Economy

Credits: internal scarce resource used for compute, tools and approved actions.

Reputation: historical reliability score, not a cosmetic XP number.

Authority: permitted action classes and maximum spend/risk.

Performance: outcome quality relative to cost and mission objective.

Lifecycle: ACTIVE → PROBATION → RESTRICTED → SUSPENDED → RETIRED.

Reward: successful, policy-compliant, resource-efficient agents can receive more credits/authority.

Penalty: repeated failure or policy violations reduce resources/authority.

Replacement: the organisation can retire weak agents and provision replacements.

7. Safety Model

LLMs output structured intents, never unrestricted execution commands.

Policy engine validates deterministic constraints before execution.

High-risk actions require explicit human approval in the prototype.

Independent auditor verifies execution and outcome; auditor cannot edit its own evidence.

Emergency kill switch can pause all execution.

Least privilege: each agent receives only the tools, budget and authority required for its role.

Never optimise survival as the primary objective. Resource scarcity is an organisational constraint, not an emotional/desperation mechanism.

8. Data Objects

Organisation: id, mission, treasury, policies, agents, state, created_at

Agent: id, role, model, budget, authority, reputation, performance, risk_score, status

Task: id, owner, objective, budget, deadline, evidence, status

Proposal: agent, action, cost, expected_value, risk, rationale, evidence

PolicyDecision: proposal_id, rules_checked, result, reason, approver

Execution: action, target, transaction/API reference, result, timestamp

Outcome: success, value_created, cost, evidence, evaluator_score

AuditEvent: actor, event, payload_hash, policy, result, timestamp

9. Evaluation

Mission success rate.

Value created per credit.

Unnecessary action rate.

Policy violation rate.

Unsafe proposal rejection rate.

Recovery/replanning success.

Agent resource efficiency.

Comparison of static allocation vs performance-based allocation.

10. Two-Week Build Plan

Days 1–2: repository, schemas, event model, organisation state machine.

Days 3–4: CEO orchestration, specialist agents, structured outputs.

Days 5–6: policy engine, budgets, permissions, kill switch.

Days 7–8: agent economy, reputation, scoring, lifecycle/replacement.

Days 9–10: execution adapters and controlled Robinhood Chain integration.

Days 11–12: auditor, replayable audit trail, failure recovery.

Day 13: dashboard and demo flow.

Day 14: testing, adversarial cases, performance tuning, final demo.

11. Non-Goals

Autonomous real-money trading as the core product.

Unlimited agent self-modification.

A generic agent marketplace.

A token launch solely for the prototype.

Putting all state or reasoning onchain.

Allowing an LLM to bypass deterministic authorization.

12. Definition of Done

A judge can provide a mission and finite budget, observe agents collaborate, see a risky action rejected, see a safe action executed, inspect the audit trail, and observe at least one resource/reputation change caused by measured performance.