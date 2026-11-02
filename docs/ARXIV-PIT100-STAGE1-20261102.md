# PIT Top-100 low-volatility confirmation — Warden Stage 1 (2026-11-02)

## Scientific disposition

**PASS at the unchanged Warden Stage 1 cross-sectional signal gate** under a new, separately pinned, monthly-as-of Binance USD-M research contract. This is a *confirmation on a corrected asset universe during an already examined historical time interval*, not a virgin temporal holdout, proof of executable alpha or a Stage 3 qualification.

| Measure | Result |
|---|---|
| Hypothesis | `HYP-ARXIV-XS-LOWVOL-PIT100-20261102` |
| Contract | `CRYPTO_PERPS_PIT100_1H_V1` |
| Contract SHA-256 / data snapshot | `c76266d4c3bf2e37ce904bedd182cd6e54b9cfd3e6bc9fe1f337925e4a818a8a` |
| Contract spec hash | `f58c385eb80eb275` |
| Dataset | 2,187,277 raw-observed OHLCV rows from 364 historically selected assets |
| Train | 2023-01-01 through 2024-07-01 exclusive |
| Stage 1 validation | 2024-07-01 through 2025-07-01 exclusive |
| Prediction/label | One-bar-delayed, four-hour open-to-open, purged 4-fold Ridge |
| Warden report | **PASS** — mean IC `0.05742364202055246`, HAC t `11.551318322648376` |
| Frozen Warden minima | Mean rank IC ≥0.015; HAC t ≥2.0 |
| Full IC observations | 8,755 |
| Selection attempt ID | `ATT-480df2ef5d8788f2-001` |
| Warden IC vector SHA256 | `289f194ea46e42b23da64137f923b6dbf702c1bb557fd49759e7a83eb13a0720` |
| Source/model family | Original inverse-24h-volatility factor, parent `HYP-ARXIV-XS-LOWVOL-20261102`; no parameter search after original result |
| Full Stage 3 trials / protected holdout reads | **0 / 0** |

### Approval and immutable contract activation

User explicitly approved continuing the next required PIT-contract adoption and Warden Stage 1 gate on 2026-11-02.

- The existing `docs/contract-proposals/CRYPTO_PERPS_PIT100_1H_V1.yaml` was adopted **byte-for-byte** as `contracts/crypto_perps_pit100_1h_v1.yaml` without changing costs, labels, embargo, train/validation splits or the pinned dataset.
- The independent source manifest is now in `contracts/datasets/CRYPTO_PERPS_PIT100_1H_V1.json`, with `APPROVED_RESEARCH_STAGE1_ONLY` authorization and source lineage. The original frozen `CRYPTO_PERPS_TOP100_1H_V1` contract remains unchanged.
- Warden's own `generate-manifest --human-approve-manifest` command produced a manifest with `mode=interactive`, `approver=human-operator`, and checksum SHA256 `4c68afda13b0c0f894b79a913d06ea2c8462edfac3f9200aafb3c087ef0927ab`. This is a **record of explicit conversational research approval**, **not a cryptographic HMAC signature**. The human-held signing key was absent and never fabricated.
- Warden `check-worktree` and `resolve_dataset` verified the protected manifest and exact observed-market SHA-256 pin before the screen.

### Causal verification and resolved verifier blocker

The source has `funding_event_time` (datetime), `funding_observed` (boolean) and `funding_age_hours` (float) provenance columns. An initial Warden `verify` invocation hit a `numpy.exceptions.DTypePromotionError` when generic `verify_prefix_invariance` attempted `numpy.allclose` on the **datetime** metadata column. This was a checker-schema error, **not a stage-1 rejection**.

A PIT-only adapter, `strategies/candidates/arxiv_lowvol_pit100_adapter.py`, exposes the frozen numeric signal features `realized_volatility_24h` and `liquidity_per_hour` for the existing verifier and delegates **unchanged** Ridge prediction/position generation to `arxiv_lowvol_xs_1h.py`. Synthetic parity tests verify exact IC vectors, positions and fold coefficients against the frozen baseline; provenance metadata changes do not alter signal features. The second Warden verify completed:

`{"status":"PASS","prefix_invariance":true,"cached":true,"rows":2187277,"market_eligible":true}`

No `src/warden` evaluator, leakage checker or frozen parent model was modified.

## Recorded signal: additional non-overlap check

A read-only diagnostic sampled every fourth item in the **persisted, canonical Warden IC array**. This removes the direct four-hour overlapping-label dependence within each sampled phase; the original Warden IC vector and statistics remain untouched.

