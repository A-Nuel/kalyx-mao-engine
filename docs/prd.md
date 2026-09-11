AUTONOMOUS ORG — PRD

Autonomous Organisation Infrastructure • Build Week Prototype • September 2026

1. Vision

Autonomous software will evolve from isolated assistants into organisations that coordinate intelligence, labour and capital. The product provides the control and economic layer needed to make those organisations useful, accountable and safe.

2. Problem

Current agent systems can reason and use tools, but organisational primitives remain fragmented: delegation, budgets, authority, reputation, accountability, verification and replacement are usually handled by prompts or application glue.

3. Product

A programmable operating layer where AI agents operate as a governed workforce. A human defines the mission and constraints; the organisation plans, delegates, allocates resources, executes permitted actions and audits itself.

4. Target Users

Developers building autonomous businesses.

Teams operating fleets of specialised agents.

AI-native startups that need agent permissions and treasury controls.

Researchers studying multi-agent economics and organisational behaviour.

5. Primary User Story

As an operator, I give an autonomous organisation a mission, a finite budget and policies. I want it to perform useful work without giving any individual model unrestricted authority, and I want to understand exactly why each consequential action happened.

6. MVP User Flow

Create organisation.

Enter mission and budget.

Select organisation policy/risk profile.

CEO creates plan.

Research and strategy agents produce evidence and proposals.

Risk/policy layer approves or rejects proposals.

Executor performs approved controlled action.

Auditor verifies outcome.

Organisation updates agent scores/resources.

Operator reviews final mission report and audit trail.

7. Functional Requirements

Mission creation and constraints.

Agent provisioning with role, budget and authority.

Task delegation and structured responses.

Deterministic budget/permission/risk checks.

Proposal approval/rejection with reasons.

Controlled execution.

Independent audit and evidence capture.

Agent scoring and lifecycle management.

Resource reallocation.

Full event history and replayable decision trail.

Human pause/approve/override controls.

8. Agent Economy Requirements

The prototype should test whether scarce, performance-linked resources improve organisational efficiency. Rewards must be bounded and cannot override policy. Survival is never an optimisation target; mission performance and policy compliance are.

9. Success Metrics

≥80% successful completion on curated benchmark missions.

0 policy-bypass executions in adversarial tests.

100% of consequential actions produce an audit event.

Demonstrable reduction in resource allocation to consistently poor-performing agents.

A complete demo mission can be understood by a first-time viewer in under three minutes.

10. Competitive Position

Do not compete as another swarm, trading bot, registry, wallet or agent marketplace. Position around organisational control: resource allocation + authority + reputation + policy enforcement + auditability.

11. Roadmap

Phase 1: Minimum Autonomous Organisation.

Phase 2: real Web2/Web3 execution adapters and treasury.

Phase 3: agent reputation and interoperable identity.

Phase 4: agent-to-agent hiring, commerce and services.

Phase 5: network of autonomous organisations.

12. Risks

Model hallucination → require evidence and structured outputs.

Reward hacking → bounded scoring and independent evaluation.

Collusion → independent auditor and role separation.

Over-spending → deterministic budgets.

Prompt injection → tool isolation, provenance and policy checks.

False autonomy → measure actual execution, not chat quality.