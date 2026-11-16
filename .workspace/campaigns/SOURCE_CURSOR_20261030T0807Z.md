# Source Research Cursor — 2026-10-30T08:07Z

Status: BATCH_CHECKPOINT — continue with the next distinct source lead on the next scheduled run.

## Integrity snapshot
- Governance guard: `READY_FOR_DISTINCT_RESEARCH` at 2026-10-30T08:06:51.001296Z.
- Registry snapshot: 139 hypotheses; 71 full trials.
- The two historical XSRR runs and HYP-2026-126 remain quarantined/individual unresolved work and are excluded from this queue.
- The legacy `src.social_campaign` runner is source-discovery only for this program; it is not an execution path.

## Terminal and frozen
| Key | Status | Evidence | Next action |
|---|---|---|---|
| HYP-SOC-BTA-NQMON-20261030 | REJECTED | ATT-f6e9f68b4805e076-001; PF 1.1384176199013911; bootstrap p 0.01; time in market 0.1710651612 | Never rerun, tune, invert, or reclassify. |
| HYP-SOC-QC-TOM-ES-20261030 | REJECTED | ATT-b56aee52fba281bc-001; PF 0.9286388123227758; bootstrap p 0.54; negative edge | Never rerun, tune, invert, or reclassify. |
| HYP-2026-126 | BLOCKED_EXECUTION | Prior verify timed out; guard scope says candidate-only unresolved, with 2 Stage-1 attempts. | Do not replay without a reconciled durable job ID. |
| HYP-2026-128 / RUN-XSRR-001 / RUN-XSRR-002 | BLOCKED_GOVERNANCE | Explicit append-only quarantine; 159 unregistered diagnostic cells retained. | Never rerun, qualify, or use as clean evidence. |

## Newly triaged source leads
| Dedupe key | Original source and date | Exact source rules / issue | Status | Precise next action |
|---|---|---|---|---|
| backtest-nasdaq100-weakness-rsi3-atrp63-limit1-target175 | Dave Johnson, “Buying Weakness on the Nasdaq 100”, 2026-08-06, https://backtest.substack.com/p/buying-weakness-on-the-nasdaq-100 | Nasdaq-100 point-in-time members; top half ATRP(63); RSI(3)<10 at close; next-session buy limit 1% below low; nightly exit limit 1.75% above close; one position/symbol. | BLOCKED_DATA | Obtain a documented point-in-time Nasdaq-100 constituent panel and intraday/limit-fill convention. Do not silently map the stock-universe system to an index CFD. |
| gurufinance-btc-donchian20-10-atr3 | Ayrat Murtazin, “The Simplest Trend-Following System…”, 2026-09-05, https://gurufinanceinsights.substack.com/p/the-simplest-trend-following-system | Long at close above prior 20-day high; exit at close below prior 10-day low or high-since-entry minus 3×ATR(20). | BLOCKED_SOURCE | The author published after the 2015–2026 interval reported. Preserve it as a current rule lead; do not call older validation history unseen. Require an eligible future-only evaluation window and contract provenance before registration. |
| trh-dirty-carry-middle | pedma, “Dirty Crypto Carry Part 3”, 2026-04-28, https://www.tradingresearchub.com/p/dirty-crypto-carry-part-3-the-moneys | Paid article exposes only a five-day funding-persistence observation; entry, exit, universe, sizing, and complete signal rules are absent. | BLOCKED_SOURCE | Do not infer missing rules or use this as a funding strategy. Seek a full public original rule set. |
| stoic-rsi-roc | Stoic Research, “1.3 RSI and Rate of Change”, catalog URL https://stoicresearch.substack.com/p/post-13-rsi-and-rate-of-change | Original post could not be read through the public capture in this run; the prior Stoic momentum lineage is already rejected. | BLOCKED_SOURCE | Obtain complete original numerical entry and exit rules, then perform duplicate/lineage review before any candidate is created. |
| trh-binary-signal-efficiency | pedma, “Binary Signal Efficiency: When Scalable Code Bites Back”, catalog lead | Full original rule extraction is still pending. | PENDING_SOURCE_REVIEW | Read the full original post and extract market, frequency, entry, exit, holding/rebalance, author-side search, capture SHA, and all missing fields before evaluating data fit. |

## Dedupe rule
A source+rules+market/contract+parameterization combination may enter Warden once only. An adapted instrument, opposite sign, reordered filter, or new identifier does not make a prior selection attempt independent.

## Next queue
1. Read the full original Binary Signal Efficiency post.
2. Review a new non-Stoic archive lead with complete numerical entry and exit rules.
3. Only after source fidelity, data fit, and a new governance check: consider a unique hypothesis, then guarded registration and durable verification.

No Warden registration, screen, backtest, or trial was started by this checkpoint.
