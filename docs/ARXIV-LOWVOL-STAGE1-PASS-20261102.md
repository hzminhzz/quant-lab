# Quant-lab Stage 1 recovery: predictive PASS, execution-cost revision (2026-11-02)

## Verified disposition

**One new predictive factor achieved Warden Stage 1 PASS** on observed Binance Top-100 hourly crypto data. Its 24-hour rebalance descendant also records PASS, but **shares the identical IC vector** and therefore is **not a second independent discovery**. One preregistered Ridge price-pressure ensemble was rejected.

No full Stage 3 trials or protected holdout reads were performed in this continuation, and no live trading was authorized. **Scientific qualification remains NOT ESTABLISHED.**

### Source contract and history

- Contract: `CRYPTO_PERPS_TOP100_1H_V1`; 100 symbols, 2,174,396 observed exploratory bars; pinned SHA-256 `a7e13ed71f8ac09a11f98bdb6d711d93f91a4021105141a2070fd79696af99bc`.
- Contract training window: 2023-01-01–2024-07-01 exclusive. Validation: 2024-07-01–2025-07-01 exclusive. The protected holdout begins 2025-07-01 and was never read.
- Stage 1 evaluation: purged fold-local linear Ridge, forward executable labels `Open[t+1] -> Open[t+5]` (4h horizon, 1h decision-to-fill delay), per-hour cross-sectional Spearman IC, frozen Warden mean IC >=0.015 and HAC t >=2.0.
- The isolated research ledger started as a **SQLite WAL-consistent backup of the actual source research registry**: 149 hypotheses / 75 full trials / 230 selection attempts. It ended with 152 hypotheses / 75 full trials / 233 attempts, containing all three new screens.
- Source authoritative registry **unchanged** by this experiment. The observed PASS is only recorded in the isolated snapshot and this versioned report, not as a promotion in the source ledger. Do not transfer by overwriting the source registry.

## Warden Stage 1 attempts

| Hypothesis | Outcome | OOS mean IC | HAC t | Warden attempt | Returns SHA-256 |
|---|---|---:|---:|---|---|
| HYP-ARXIV-XS-PRICE-PRESSURE-20261102 | REJECT | 0.004819 | 1.349 | ATT-50aee13f80f8799c-001 | `879270381270cdd13429ccf01f213e2143ffb7cc926407fa5ef97a6fedaa0dca` |
| **HYP-ARXIV-XS-LOWVOL-20261102** | **PASS** | **0.052817** | **12.020** | ATT-2ae7363d4315e291-001 | `a3a569bdfbc665679ec5cdccd74f498c1951c85b63262bab0006a6d32c0ad92b` |
| HYP-ARXIV-XS-LOWVOL-24H-20261102 | PASS, same signal | 0.052817 | 12.020 | ATT-ccfdfe570c9d3343-001 | `a3a569bdfbc665679ec5cdccd74f498c1951c85b63262bab0006a6d32c0ad92b` |

**Selection debt:** The low-vol signal was selected from ten disclosed **training-only** factor probes, using both training halves and a predetermined criterion. Details and all probes: [ARXIV-TRAIN-ONLY-FACTOR-20261102.md](ARXIV-TRAIN-ONLY-FACTOR-20261102.md). This factor is explicitly a descendant of previously rejected `HYP-2026-031`; historical `HYP-2026-128` had a prior Stage 1 PASS but was quarantined. The daily-rebalance revision is a post-screen **correlated economic adaptation**. Any future DSR must account for historical/related experiments and the ten training-only probes.

## Cost-aware execution comparison

The **same trained predictor** and Warden `NEXT_BAR` prices/funding were used for both portfolio policies. Fees: 4bps taker plus 2bps slippage, per one-way turnover; the pinned source has potentially zero-filled funding observations. No midprice fill claims.

