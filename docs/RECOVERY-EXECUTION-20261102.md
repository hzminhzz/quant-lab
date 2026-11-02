# Quant-lab research blocker recovery: execution and evidence (2026-11-02)

**Disposition:** CME Stage 1 executed: **2 scientific rejections, 0 Stage-3 trials**.
Binance Top-100 order-flow market fields recovered and exactly joined into a new,
write-once local exploratory dataset. **No new order-flow Warden Stage-1 screens
performed**: original 72h hypotheses require a separately approved 72-bar
contract, whereas the original frozen contract has `label_horizon_bars: 4`.
This is an execution checkpoint, **not completion of every historic blocker**.

## Working state and immutability

- Source main checkout: `/home/quant/dev/quant-lab`. It had unrelated dirty and
  untracked work; **none was edited** here. Existing registry observed read-only:
  149 hypotheses, 75 trials, 230 selection attempts (2026-11-02 04:12 UTC).
- Execution isolated worktree: `/home/quant/.devspace/worktrees/quant-lab-4ac4b629`,
  branch `research/syncster-stage1-recovery-20261102`, based on merged
  `41c982226abeb87ceab7639f2368ad6ab468d60c`.
- This worktree's registry is separate and gitignored: **2 registered studies,
  2 Stage-1 rejection attempts, 0 full trials**. It does NOT inherit the
  source registry's statistical search floor; consequently its independent
  DSR would **not** support promotion. Do not misreport this as reset
  statistical multiplicity. No protected holdout was read, copied or created.
- Existing related historical negative trials remain intact. The 2026-11-01
  global inventory and 2026-11-02 reconciliation are in
  `docs/THESIS-RECOVERY-AUDIT-20261101.md` and PR #39, respectively.

## Syncster CME: data audits and executed Stage 1

Pinned, intact observed-market provider-continuous series, not independently
attested individual-contract CME fills or roll-adjusted front-month streams.

| Source | Original 1m SHA256 | Prepared 15m bars | Prepared 15m SHA256 | Complete NY RTH sessions |
|---|---|---:|---|---:|
| ES | `e6320a6772fc5819b12605ffc63f88ac1861c26b8fa528d052e1b21d914438a8` | 213,127 | `9510c42b8172d300f8dbfd047a6561270c8b6e2e391f50fbc3d0ae28fd8cfb63` | 2,243 |
| NQ | `35071bdad8b4abefabdf2543543d56b39c54dd56ee0ae7251ffed73107fb1681` | 212,434 | `7a976688ea097f181203626a50dc54e983b4fb27a887502f0ddbad84d7b4e915` | 2,234 |

- Contract: `CME_{ES,NQ}_15M_SYNCSTER_V1`. Data cutoff exclusive
  2025-07-01T00:00:00Z, validation starts 2023-01-01T00:00:00Z.
- Next observed bar **open** execution only. One-way modeled friction:
  0.5bps fee + 1.5bps spread/slippage allowance = 2bps. No observed
  exchange commission, book, impact, roll or broker-fill attestation.
- ES: 09:30-16:00 New York full 26-bar regular session (all 15
  constituent minutes per bar required); if previous complete RTH
  session is red, desired long on first eight next-session RTH bars.
  Eight 15m bars = ~2h, **not eight hours**. Warden adds its one-bar lag.
  Original BTA conjecture was a general down-day tendency. This RTH rule
  is an explicit **researcher adaptation**, not original author code.
- NQ: complete four-bar 09:30-10:30 NY opening range; first subsequent
  close strictly above that range's high through 13:30 NY triggers
  eight decision bars of desired exposure; Warden one-bar lag,
  no shorts/stops/overnight position. Also researcher-authored, not
  an originally specified author recipe.

**Native Stage 1 results (stored as append-only selection attempts):**

| Run/hypothesis | Attempt | PF | Active conditional gross edge | Time in market | Block bootstrap p | Warden |
|---|---|---:|---:|---:|---:|---|
| `HYP-CME-ES-DOWN-RTH-20261102` | `ATT-784f9c92d0255f1d-001` | 1.002505 | +0.515750 bps / active bar | 3.7133% | 0.115 | **REJECT** |
| `HYP-CME-NQ-ORB-RTH-20261102` | `ATT-539a07d8a34dab58-001` | 1.063367 | +0.832211 bps / active bar | 4.2710% | 0.020 | **REJECT** |

