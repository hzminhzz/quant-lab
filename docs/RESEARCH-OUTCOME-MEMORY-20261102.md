# Durable failed-strategy memory and research backlog — 2026-11-02

## Decision

**Do not re-run Stage-1-rejected or Stage-3-REVISE hypotheses merely because market data was refreshed.** The original validation sample, parameters, costs and trial/selection debt remain consumed. Treat repaired code, independent datasets, changed horizons and fresh periods as separately preregistered, auditable lineages—not backdoor resets of failed studies.

Canonical per-identity history is the append-only Warden SQLite registry. PR evidence not yet imported into that registry is pinned separately in [branch-result-index-v1.json](research-outcomes/branch-result-index-v1.json). The index is versioned alongside its original PR evidence; it does **not** impersonate Warden records or create trials.

At inspection (2026-11-02), `/home/quant/dev/quant-lab/registry/registry.db` contains:

| Population | Count | Action |
|---|---:|---|
| Registered historical hypotheses | **149** | Audit by identity, contract, strategy family and lineage |
| Historical trials | **75** | Retain all results, including failures |
| Historical selection attempts | **230** | Preserve for DSR and repeated search correction |
| Registered hypotheses with zero trials | **108** | **Not** 108 backtests to run |
| Registered hypotheses lacking native Stage 3 | **115** | Most were already falsified or blocked |
| Already Stage-1 REJECT without Stage 3 | **99** | Historical negative: do **not** automatically backtest |
| Early-failure or Stage-1 trial records without Stage 3 | **7** | Do not classify as untouched |
| Stage-1 PASS without Stage 3 in source registry | **0** | Every recorded source-registry Stage-1 passer already has a full trial |
| **Neither trials nor selection attempts** | **9** | See true unattempted list below |

The **nine truly unattempted original hypotheses** are:

| Original ID | Original data issue | Data recovery status |
|---|---|---|
| `HYP-2026-035` Kyle Lambda | Signed 2023–25 trade sequence and impact estimates absent | SOURCE INCOMPLETE; no faithful tick-level Stage 1 |
| `HYP-2026-050` trade-size granularity | Original contract 4h target, hypothesis requires 72h; historical code invented trade counts | Actual Binance `quote_volume` and `trade_count` recovered. Separate corrected 72h ATS preview failed; governed 72h contract **not signed** |
| `HYP-2026-054` taker imbalance | Original contract 4h, hypothesis requires 72h; historical code used candle-sign proxy | Actual `taker_buy_quote_volume` recovered. Separate corrected 72h NTR preview failed; governed 72h contract **not signed** |
| `HYP-2026-062` VPIN | Volume-synchronized signed trade sequence absent | SOURCE INCOMPLETE; no faithful 2023–25 VPIN |
| `HYP-2026-115…119` FX S01–S05 | Original QUOTE_SIDE contract lacks observed bid/ask | Original five remain blocked. Separate Dukascopy NEXT_BAR counterparts `HYP-2026-120…124` already **Stage-3 REVISE**; do not duplicate them |

## Off-main results must not be forgotten

