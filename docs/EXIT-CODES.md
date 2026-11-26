# Exit codes

One vocabulary, shared by the agent-facing write and resolve commands. A caller branches on
the number: `2` means "your request is not acceptable", `3` means "the request is fine and
the answer is no data", `1` means "it ran and the evidence was refused". Do not renumber an
existing code — agents and cron wrappers already branch on `2` versus `3`.

| code | meaning | emitted by |
|---|---|---|
| **0** | success. Includes "nothing to compile" (`data compile` found a reusable signed contract) and "already reused" (an identical artifact was re-verified byte for byte). | `resolve-data`, `data compile`, `promote-contract`, and every read-only command |
| **1** | the command ran and refused the **evidence**: a fence that cannot be held, a write-once artifact whose bytes moved, a quality gate, a tampered or mismatched artifact on promotion, a failed screen/verify/backtest gate. Also `advance`'s review-required outcome. | `data compile`, `promote-contract`, `verify`, `screen`, `backtest`, `advance` |
| **2** | the **request** is refused or its input is untrusted: an invalid requirement, a policy-owned override, a missing/untrusted venue policy, a stale worktree manifest, an archived contract, a selection group that contradicts the policy, a tampered sandbox contract, a human-only gate that was not confirmed. | every command |
| **3** | the requirement is **not satisfiable** — the lake cannot serve it (missing symbol, insufficient history, unmapped policy domain). Distinct from 2 so a caller can retry after fixing its request instead of after fixing its data. | `resolve-data`, `data compile` |
| **75** | the mutation lease is busy: another writer holds it. Retry later; nothing was decided. | every mutating command (`advance`, `register-hypothesis`, `verify`, …) |

## Documented exceptions

- **`data build` returns 1 for input and parse errors** (`DatasetSpecError`, `ValueError`),
  where `data compile` returns 2 for the same class. That is legacy behaviour kept on
  purpose: `data build` predates the vocabulary and its callers treat 1 as "the build
  failed". New commands must use 2.
- **`resolve-data` never returns 1**: it writes nothing, so it has no evidence to refuse.
- **`data compile` returns 0 for "nothing to compile"**, because a reusable signed contract
  is a successful answer, not a refusal.

## Where each code is produced

| command | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| `resolve-data` | reuse or compile-required | — | invalid requirement, refused input | gap |
| `data compile` | compiled / reused / nothing to compile | fence, write-once, quality gate | override, untrusted policy, invalid input | gap |
| `promote-contract` | promoted | artifact evidence refused (tampered, mismatched, stale policy/fence) | human gate, unknown/archived/already-promoted contract | — |
| `verify` / `screen` / `backtest` | the gate ran | the gate refused | untrusted request or tampered sandbox contract | — |

The pins live in `tests/test_spec109_exit_codes.py`: one parametrised CLI test asserts the
number each command produces for its documented situation, so a command that invents a
fifth code (or reshuffles 2 and 3) fails the suite.
