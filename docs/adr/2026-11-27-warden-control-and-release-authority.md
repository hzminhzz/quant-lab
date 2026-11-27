# ADR: deepen Warden control and separate release authority

Status: **PROPOSED FOR IMPLEMENTATION**. Date: 2026-11-27.
The principal requested an agent-intuitive, ergonomic and accretive system design
and corresponding document changes. This ADR selects a target architecture; it
does not grant standing release authority or authorize production effects.

## Context

#109 removed the need to guess static data contracts by introducing semantic
requirements and a policy-derived compiler. #119 added prospective intent,
candidate/operation accounting, independent conformance, benchmark-relative
evidence, novelty and exposure governance.

The remaining caller burden is systemic: reconstructing current state, choosing
a legal next action, understanding whether an interruption spent evidence,
distinguishing an active runtime from a dirty checkout, and preserving useful
knowledge without importing stale authority. The manifest couples changing source
inventory to approval, creating repeated operator mechanics.

The principal operates through ChatGPT and does not write code or run CLI commands.
The existing Warden/ML4T/SQLite implementation and scientific history are valuable
and must survive the change. Current AGENTS.md retains principal ownership of
evaluator approval and protected actions.

## Decision

Deepen Warden's application interface around bounded situation views, prepared
typed actions and durable operation receipts. Keep scientific admission,
evaluation, accounting and exposure in their current domain owners. All adapters
share that implementation.

Separate immutable build identity, verification evidence and release authorization.
Use a protected Release Authority with an authenticated principal-decision path
and explicitly scoped standing grants where the principal chooses them.

Accumulate evidence-linked findings, regressions and remedies through existing
sources and rebuildable projections. Read context by task and relevance while
retaining complete mandatory novelty/history checks.

Specify behavior in [control contracts](../AGENT-CONTROL-CONTRACTS.md) and deliver
through the [dependency plan](../AGENT-CONTROL-PLAN.md). Keep development,
engineering verification, scientific qualification and deployment separate.

## Alternatives considered

| Interface/design | Depth and locality | Agent/resource cost | Migration/test implications | Disposition |
|---|---|---|---|---|
| Richer prompts and runbooks around individual CLI commands | Ordering and error semantics stay spread across callers | Low implementation cost, repeated context assembly and manual recovery | Existing tools reusable; integration mistakes remain caller-dependent | Retain as current procedure; insufficient target |
| One opaque “research this objective” tool | Small signature but hides permission, partial effects and stopping semantics | Few calls, expensive ambiguous recovery and poor controllability | Hard to demonstrate exactly which effect was admitted | Reject as the sole interface; allow bounded objective progression over typed actions |
| Typed control module over existing domain operations, with separate release authority | Small interface hides ordering/recovery; science and authority remain local to their owners | Bounded context and reusable evidence; modest adapter/migration work | Observable contract tests; additive rollout and legacy parity | **Chosen** |
| New event-sourced orchestrator, workflow platform and memory/graph stores | More sources of state and transition ownership | Extra infrastructure and synchronization cost before user value | High migration risk and duplicated debt/exposure semantics | Reject absent a measured need |
| Automatically regenerate/sign every changed worktree after its own tests pass | Signing mechanics disappear while proposer controls evidence and approval | Small immediate implementation, weak authority separation | Candidate can weaken gates/tests; no protected admission predicate | Reject |

## Consequences

Positive: a new session can understand the situation without replaying a long
transcript; an action advertises effects and authority; duplicate submission
retrieves receipts; routine release mechanics fit explicit standing policy; failed
research and successful repairs improve later decisions.

Costs: result schemas, idempotency and compatibility require disciplined design.
A protected authority and real authenticated decision adapter require explicit
bootstrap capability. Principal review remains necessary for decisions outside
the delegated scope. Tests do not establish semantic equivalence or trading edge.

Preserved constraints: one canonical scientific registry/migration; immutable
history; no budget refunds; no free market-scoring preflight; exact data/code
binding; fixed benchmarks; spent holdout claims; existing independent conformance;
current CLI/evidence compatibility; direct execution without mandatory delegation.

## Trust and rollout

Application controls do not prevent arbitrary same-UID raw-data computation or
ledger writes. Isolate signing/activation first and document remaining access.
Claim stronger worker isolation only after actual permission tests. Knowledge and
an agent's statement about a human decision never mint authority.

Prototype read-only observation first. Build immutable candidates next; integrate
authenticated decisions and scoped release admission before claiming that the
manifest friction is solved. Rehearse migration/rollback on isolated state.
Activate only under separate explicit authority.

Open facts: actual protected installer/decision channel, granted release classes,
active runtime/ledger compatibility and production-scale resource baselines.
These remain implementation prerequisites, not invented approvals.

## Related concepts and evidence

- [Glossary](../../CONTEXT.md): intent, candidate, lineage, selection group,
  exposure, receipt, finding, release authorization.
- [Architecture](../ARCHITECTURE.md): abstraction tower and operating loop.
- [#109](https://github.com/hzminhzz/quant-lab/issues/109) and
  [#119](https://github.com/hzminhzz/quant-lab/issues/119): accepted scientific scope.
- [Current integrity](../../src/warden_ops/integrity.py),
  [CLI](../../src/warden/cli.py), [read model](../../src/warden_ops/readonly.py),
  [checkpoint](../../src/warden_ops/checkpoint.py) and
  [MCP adapter](../../scripts/mcp_warden/adapter.py): inspected implementation seams.
