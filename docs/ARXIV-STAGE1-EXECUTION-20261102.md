# ArXiv Stage 1 execution — 2026-11-02

## Disposition

**Four paper-derived research variants screened on real, SHA-pinned hourly Binance USD-M data. All four REJECT at Warden Stage 1. Zero full Stage 3 trials, zero protected holdout reads, zero live orders.**

This is **isolated exploratory evidence**: the run used an empty local worktree registry. The authoritative source-checkout registry was consulted read-only (149 hypotheses, 75 historical trials, 230 selection attempts); its contract-wide selection history was **not inherited** by these four Warden screens. Do not treat this local evidence as eligible for a full-trial DSR gate until the append-only histories have been reconciled.

- Frozen Warden contract: `CRYPTO_MAJORS_1H_V1`; observed `verified_market` data SHA-256 `d0a6edd003507e97a725938a287e6e03527873643e2285231c199c55aaf8589d`.
- Validation: 2023-01-01 to 2025-01-01 UTC exclusive; 17,544 observed hourly timestamps. Train history before 2023. Existing 4h label horizon / 1-bar execution delay / 6bps modeled one-way fees+slippage / contract-provided funding (source may zero-fill missing funding rates).
- Pinned observed source was first checksum-verified against the source checkout and then materialized to this isolated worktree. No sealed data was accessed.
- Full Stage 3 and venue execution qualification were **not** attempted.

## Recorded Warden results

| Research adaptation | Stage 1 | Net PF | Bootstrap p | Active % | Gross trade edge bps | Avg holding h | Net SR* | Attempt |
|---|---|---:|---:|---:|---:|---:|---:|---|
| BTC/ETH Ridge sign (4h forecast) | REJECT | 0.9928 | 0.085 | 53.07 | 12.224 | 18.75 | -0.153 | ATT-29d803216c04956d-001 |
| BTC/ETH Ridge fixed 2x cost band | REJECT | 1.0343 | 0.090 | 56.63 | 534.198 | 764.23 | 0.753 | ATT-429db6d2b034af44-001 |
| Six-hour crypto trend, equal weights | REJECT | 1.0072 | 0.015 | 100.00 | 15.556 | 33.59 | 0.214 | ATT-da5cd8ca85044f20-001 |
| Six-hour crypto trend, inverse-vol weights | REJECT | 1.0205 | 0.005 | 100.00 | 19.388 | 34.15 | 0.597 | ATT-cdf48ff435297758-001 |

*Net SR calculated from the **persisted Warden Stage 1 hourly net return vectors** using annualization `sqrt(8760)`. It is diagnostic, not a Stage 3 result. The Warden `trade_edge` and `avg_holding_bars` are evaluator-produced approximations, not realized closed-trade analytics; the unusually large fixed-band values reflect rare position changes.

#### Return-vector identifiers

Warden recorded complete 17,544-bar net-return vectors in the isolated registry. Their SHA-256 digests are:

| Variant | Returns SHA-256 |
|---|---|
| BTC/ETH immediate | `f148e8eb92560009d5171aed790163b39e801d3e7349e984e91453bcbd88d89c` |
| BTC/ETH cost filter | `325fbd3110ca27345a07adf32d08b523fc8665bb09400acb0efaf946f6b0107c` |
| Six-hour equal weight | `a1d92f16af2fa4b20b779c287532faf5530cc02b4c2b5d7275f7e9874926b10a` |
| Six-hour volatility weight | `6f42bf9cc0e7d27b7847175e1a05cceab6d2cb5271991b90a5783d62ca6bc3e5` |

The ignored worktree registry contains the full arrays. This versioned report records their identity, but the authoritative historical ledger has not been modified or reconciled.

## Economic interpretation

