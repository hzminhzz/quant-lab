# Phase 2 research case studies

Two executable Marimo applications using the ML4T third-edition case-study
sequence (mechanism, data feasibility, causal features/labels, linear model,
diagnostics, economic decision, synthesis). The reference case study is
https://github.com/stefan-jansen/machine-learning-for-trading/tree/main/case_studies/crypto_perps_funding.
This repository consolidates the narrative into one app per scientific
question; Warden remains the only scientific evaluator and evidence owner.

## Run locally on the VPS

From this repository root, using the isolated locked environment:

```sh
uv sync --frozen
PYTHONPATH="$PWD" uv run --frozen --with marimo==0.24.0 --with matplotlib==3.10.8 marimo edit case_studies/phase2/p2_d01.py --host 127.0.0.1
PYTHONPATH="$PWD" uv run --frozen --with marimo==0.24.0 --with matplotlib==3.10.8 marimo edit case_studies/phase2/p2_a01.py --host 127.0.0.1
```

Run one at a time, or choose a separate port. Use an authenticated tunnel or
local VPS desktop; do not expose the editor publicly. The UI packages are
directly pinned in a separate uv overlay; the scientific dependencies come
from the existing uv.lock (ML4T Engineer and Diagnostic 0.2.1, Polars 2.0.0).
UI transitive packages are not part of the appliance release lock.

By default both apps are idle. Click **Run bounded synthetic demonstration**
for deterministic generated data; click **Read appliance observation** for
the supported read-only control projection. No notebook cell compiles a
dataset, registers a hypothesis, scores market data, opens sealed files or
starts a backtest. The actual registry remains external. A hypothesis
identifier is optional and only filters the observation. No registered
forecast evidence exists for either draft, so unavailable evidence is shown
as such rather than fabricated.

## Verification

```sh
PYTHONPATH="$PWD" POLARS_MAX_THREADS=2 OPENBLAS_NUM_THREADS=1 uv run --frozen python -m pytest -o addopts='' -q tests/experiments/test_phase2_case_studies.py tests/test_ml4t_catalog_v2_resolution.py tests/test_spec109_i_resolve_data.py tests/test_ml4t_lake_adapter.py
PYTHONPATH="$PWD" uv run --frozen --with marimo==0.24.0 --with matplotlib==3.10.8 marimo check case_studies/phase2/p2_d01.py case_studies/phase2/p2_a01.py
PYTHONPATH="$PWD" POLARS_MAX_THREADS=2 OPENBLAS_NUM_THREADS=1 MPLBACKEND=Agg uv run --frozen --with marimo==0.24.0 --with matplotlib==3.10.8 python -m case_studies.phase2.smoke
```

Optional `--observe-appliance` performs read-only before/after control
observations and checks ledger counts and absence of scientific effects.
The actual smoke test verified 5,470 eligible D01 rows / 3,280 validation
forecasts and 251 eligible A01 sessions / 148 validation forecasts, four
chronological native ML4T folds each. The remaining generated observations
are not scored; this is not an approved market train/validation split.
No synthetic statistic is market evidence.

## Metadata resolution

On a development checkout with the v2 taxonomy repair, the following
commands perform the existing metadata-only requirement resolution, not
admission:

```sh
ML4T_DATA_STORE=/home/quant/dev/quant/ml4t-data/.data/store uv run --frozen warden resolve-data --requirement case_studies/phase2/d01_requirement.yaml --json
ML4T_DATA_STORE=/home/quant/dev/quant/ml4t-data/.data/store uv run --frozen warden resolve-data --requirement case_studies/phase2/a01_requirement.yaml --json
```

Both identify canonical source stores and return compile_required. Their
production venue-policy trust is absent. Do not compile or score before the
authority and scientific-interface requirements in DECISION.md are resolved.
Operational commands against canonical state must use the installed
appliance launcher; the development environment is not that runtime.

INTENTS.md is the consolidated draft, not an approved Warden intent.
DECISION.md identifies the exact integration, policy and scientific
approval dependencies. No applications exist for deferred B01 or C01.