Versioned [JSON evidence index](research-outcomes/branch-result-index-v1.json) covers **15** named off-main research identities, across still-open [PR #40](https://github.com/hzminhzz/quant-lab/pull/40), [PR #41](https://github.com/hzminhzz/quant-lab/pull/41), and [PR #42](https://github.com/hzminhzz/quant-lab/pull/42):

| Source | Historical outcomes | What not to do |
|---|---|---|
| PR #40 | Two CME ES/NQ native Stage-1 REJECT; two Binance corrected 72h order-flow *provisional* failures | Don't relabel preliminary tests as native screens; don't repeat under same dataset/parameters |
| PR #41 | Four BTC/ETH and 6h-trend native Stage-1 REJECT, one pooled Ridge price-pressure REJECT | Preserve every negative outcome and original search history |
| PR #41 | Low-vol and its correlated 24h rebalance descendant both native Stage-1 PASS; **no full Stage-3 trial** | Don't call either qualified; both share the **same** IC vector; count ten prior training-only factor probes |
| PR #42 | Three 4h cross-sectional Stage-1 REJECT; sign-only reversal Stage-1 PASS followed by **Stage-3 REVISE** (net Sharpe **−8.1754**) and failed six-check audit | Don't rerun the failed reversal or promote positive gross Sharpe as executable alpha |

Of these 15 off-main identities, **13 are negative/unqualified for the exact evaluated configuration**, and **two are Stage-1 predictive passes without economic qualification**. They are **not added** to the above 149 primary registry identities, and any future ingestion must preserve original trial and selection IDs, source hashes, duplicate checks and DSR search debt.

### The remaining promising but not economically qualified study

`HYP-ARXIV-XS-LOWVOL-20261102` yielded mean cross-sectional IC **0.052817**, HAC t **12.020**. Its `HYP-ARXIV-XS-LOWVOL-24H-20261102` execution-only revision cut turnover by about 52%, generated preliminary net Sharpe **0.9104** and 2x-cost Sharpe **0.1528**. Both are below the frozen economic test requirements (net Sharpe above 1.2 and doubled-cost Sharpe above 0.5). The source audit also found **20 active asset-hours without a complete executable next-open transition**, plus PIT Top-100 universe/survivorship uncertainty. No full trial was consumed in that branch; before any new scientific claim, resolve real execution gaps and test a **new, previously untouched validation period or prospective observations** with separate preregistration. Do not tune the existing validation split.

## Working data versus unresolved repairs

- **Recovered usable exploratory research inputs:** SHA-pinned Syncster CME ES/NQ 15m; Dukascopy NEXT_BAR FX/index proxy where the contract matches; Binance Top-100 real 1h quote/trade/taker aggregate fields in PR #40.
- **Still unfixed for exact original methods:** 2023–25 signed trade sequences (Kyle/VPIN); original quote-side FX bid/ask; unsigned 72h Binance order-flow contract. Corrected PR #40 adapters do not rewrite the two original `cs_*` candidates left in a dirty primary checkout.
- **Engineering/data caveats, not permission to retest:** `INDICES_1H_V1` existing checksum mismatch (V2 verifies); absent tracked `MULTI_ASSET_1D_V1` contract in clean checkouts; Warden's fixed per-bar PF=1.15 has a frequency-comparability problem (PR #41). Frozen evaluator and contracts were not changed by this work.
- A data refresh after the frozen validation end does **not** create a new independent observation for an old contract. If all data were fetched for an extended interval, a new contract and explicit principal-approved experimental cutoffs are still required. Protected holdouts remain sealed without explicit approval.

## Machine-enforced replay prevention

From a checkout that has access to the existing authoritative Warden registry:

```bash
uv run --frozen python -m scripts.research_outcome_memory --inventory
uv run --frozen python -m scripts.research_outcome_memory --check-spec experiments/queue/example.yaml
```

The `--inventory` command is read-only: it dynamically joins hypotheses, trials and selection attempts, then lists the nine truly unattempted original mechanisms separately from 15 off-main evidence records. The checker refuses any identical hypothesis+contract with a frozen sidecar negative/PASS-but-unqualified, any Warden hypothesis with existing trials or attempts, unresolved original source blockers, contract ID conflicts, and obvious registered module aliases. The checker **never** authorizes a contract or trading: a new candidate still goes through normal Warden source, causality, cost and governance gates.

`scripts/run_hourly_pipeline.sh` now calls the checker **before** `warden verify` and `warden screen`, under the existing lock; blocked queued specs are moved to its existing `experiments/active/failed/` folder and recorded in the job log, rather than consuming an extra validation attempt. Missing/corrupted registry or index fails closed. A genuinely new hypothesis with new data is eligible for further Warden checks, **not automatically approved**.

**Important integration boundary:** This PR modifies the hourly runner only in its proposed Git branch. It does **not** claim deployment until merged and actual hourly service execution is verified. The canonical primary registry was read-only and unchanged throughout this inventory.

Source reports: PR #40 `docs/RECOVERY-EXECUTION-20261102.md`, `docs/BINANCE-ORDERFLOW-RECOVERY-20261102.md`; PR #41 `docs/ARXIV-STAGE1-EXECUTION-20261102.md`, `docs/ARXIV-LOWVOL-STAGE1-PASS-20261102.md`; PR #42 `docs/FOUR-XS4H-EXECUTION-20261102.md`.
