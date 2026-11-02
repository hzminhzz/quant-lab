# Four-XS4H post-test selection ledger reconciliation — 2026-11-02

This is an **append-only addendum** to
`docs/FOUR-XS4H-EXECUTION-20261102.md` and immutable
`.workspace/campaigns/four-xs4h/evidence.json` (which correctly
recorded the ledger **at completion of the research campaign**).

## Why the later local selection-attempt count is 236, not 234

The inherited source snapshot contained **149 hypotheses, 75 trials,
230 selection attempts**. Warden registered four new Stage-1
attempts, one Stage-3 trial; the exported research evidence therefore
had **153 hypotheses, 76 trials, 234 selection attempts**.

A later broad `pytest` run against that isolated worktree added
**two Warden failed-reservation events** to the local append-only
ledger, after the frozen research experiment had completed:

| Attempt ID | Hypothesis | Stage | Outcome | Evidence |
|---|---|---|---|---|
| `ATT-08f622aa290fb1f3-043` | `HYP-2026-001` | `STAGE_0` | `ERROR` | `Hypothesis HYP-2026-001 budget exhausted (3/3)` |
| `ATT-08f622aa290fb1f3-044` | `HYP-2026-001` | `STAGE_0` | `ERROR` | Same exhausted-budget guard |

Both contain `returns_observed=0`. These are gate-refusal
test side effects, **not legitimate new market screens or full trials**.
They must **remain recorded** for later multiple-attempt accounting,
not be deleted or used to claim a signal.

Final post-test isolated ledger: **153 hypotheses / 76 trials / 236
selection attempts**. Authoritative primary checkout remains
**149 / 75 / 230**, verified read-only after the suite.

This addendum does not change any Stage-1 metric, Stage-3 negative
verdict, Warden protected evaluator/contract, immutable evidence
snapshot, or qualification status. The scientific conclusion remains
**one gross predictive sign-reversal Stage-1 pass, zero net-qualified
strategies**.

Full test suite excluding missing untracked daily-contract suite:
**256 passed**. Additional focused snapshot isolation tests:
**19 passed**, Ruff and `warden check-worktree` pass.
