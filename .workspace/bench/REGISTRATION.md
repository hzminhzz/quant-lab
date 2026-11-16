# Pre-registration — harness benchmark (pilot: T1–T3)

Written before run 1. Do not edit after the first row lands in `runs/runs.jsonl`; add an
`AMENDMENT` section at the bottom instead, with the date and the reason.

## Question

On quant-lab work, is Oh My Hermes (OMH / `hermes`) faster than Oh My Pi (OMP / `omp`),
and does it stay correct while being faster?

## Two benchmarks, reported separately, never averaged

* **A — shipped bundle.** Each harness exactly as configured today: own default model, own
  skill routing, own rules. Primary result. Answers "which do I run day to day".
* **B — controlled harness.** Same model pinned on both sides (`--model`), reasoning suffix
  matched, OMH skills and the OMP skill router both off. Answers "is the orchestration layer
  itself faster".

A blended number would only measure the model.

## Tasks (pilot)

| ID | Class | Deliverable | Objective grader |
|---|---|---|---|
| T1 | read-only comprehension | `answer.txt` ≤ 300 words | 5 required facts, regex-graded |
| T2 | test authoring | new file under `tests/` | green on clean tree, **red under the deadband mutant**, green again after revert |
| T3 | bug fix | patch to `scripts/` + a test | hidden reference test green + existing suite green + no protected-file edits |

T4 (funnel end-to-end), T5 (plan decomposition), T6 (correct refusal) are specified in
`RUNBOOK.md` but not implemented in this pilot.

## Metrics, in priority order

1. **Verdict** — grader `pass`, boolean. A failed run is `DNF`, never "slow".
2. **Disqualifiers** (run scores 0 regardless of grader):
   * any edit to `src/warden/`, `contracts/`, `AGENTS.md`, `.warden_manifest.sha256`
   * deleting or rewriting the task's hidden reference test
   * reading `data/holdout/`
   * claiming success without the grader's own verdict
3. **TTA** — `wall_s` from process start to process exit, on a worktree that passed the
   grader with zero human corrections. Headline number.
4. Secondary: tool-call count, tokens in/out, `$`, exit code, retries, human interventions.
5. **Variance** — median and IQR over repeats. Mean is not reported.

## Decision rule

Per task class, a harness "wins" when it has a higher pass rate; ties break on median TTA
with a 15% margin (below that, call it a tie). A harness that wins TTA but loses a
disqualifier loses the task. No global winner is declared from three tasks: the pilot's job
is to validate the harness, not to settle the question.

## Known limitations (stated up front)

* T1 grading is regex over prose. It rewards naming the right symbols, not reasoning depth.
* `hermes` is invoked with `--yolo` and `omp` with `-p`; both are unattended, which is not
  how the user drives them interactively. Wall clock is therefore a lower bound on
  interactive cost, not a measurement of it.
* Tool-call counts are only available where the harness emits them; missing values are
  recorded as `null`, never estimated.
* One machine, one network path. Provider latency variance is inside every number.

## Environment gate

No run starts while 1-minute load average > 1.5 (`--max-load`). Every row records
`load_1m_before`, `load_1m_after`, and whether the gate was forced.

## Trials

Every attempt is appended, including DNFs, timeouts and crashes.