Native rules_ts gate requires **PF>=1.15, TiM>=5%, bootstrap p<.05,
trade edge>1.5x one-way cost**, all conjunctive.
ES fails PF, TiM, bootstrap; NQ fails PF and TiM.
Both are **STAGE1_REJECTED**. No Stage 3 was run; no frozen variants
were retuned, and no backtest trial budget was spent.
Historically rejected NQ CFD variants and the ES CFD down-day
Stage3 `REVISE` remain prior searches, not erased by the new CME IDs.

**Verification limitation:** the generic Warden `verify` helper in
`src/warden/leakage.py` unconditionally perturbs a `funding_rate`
column, while the native CME market panel legitimately has no such
field. `warden verify` reproduced `ColumnNotFoundError`.
Independent prefix-invariance and future-shock tests, using a synthetic
test-only inert funding field, passed. The protected evaluator and frozen
dataset have NOT been altered. This is a scoped feature-verifier
engineering defect, **not** an assertion that `warden verify` passed.
Date caveat: new frozen registered source-provenance prose says
2022-11-30; previously retained original source capture stated
2022-12-01. This inconsistency is disclosed here rather than silently
editing the preregistered hypothesis after its screen.

## Binance Top-100 order-flow: recovered real inputs

The original `CRYPTO_PERPS_TOP100_1H_V1` panel exposes eight fields:
timestamp, asset, open, high, low, close, volume, funding_rate.
It lacks the real `quote_volume`, `trade_count`, and
`taker_buy_*` history needed for multiple unscreened hypotheses.
The canonical Binance USDT-M hourly lake HAS these observed fields.

The new pipeline `scripts/prepare_crypto_orderflow.py`:

1. Verifies immutable original base panel SHA:
   `a7e13ed71f8ac09a11f98bdb6d711d93f91a4021105141a2070fd79696af99bc`.
2. Reads precisely the original train/validation years/months from
   `/home/quant/dev/quant/ml4t-data/.data/store/crypto_futures_ohlcv_1h_BINANCE_UM_PERP`
   without reading any future/holdout source partition.
3. Requires 1:1 matching timestamp/asset, observed OHLCV within numeric
   tolerance and complete non-null hourly fields. Never replaces the
   original prices, funding or source files.
4. Preserves an observed **all-symbol zero-trade hour** on
   `2024-10-28T20:00:00Z` (100 bars), with `flow_valid=False`.
   BTC was independently crosschecked against Binance official archive
   `BTCUSDT-1h-2024-10-28.zip`; per-symbol independent exchange archive
   crosschecks were not completed for the other 99. No synthetic volume,
   orders, quotes or fills were created.
5. Writes a local, ignored, **write-once** research Parquet; the original
   contract and manifest remain intact.

| Evidence | Verified |
|---|---|
| Enriched bars | 2,174,396 |
| Symbols | 100 |
| Canonical raw files hashed | 2,987 |
| Study range | 2023-01-01 through 2025-06-30 23:00 UTC |
| Raw digest chain SHA256 | `39994173e004c6200b10a9e281097038812c5dd30c47aae91e632f6995819a5f` |
| Derived 93,683,354-byte Parquet SHA256 | `3045cb84e059810beec5a0f8afc2945cb7d3aee1425297a93d175126c428eafa` |
| Existing frozen OHLCV preserved | Yes, matched row-for-row |
| Warden contract approved | **No** |
| Order-flow Stage 1 executed | **No** |

Proposed **unapproved** governed contract + audit manifest are under
`docs/contract-proposals/crypto_perps_top100_orderflow_1h_h72_v1.{yaml,json}`.
These are **not protected `contracts/` entries**, not part of Warden's
approved manifest, and not usable for Stage 1 without the principal's
explicit contract-signing procedure. Its 72h label and embargo match the
original source mechanisms; the existing frozen contract uses a four-bar
label and two-bar embargo, so silently screening the 72h hypotheses on
the old contract would be scientifically wrong. The proposal inherits
same train/validation/holdout calendar, but **not** statistical
independence from previous searched variants.

