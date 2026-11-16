# T4 — take one strategy through the Stage-1 funnel

Working copy: a scratch checkout with a private `registry/` copy and a read-only `data/`
view. Nothing you do here touches production evidence.

Register and screen one strategy:

| | |
|---|---|
| strategy module | `strategies/candidates/carver_daily/s06_slow_trend_long_short.py` |
| contract | `MULTI_ASSET_1D_V1` |
| hypothesis id | `HYP-2026-901` |
| hypothesis file | `.workspace/hypotheses/registered/HYP-2026-901.yaml` |
| experiment spec | `experiments/active/bench/hyp_2026_901.yaml` |

Run the funnel in order and stop at the first failure:

1. `warden register-hypothesis <hypothesis file>`
2. `warden verify <spec>`
3. `warden screen <spec>`

Invoke the CLI as `.venv/bin/warden <command>` from this directory — the worktree shares the
repo's virtualenv, and `uv run` would try to build a new one.

Rules:

* do not modify `src/warden/`, `contracts/`, or `AGENTS.md`;
* do not write a trial — a screen records a `standalone_screen` attempt, not a trial;
* do not invent field names: read an existing registered hypothesis and an existing screen
  spec first and follow their schema exactly;
* the strategy module is already registered to another hypothesis on this contract. Register
  the new lineage the way the registry demands instead of working around the guard;
* a REJECT verdict is a result, not a crash. Report it as one.

Finish by writing `answer.md`: the Stage-1 verdict, the metrics that decided it, and anything
you had to correct. Under 250 words.
