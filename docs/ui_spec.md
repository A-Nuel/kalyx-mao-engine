AUTONOMOUS ORG — UI DESIGN SPEC

Autonomous Organisation Infrastructure • Build Week Prototype • September 2026

1. Design Goal

Make an invisible machine-organisational process visually obvious. The interface should feel like mission control for a living autonomous organisation: technical, serious, calm, information-dense without becoming a crypto dashboard.

2. Visual Direction

Dark command-centre aesthetic with restrained electric-blue accents and neutral surfaces.

No neon overload, casino/crypto styling or cartoon agent avatars.

Use status, telemetry, evidence and event chronology as the primary visual language.

Animations communicate state transitions, not decoration.

3. Information Architecture

Overview

Mission

Organisation

Agents

Treasury & Credits

Policies

Activity / Audit

Experiments

Settings

4. Overview Screen

Mission objective and completion percentage.

Treasury/credits remaining and burn rate.

Organisation status.

Agent roster with status, reputation, budget and authority.

Live event stream.

Pending approvals.

Recent policy decisions.

Current bottleneck / risk.

5. Organisation Screen

Visual graph: HUMAN → CEO → SPECIALISTS → RISK/POLICY → EXECUTION → AUDITOR. Clicking an agent opens its resource, authority and performance profile.

6. Agent Profile

Role and model.

Current status.

Budget and burn rate.

Reputation trend.

Success rate.

Resource efficiency.

Allowed tools/actions.

Authority ceiling.

Recent tasks.

Reasons for promotions/demotions.

Retire/restrict controls where permitted.

7. Mission Console

Mission text.

Constraints.

Budget.

Risk profile.

Current plan.

Task graph.

Evidence gathered.

Proposals awaiting decision.

Final outcome.

8. Policy Centre

Show rules in human-readable form: maximum spend, allowed assets/destinations, required approvals, risk threshold, tool permissions and emergency stop. Every rejection should expose the exact rule that blocked it.

9. Audit View

Timeline of consequential actions. Each row answers: WHO acted? WHAT was proposed? WHY? WHAT POLICY applied? WAS IT approved? WHAT executed? WHAT was the outcome? WHAT evidence supports the result?

10. Agent Economy View

Credits distributed by agent.

Performance vs resource consumption.

Reputation changes.

Authority changes.

Promotion/demotion events.

Agents at risk of restriction.

Counterfactual experiment controls for static vs dynamic allocation.

11. Critical Interaction

When a proposal is rejected, show the rejection as a visible system event: PROPOSAL → POLICY CHECK → REJECTED → REASON → REPLAN. When approved: PROPOSAL → POLICY CHECK → APPROVED → EXECUTED → VERIFIED.

12. Demo Mode

A guided 3-minute mode should automatically surface the most important events while retaining access to the full system. The viewer should see one intelligent collaboration, one safety rejection, one successful execution and one measurable agent-economy update.

13. Responsive Requirements

Desktop-first for Build Week demo.

Tablet usable.

Mobile should prioritise mission, agent status, approvals and activity.

Avoid giant graphs that become unreadable on smaller screens.

14. Accessibility

Never communicate status by colour alone.

Keyboard-accessible controls.

Readable numerical formatting.

Clear confirmation for irreversible actions.

Plain-language policy explanations.