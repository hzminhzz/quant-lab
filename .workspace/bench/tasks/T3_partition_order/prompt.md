# T3 — make `load_pair` order-independent of partition naming

`scripts/build_fx_majors_1d_v2.py` reads one Dukascopy daily store per FX pair. The lake's
Hive writer emits month partitions from `dt.month()`, so a store can contain `month=9`
next to `month=10`. `load_pair` concatenates the parquet leaves in whatever order the
filesystem enumeration produced.

Today that defect is invisible only because a downstream `normalize_panel` sorts the whole
panel at the end. Any other consumer of `load_pair` inherits silently misordered rows.

Fix `load_pair` so that the frame it returns is in chronological order on its own, for both
padded and unpadded partition names. Keep the existing validation (implausible years, symbol
label disagreement) intact, and do not weaken it to make ordering pass.

Add a regression test under `tests/` that builds a small synthetic store with misordered
partition names and asserts chronological output. Your test must fail on the current code.

Do not modify anything under `src/warden/`, `contracts/`, or `AGENTS.md`.