1. Paper A, [Bysik and Slepaczuk (2026)](https://arxiv.org/abs/2606.00060): filtering changes improved the net Sharpe diagnostic by about 0.906 and PF by 0.0415, but **failed net PF and bootstrap significance**. Positions held for about 764 hours on average; this may be largely persistent crypto beta, not skill. The 4h BTC/ETH Ridge baseline is an explicitly distinct adaptation, not a replication of the paper's hourly XGBoost results.
2. Paper B, [Nguyen (2026)](https://arxiv.org/abs/2602.11708): volatility weighting improved net Sharpe diagnostic by about 0.382 but PF remained 1.021, below 1.15. Both bootstrap p values were below 0.05, demonstrating that positive gross conditional return can coexist with insufficient net profit factor. This adaptation uses 6-hour **decisions**, an as-of 30-day dollar-liquidity filter and fixed 96-hour momentum. It does **not** use absent market capitalization, monthly Sharpe optimization or unobserved intrabar stops.
3. The 23-asset history is a frozen, continuous-coverage major-coin universe. This creates possible historical survivorship and long-beta confounding; neither study is authorized to claim generalizable alpha.

## Stage 1 metric calibration — substantial frequency mismatch

**Verified engineering fact:** `src/warden/gates.py:evaluate_stage1_rules_ts` computes profit factor as `sum(positive hourly net_returns) / abs(sum(negative hourly net_returns))`, i.e., **bar-return PF**, not completed-position/trade PF. The same default minimum `1.15` is used for rule-based signals at 1h, 15m, and 1d.

Independent formula from truncated normal moments: for per-bar normally distributed excess return `R ~ N(mu, sigma^2)`, `z=mu/sigma=annual_SR/sqrt(bars_per_year)`, expected bar-PF equals `(phi(z)+z*Phi(z)) / (phi(z)-z*Phi(-z))`. Solving PF=1.15 yields:

| Bars per year | Expected bar PF at annual SR=2 | Implied annual SR for PF >= 1.15 |
|---:|---:|---:|
| 252 daily | 1.37153 | 0.8850 |
| 8760 hourly | 1.05502 | 5.2182 |
| 35040 15-minute | 1.02714 | 10.4364 |

This is a **theoretical iid-Gaussian counterexample**, not a calibrated inference about actual crypto distributions or exact individual-trade hurdle; costs, holding policies, skewness and dependence change its meaning. Nevertheless the same PF=1.15 bar-level threshold demonstrably represents different annualized economic requirements depending on sampling frequency.

- Reproducible diagnostic: `uv run python scripts/audit_stage1_pf_frequency.py`.
- Three deterministic regression tests, including an actual unchanged Warden Stage 1 rejection of a synthetic positive-annual-Sharpe hourly path: `tests/test_stage1_pf_frequency_diagnostic.py` (3 passed).
- **No gate changes approved or applied.** Suggested remedy is a separately approved, versioned evaluator/contract that measures completed-trade (or explicitly standardized holding-period) P&L with sound funding/cost attribution and false-positive calibration, rather than silently lowering PF=1.15. Independently compare with existing frozen gate on quarantined research windows, keep original trial counts and protected-holdout reads untouched.
- Stage 1 also computes bootstrap p on active **gross** returns while PF is on **net** returns, so gross statistical detectability does not imply executable net edge. Pair bootstrap of **net return differentials** is a candidate diagnostic, not a substitute for Warden's existing gate.

## Engineering and governance

- New source modules: `arxiv_cost_aware_ridge_1h.py`, `arxiv_six_hour_crypto_trend.py`.
- Four distinct preregistered hypotheses + four immutable experiment specifications.
- Twelve focused new tests: 5 cost/filter/snapshot, 4 six-hour controls, and 3 analytic frequency controls passed. Earlier five Stage 1 calibration tests also passed. Both strategy pipelines passed actual `warden verify` prefix-invariance on pinned market data.
- Ruff and Warden `check-worktree` passed. The broader clean-tree suite retains the historical missing `contracts/multi_asset_1d_v1.yaml` fixture blocker.
- No Warden core, contract, signed manifest or source live registry modifications.

## Research continuation

- Preserve all four REJECT records; do not rescreen with tuned thresholds.
- [PR #40](https://github.com/hzminhzz/quant-lab/pull/40) already screened ES/NQ 15-minute Syncster hypotheses (both rejected); reconcile that evidence rather than duplicating it. Its Binance order-flow contract proposal remains separately unapproved.
- Next: fit source-complete **distinct** mechanisms, benchmark persistent exposure against passive/market-beta controls, and calibrate Stage 1's gross bootstrap against net economics with synthetic nulls. Use a WAL-consistent snapshot of the **full** source registry before any new Stage 3 attempt; no synthetic trial-budget reset.