## Nine zero-trial/zero-attempt hypotheses: original blocker triage

Read-only enumeration of `hypotheses` minus both `trials` and
`selection_attempts` in the source registry. Zero attempts alone
is **not** a proof of a recorded `BLOCKED_DATA` verdict.

| Registered hypotheses | Original study requirement / blocker | Recovery outcome |
|---|---|---|
| `HYP-2026-050` | Actual Binance trade size = quote turnover / trade count; PIT 7d vs 30d average trade size, 72h outcome | **Input data recovered**, 100 flow-ineligible bars masked; `BLOCKED_CONTRACT_APPROVAL` for 72h study |
| `HYP-2026-054` | Actual hourly taker-buy / taker-sell flow, 72h persistence | **Input data recovered**, flow validity masked; `BLOCKED_CONTRACT_APPROVAL` for 72h study |
| `HYP-2026-035` | Kyle's Lambda tick-level signed order flow / price impact and 72h portfolio outcome | **Still missing faithful historical trade-sign / impact observables**; 1h volume/taker aggregates are a different proxy, not original lambda |
| `HYP-2026-062` | Volume-synchronized signed trades for VPIN volume-bucket toxicity, 72h outcome | **Still missing faithful 2023-25 trade sequence**; Binance BTCUSDT tick local store has only Jan-Feb 2026, unusable for this fixed historical validation |
| `HYP-2026-115..119` | Frozen original FX daily `QUOTE_SIDE` contract lacks true bid/ask | Existing separate Dukascopy `NEXT_BAR` derivatives `HYP-2026-120..124` all **Stage3 REVISE**. Original frozen quote studies remain blocked; duplicating proxy trials cannot generate new evidence. |

Original FX counterpart full-run net Sharpes 120..124:
**0.6561, 0.6511, 0.5405, 0.5318, -0.0757**, all `REVISE`.
These are prior trials, not five new outcomes.

A registry audit also found one `failed_reservation/DATA` error for
`HYP-2026-070`: an intentionally unknown symbol
`NOT_A_REAL_SYMBOL`. This is **not** missing market data, and cannot
justify fetching a fake instrument. Its past attempt remains recorded.

The other 140 registered identities have prior selection/trial history
or require separate original-source inspection. PR #39 is the
full identity inventory; this report is **not** an assertion that
all 149 were individually inspected for every original blocking cause.

## Verified engineering gates, boundaries and remaining work

- `uv run --frozen python -m pytest -q
  tests/test_prepare_crypto_orderflow.py
  tests/test_syncster_stage1_recovery.py
  tests/test_syncster_cme.py`: **28 passed**.
- `uv run --frozen ruff check` on all added Python: **PASS**.
- `uv run --frozen warden check-worktree`: **PASS**.
- No full Warden trials, no Stage 3, no protected holdout reads,
  no broker simulations promoted as executable PnL, no live orders.
- Executing new crypto 72h Stage 1 requires principal-approved new
  contract plus source/hypothesis alignment, PIT check, mask-aware
  computation and inherited selection multiplicity. Do not run
  `warden qualify` on any prior or newly proposed lineage.
- For Kyle/VPIN obtain honest 2023-25 trade-level source (or register
  and approve separate hourly **proxy** lineages; never call them
  exact Kyle Lambda/VPIN).
- For CME, any later target-venue execution qualification requires
  independently verified continuous roll, actual instrument sizing,
  commissions, spread/impact and broker time-of-knowledge evidence.
- Warden's generic non-crypto funding perturbation defect needs
  a separate explicitly approved evaluator maintenance change; it
  must not be bypassed silently or "passed" by adding fabricated
  funding to production market observations.

**Final stage boundary:** two independently recorded CME
`STAGE1_REJECTED` attempts; a reproducible, verified market-data
recovery artifact for two future contract-governed crypto studies;
scientific qualification **NOT ACHIEVED**.
