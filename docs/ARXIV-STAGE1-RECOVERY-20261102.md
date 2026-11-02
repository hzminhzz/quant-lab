# ArXiv-to-Stage-1 research recovery — 2026-11-02

## Goal and authority
Improve the measured quality and diversity of quant-lab Stage 1 research without changing frozen Warden gates, rewriting evidence, inspecting protected holdouts, or claiming alpha based on paper-reported backtests. The principal approved this research work unit on 2026-11-02.

## Evidence baseline
- Local main checkout has extensive dirty/untracked research artifacts; execute only in isolated worktree from origin/main.
- 2026-11-01 inventory: 149 registered hypotheses; historical trial and selection rows require fresh read-only reconciliation before attempting anything. Recent OptMem records say HYP-2026-096/097/098 advanced to Stage 3 and REVISE; do not spend again.
- Warden rules_ts gate: net profit factor >=1.15, active time >=5%, gross conditional trade-edge >1.5x one-way cost, active-bar bootstrap p<0.05. These are frozen semantics.
- Audit concern: the current 200-replicate active-bar bootstrap conditions on realized activity and uses a stationary-in-active-bars circular resample. This needs calibration diagnostics, not unilateral policy changes.

## Executable workstream — in dependency order
1. **Calibration / controls**: Add isolated deterministic diagnostic experiments exercising the genuine Stage 1 evaluator on null, planted-signal, high-dependence, sparse-position and fee-dominated inputs. Assert only stable invariants; report empirical false-pass/power without adjusting gate thresholds. Trace apparent findings to concrete code and data semantics.
2. **Inventory recovery**: Read-only reconcile current registry attempts and outcomes. Verify inherited and existing Stage 1 passes, avoid double screens, and rank eligible unscreened 8 crypto-major and 42 cross-sectional mechanisms by verified data availability. Archive disposition separately from registry state.
3. **Paper A: cost-aware BTC/ETH hourly position change**: primary source https://arxiv.org/abs/2606.00060. Build causal trailing-feature Ridge baseline and a cost-aware no-trade band with fixed lag, cost assumptions, forward label horizon and purged walk-forward. Explicit hypotheses: (i) directional forecast has positive OOS IC, (ii) conditional trading improvement survives costs over the same forecast without a band. Avoid optimizing threshold directly on validation/holdout.
4. **Paper B: 6h crypto trend/portfolio**: https://arxiv.org/abs/2602.11708. Compare simple 6h trend with volatility-scaled exits, and independently test PIT universe/portfolio construction; long-bias and financing attribution mandatory.
5. **Paper C: ES/NQ session regime**: https://arxiv.org/abs/2605.04004. Independently specify observable session-based mechanism before tests. Use pinned Syncster exploratory CME contracts only where matching source/window; validate continuous roll, timezone, costs and session gaps. No 1-minute microstructure requirements for preliminary directional screening.
6. **Governed screens**: For each genuinely new source-complete hypothesis, source inspection -> preregister -> preflight -> verify -> Warden Stage 1. Advance only passers to Stage 3 with existing budgets, immutable full trial accounting and six-check audits.

## Acceptance
- A reproducible calibration matrix with data generation seed, sample sizes, observed Stage 1 metrics and caveats.
- Candidate specifications declare mechanism, source, feature timestamps, universe, horizon, baseline, cost model, risk and reject criteria *before* validation results.
- Every screen references Warden attempt/run ID and fixed data SHA. Rejections remain rejected. No fake proxies, missing source fields or holdout reuse.
- No strategy called qualified absent direct evidence of OOS strength, costs, stress, and authorized protected evaluation.

## Executed calibration evidence (2026-11-02)

- `tests/test_stage1_calibration_controls.py`: 5/5 tests passed against the real, unchanged `evaluate_stage1_rules_ts` function.
- Fixed synthetic null: 50 independent normal zero-drift series, 300 active bars each, seed `20261102`, one-way cost 1 bp. Four of 50 passed all existing gates (8% observed false-pass fraction). Sampling uncertainty is substantial; not proof of true Type-I error or permission to change thresholds.
- Explicit positive constant-drift, zero-signal, friction-dominated and too-sparse controls behaved as intended.
- Ruff passed on added tests; `warden check-worktree` verified protected hash/LOC integrity; `git diff --check` passed.
- These tests use synthetic observations only and provide **no** strategy alpha or statistical qualification.

## Disposition protocol
Classify every candidate as SOURCE_INCOMPLETE / DATA_BLOCKED / IMPLEMENTATION_BLOCKED / SCREEN_REJECT / SCREEN_PASS / STRESS_REVISE / READY_FOR_HUMAN_HOLDOUT. Recover ordinary blockers and continue independent candidates. No live trading authorization.
