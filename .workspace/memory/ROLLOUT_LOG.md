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