| Recorded IC phase offset | Samples | Mean rank IC | HAC t |
|---|---:|---:|---:|
| 0 | 2,189 | 0.058634 | 10.84 |
| 1 | 2,189 | 0.057559 | 10.45 |
| 2 | 2,189 | 0.058484 | 10.73 |
| 3 | 2,188 | 0.055016 | 10.22 |

The frozen 2024-07–2025-07 period had already been inspected through the former hindsight-selected cohort, so this is **a correlated universe confirmation**, not independent fresh time OOS. This summary must not be used to replenish the 3-trial budget or reduce the family DSR correction.

### Full selection history accounting

The isolated Stage 1 registry copied the existing authoritative historical lineage and retained the preceding three arXiv XS screens. Its state **after** the PIT attempt is: **153 hypotheses, 75 full trials, 234 selection attempts**. Four earlier arXiv BTC/ETH/6-hour screens were kept in a **separate earlier isolated registry**, not secretly inserted into this one. Their seven-attempt master archive, `docs/evidence/arxiv_stage1_20261102/selection_attempts.json.gz`, preserves their full canonical return vectors. Together the two stores account for **238 distinct selection attempt IDs**, **not 238 independent strategy trials**. No DSR or final eligibility conclusion should be computed from only 234 attempts.

The PIT attempt, its **full 8,755 IC observations**, both raw Warden metrics, phase statistics and explicit linkage to the prior archive are exported in immutable `docs/evidence/arxiv_stage1_20261102/pit100_lowvol_stage1_receipt.json.gz`:

- Receipt archive SHA-256: `28a1fe8de2574be4b7222eb0936f4397cbdc75d84cc2dadbc83db240d001a341`.
- Verifies exact Warden `returns_sha256` and independent recomputation of its mean IC and HAC statistic.
- Includes four archival-only attempt IDs so no prior selection debt disappears.
- **No authoritative SQLite registry was overwritten**. This remains an isolated, export-backed research screen until normal approval/merge and append-only registry reconciliation.

## Economic limits remain unchanged

- **14.39%** of PIT-panel rows lack observed funding; the venue has 1h/2h/4h/8h event intervals. Warden's 8h masking is not a faithful per-instrument funding ledger.
- The as-of monthly source removes the identifiable 2024 full-year and 2026-survivor selection errors, but available raw exchange archives have **not been attested as delisting-complete**.
- The prior historical 24h-rebalance economic diagnostic was **not** requalified on this new PIT cohort. Its known-event funding diagnostics on the old biased 100-coin cohort were net Sharpe `0.785` (1x cost) and `0.026` (2x), both below the existing Stage 3 hurdles.
- Missing/event-time funding, terminal execution, source availability, actual slippage, DSR, regime robustness and protected holdout still block an executable portfolio claim.
- **No capital is authorized.**

## Verification outcomes

- **Warden `verify`: PASS** — full 2,187,277-row SHA-pinned PIT source, causal prefix-invariance, numerical feature cache and observed-market provenance verified.
- **Focused regression suite: 66 passed**, including immutable receipts, exact model-adapter parity, provenance/contract integrity, Warden calibration and safety tests. One Polars as-of-join sortedness warning remains in the source-builder fixture; this is not a strategy or verifier failure.
- **Ruff: PASS** on modified/new Python files; `warden check-worktree` and staged diff checks pass.
- **Broader clean-checkout pytest:** 246 passed, **16 failed**. Those 16 are the previously observed clean-checkout failures (missing local contract datasets and registry fixtures, test isolation/timing assertions), not introduced by this PIT screen. One separate test module requires the pre-existing missing `contracts/multi_asset_1d_v1.yaml` fixture and was excluded from that run.
- No change to the original parent strategy, Warden core statistical rules, fee model, existing protected dataset identity or authoritative source registry.

## Reproduce / verify

From the isolated branch with the exact pinned dataset materialized in `data/exploratory/`:

```bash
uv run warden check-worktree
uv run warden verify experiments/active/arxiv_crypto_lowvol_pit100_20261102.yaml --json
uv run python -m pytest -q tests/test_arxiv_lowvol_pit_adapter.py tests/test_arxiv_pit_evidence.py tests/test_arxiv_pit_stage1_receipt.py
```

A new Warden screen is **not** part of routine reproduction. It was recorded exactly once with `scripts.run_history_preserved_stage1`; subsequent sessions must reuse the archived receipt and existing selection attempt, not reissue a candidate ID.

The 48 MB source data file is **local and gitignored**, not shipped by PR. Rebuilding it requires the pinned raw Binance monthly archives and the source-hash-checked `scripts/build_pit_top100_proposal.py` workflow. Fail closed if bytes differ. The contract/manifest/proposals, experiment, code, complete Warden receipt and tests are version-controlled.
