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

## Approved PIT signal confirmation (2026-11-02)

Warden's interactive approval path adopted the unchanged PIT contract proposal as `CRYPTO_PERPS_PIT100_1H_V1` (SHA `c76266d4...`); no human-held HMAC key was present. The frozen inverse-volatility Ridge factor passed the unchanged cross-sectional Stage 1 gate with mean IC **0.05742364**, HAC t **11.5513** on **8,755** IC observations. Non-overlapping four-hour IC phases remain positive (IC 0.0550–0.0586). Historical evidence preserved with attempt `ATT-480df2ef5d8788f2-001`, no Stage 3 trial or holdout read. This is a *same-time-window corrected-universe confirmation*, not a new independent temporal holdout or executable-profit claim. Details: [ARXIV-PIT100-STAGE1-20261102.md](ARXIV-PIT100-STAGE1-20261102.md).

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

## Continuation results — 2026-11-02

Four paper-derived BTC/ETH and six-hour crypto variants were preregistered and evaluated on frozen observed-market hourly data with the real Warden Stage 1 runner: **4 REJECT, 0 PASS, 0 Stage 3 trials, 0 holdout reads**. Their separate experiment specifications, Warden attempt IDs and return-vector SHA-256 hashes are documented in [ARXIV-STAGE1-EXECUTION-20261102.md](ARXIV-STAGE1-EXECUTION-20261102.md).

Independent gate calibration found a major frequency comparability concern: the unchanged Warden rules_ts profit factor is calculated on per-bar net returns, but its fixed PF=1.15 hurdle maps, in an iid normal counterexample, to annualized Sharpe roughly 0.885 for daily, 5.218 for hourly, and 10.436 for 15-minute sampling. Three reproducible diagnostic tests passed. **No frozen gate was modified, and previously rejected studies stay rejected.** Contract/evaluator approval is required before any corrected qualification rule.

The ES/NQ Syncster 15m screens were already executed independently in PR #40 and both rejected; this work avoids duplicate aliases or resetting budgets.

## Stage 1 continuation — observed PASS, 2026-11-02

A causal pooled Ridge model of price pressure (HYP-ARXIV-XS-PRICE-PRESSURE-20261102) failed Warden cross-sectional Stage 1 (IC=0.00482, HAC t=1.35). Ten **training-only** factor probes were explicitly disclosed. A selected low-realized-volatility factor, **HYP-ARXIV-XS-LOWVOL-20261102**, passed the unchanged Warden cross-sectional Stage 1 (IC=0.05282, HAC t=12.02) with the existing full selection history carried in a WAL-consistent isolated registry snapshot. This lineage descends from older rejected HYP-2026-031 and is not independent of historical factor mining.

A single explicitly correlated economic revision—24h rather than 4h rebalance—retained the **same** Stage 1 IC PASS but improved base-cost net Sharpe from -0.040 to +0.910 and double-cost net Sharpe from -1.533 to +0.153; turnover fell about 52%. **Stage 3 is not qualified** (net Sharpe below 1.2, 2x cost below 0.5, incomplete executable price paths and historical selection debt). No full trial or holdout was consumed. Full receipt: [ARXIV-LOWVOL-STAGE1-PASS-20261102.md](ARXIV-LOWVOL-STAGE1-PASS-20261102.md). Original four Stage 1 REJECT attempts are unchanged.

## Point-in-time source recovery and economic re-audit (2026-11-02)

The original Stage 1 low-volatility PASS was conditional on a **future-selected Top-100**. The source pipeline required year-2026 availability and ranked using full-2024 quote volume. Reconstructing 30 purely-as-of monthly elections across 8,795 observed Binance market shards yielded **364 unique eligible assets**, with only **54.1/100 average monthly overlap** versus the frozen original. A separately versioned PIT 1h panel (2,187,277 rows / 364 assets) and unsigned contract proposal now exist; original Warden data and gates remain unchanged. The 20 missing next-open events were all at the exact exploratory cutoff, fixed via read-only terminal-flat fee accounting (not a new strategy). Funding cadence audit found 4h/8h/1h/2h exchange events while the frozen Warden model assumes universal 8h. Known-event funding reduces 24h net SR from 0.908 to 0.785 at 1x and 0.149 to 0.026 at 2x costs; **no Stage 3 qualification**. Full evidence and hashes: [ARXIV-LOWVOL-NEXT-PIT-RECOVERY-20261102.md](ARXIV-LOWVOL-NEXT-PIT-RECOVERY-20261102.md).

## Disposition protocol
Classify every candidate as SOURCE_INCOMPLETE / DATA_BLOCKED / IMPLEMENTATION_BLOCKED / SCREEN_REJECT / SCREEN_PASS / STRESS_REVISE / READY_FOR_HUMAN_HOLDOUT. Recover ordinary blockers and continue independent candidates. No live trading authorization.
