Ticket: TKT-01 — VPS Environment Audit & Clean-Room Repository Bootstrap
Commit: 8e11761 (pre-existing implementation; reverified at rollout start)
Tests: uv run pytest tests/test_bootstrap.py -q — 4 passed in 0.27s
Lint: uv run ruff check src/ scripts/ tests/ — All checks passed
Metrics: baseline warden CLI and locked environment available; rollout continues at TKT-02
Ticket: TKT-02 — Portable agent instructions, 61 skills and optional adapters
Commit: d416b51
Tests: uv run pytest tests/test_agent_compatibility.py -q — 17 passed in 0.97s
Lint: uv run ruff check — All checks passed; reference typing/imports mechanically modernized
Metrics: 61 skills; upstream a3f74452112a22e90654950b150a7effca2be0ff; OMP install/clean idempotency smoke passed
Ticket: TKT-03 — Immutable contracts schema and initial markets
Commit: e6dca4e
Tests: uv run pytest tests/test_contracts.py -q — 15 passed in 0.08s
Lint: uv run ruff check — All checks passed
Metrics: crypto spec_hash 5dba993566c480ec; mandatory one-way cost 6 bps; timezone-aware half-open date windows
Ticket: TKT-04 — Read-only salvage and chronological POSIX holdout split
Commit: 9762938
Tests: uv run pytest tests/test_data_salvage.py -q — 2 passed in 2.05s
Lint: uv run ruff check — All checks passed
Metrics: legacy absent; synthetic exploratory 24000 rows SHA256 6abd79bd09404bbf3795c5401323a175d05e708f031d2fb817a065bdf25317cb; holdout 4800 rows SHA256 6bf2befc26d3217dc354385a4205bb37be091753378830cf86061d600c69d224; cutoff 2025-03-25T08:00Z; human-only lock prepared, never run
Ticket: TKT-05 — Append-only SQLite trial registry and effective-rank DSR
Commit: 382772b
Tests: uv run pytest tests/test_registry.py -q — 3 passed in 2.39s
Lint: uv run ruff check — All checks passed
Metrics: SQLite BEFORE UPDATE/DELETE triggers abort mutations; 3-strike budget enforcement halts 4th attempt; candidate DSR decouples from historical leader
Ticket: TKT-06 — Prefix-invariance leakage prover and content-addressed feature cache
Commit: bad97f9
Tests: uv run pytest tests/test_warden_leakage.py -q — 4 passed in 2.96s
Lint: uv run ruff check — All checks passed
Metrics: 100% detection across 4 planted leakage traps; causal time-series/cross-sectional pipelines verified; feature cache retrieval under 50ms
Ticket: TKT-07 — Family-specific gates, backtest runner, warden CLI and manifest
Commit: d0695d6
Tests: uv run pytest tests/test_gates_and_runner.py -q — 5 passed in 5.05s
Lint: uv run ruff check — All checks passed
Metrics: src/warden/ total 1140/1200 LOC; .warden_manifest.sha256 generated; worktree tampering aborts with code 2; linear-first gate enforced