| Metric | Four-hour full rebalance | 24-hour full rebalance |
|---|---:|---:|
| Net Sharpe, base costs | −0.0402 | **0.9104** |
| Gross Sharpe | 1.7454 | 1.9067 |
| Net Sharpe, doubled costs | −1.5334 | **0.1528** |
| Net Sharpe, tripled costs | −3.0174 | −0.6003 |
| +1 bar delay net Sharpe | 0.3584 | 0.7635 |
| Annualized turnover units | 777.1 | **369.7** |
| Break-even one-way cost | 6.996bps | **15.098bps** |
| Base-cost maximum drawdown | 21.33% | **15.18%** |
| Net total simple return, base costs | −5.93% | +25.08% |
| Net total simple return, doubled costs | −41.00% | +0.17% |
| Active asset-hours lacking next tradable price | 20 | 20 |

The 24-hour policy reduces turnover by approximately 52.4%, improves base-cost net Sharpe by approximately 0.951, and retains marginally positive net performance at 2x cost. The held predictor has not been independently validated at the 24-hour trading horizon; the 4h IC is unchanged because it measures prediction, not the revised execution.

Gap audit of the 24-hour Warden return path: 20 active asset-hours lack a complete next-hour open path. The script's current zero-filled gap return accounting attributes **0.0 percentage points of gross PnL** to those invalid positions, but this is **not evidence of executable fills**. These events remain unresolved and prohibit venue-specific qualification.

**Warden Stage 3 preflight:** the 24-hour execution variant is below the *existing* cross-sectional backtest thresholds of net Sharpe >1.2 and doubled-cost Sharpe >0.5, before considering the substantial DSR selection debt. Thus we **did not spend** a full Stage 3 trial merely to log a predictable REVISE.

## Preserved attempt evidence

Seven complete Warden Stage 1 attempt records (the four earlier REJECT attempts plus this continuation's one REJECT and two correlated PASS records) have been archived with their **exact Warden JSON return/IC vectors** at `docs/evidence/arxiv_stage1_20261102/selection_attempts.json.gz` (695,236 bytes). SHA-256 of the deterministic archive: `4723cc14b70938de1ed04fbad279432f8b4f01971e9506fe905ff483ba31d772`. The exporter verifies that every stored vector matches its original Warden `returns_sha256`. A test verifies record counts, original hashes and byte-for-byte equality of the two low-vol IC vectors. This is a durable export, not permission to import/overwrite authoritative research history.

## Execution artifacts

- Hypotheses under `.workspace/hypotheses/registered/HYP-ARXIV-XS-*.yaml`.
- Frozen experiment specs under `experiments/active/arxiv_crypto_*.yaml`.
- Candidate strategies: `arxiv_price_pressure_xs_1h.py`, `arxiv_lowvol_xs_1h.py`, `arxiv_lowvol_xs_24h.py`.
- Train-only study: `scripts/train_only_factor_discovery.py`; full write-once results in ignored `.workspace/stage1_optimization/train_only_factor_audit.json`.
- Selection-history-safe execution: `scripts/run_history_preserved_stage1.py`; private SQLite backup with immutable source history in ignored `.workspace/stage1_optimization/registry.db`.
- Economics: `scripts/audit_lowvol_stage1_economics.py`; write-once JSONs, including additional gap audit, in ignored `.workspace/stage1_optimization/*economics*.json`.
- Real Warden prefix-verification passed for both original and 24h low-vol pipelines.

## Next economically defensible objective

**Keep the Stage 1 PASS frozen. Do not rerun this validation window with a sweep of further turnover knobs to manufacture a Stage 3 pass.**

1. Eliminate the 20 missing executable open transitions through strictly PIT account/tradability review; do not fill an absent exit price with a known future print.
2. Verify whether the fixed historical Top-100 universe creates survivorship bias. Recompute listing/eligibility from as-of listings and volume, only through a separately versioned contract approved by the principal.
3. Obtain a fresh, previously untouched validation/forward observation interval or paper execution receipts for testing the 24h policy. No protected holdout access without human authorization.
4. Only when economics clear the unchanged net Sharpe, 2x cost, delay, robust selection-DSR and stress gates should the agent commit a full Stage 3 trial or propose venue-specific qualification.

No Warden evaluator, protected contracts, production holdout or authoritative registry records were changed.
