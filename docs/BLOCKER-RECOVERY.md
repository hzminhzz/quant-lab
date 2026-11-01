# Autonomous blocker recovery — quant-lab

Adapted from [copytrade-pm PR #17](https://github.com/hzminhzz/copytrade-pm/pull/17), merge `d3e0ce916c2f0b64ad36593f55b261607ec0cbdd`. This is an **execution/continuation protocol**, not an override of Warden, frozen contracts, or statistical thresholds.

## RECOVER — execute to objective completion

On `BLOCKED`, source/API failure, missing bid/ask, failed test, insufficient coverage, tool outage, `JUDGE_UNAVAILABLE`, invalid hypothesis, or Warden rejection:

1. **Keep the requested objective.** Record the failing stage, exact error, original hypothesis/contract ID, dataset path and SHA, decision cutoff, evidence location, and current Git/other-session changes. Protect immutable trial and raw-data records.
2. **Diagnose narrowly.** Reproduce once with a focused check. Distinguish (a) implementation fault, (b) missing/stale/invalid data, (c) a *mismatched data-purpose contract*, (d) external authorization/outage, and (e) actual scientific falsification. Check the existing `ml4t-data` catalog/quality reports before assuming a dataset is unavailable.
3. **Compare remedies.** Inspect the native source and importer, a separately pinned market proxy (see [DATA-PROXY.md](DATA-PROXY.md)), and an unaffected symbol/window/candidate or prospective-data path. Choose the smallest reversible remedy. Respect provider quotas and frozen scopes.
4. **Implement and verify.** Correct the data catalog, parser, source ingestion, test, or configuration; for a different data basis use a **new contract ID and new audit lineage**, never edit a frozen source or paper over a SHA mismatch. Test the original failure case, the quality checks, and Warden's required causality/cost/provenance behavior.
5. **Resume the original campaign.** Call the real Warden CLI with the approved contract. Record the screen/attempt and move to the next eligible dependency. If one window/contract cannot be supported, label *that result* blocked and continue other eligible work. A hypothesis genuinely falsified at Stage 1 remains rejected; move to a **distinct thesis**, preserving selection counts and budgets.
6. **Close against the objective.** Report implemented changes, actual tests, trial/evidence IDs, the research-stage result, what remains unqualified, and the precise action only a principal/external provider can take. A chat session does not continue in the background without a verified automation.

Bound retries. After repeated same-cause failures, change the source or diagnosis rather than repeating the same request. Repair routine reversible issues without another approval when the principal has already authorized the research goal.

## Blocker routing

| Observation | Required recovery, then resume | Legitimate narrow status |
| --- | --- | --- |
| `QUOTE_SIDE` missing bid/ask, but vetted OHLCV exists | Preserve quote contract; use a separately versioned `NEXT_BAR` CFD proxy contract and positive cost assumptions for preliminary screens | Original quote-lineage `BLOCKED_EXECUTION_DATA`; proxy `PROXY_SCREEN` |
| Exchange **futures** market data absent, but Dukascopy index CFD is available | Check catalog/QA and causal alignment; use CFD proxy for Stage 0/1. Native futures or target-broker quotes are required only when the later claim actually depends on their execution | `TARGET_VENUE_UNVERIFIED`, not blanket `BLOCKED_DATA` |
| Missing most-recent data **outside** the fixed historical screen | Run on the approved historical study window; never require bars after its `val_end`; preserve protected holdout | `FUTURE_TAIL_NOT_NEEDED` |
| Corrupt candles, missing bars inside validation, ambiguous timestamp or stale catalog | Repair upstream/verify a bounded source generation; otherwise isolate the affected instrument and propose a separately frozen study | `BLOCKED_SOURCE` for affected dataset |
| No factual bid/ask or target-venue fill economics | Model spread/fee/slippage under a proxy contract, run sensitivity; withhold executable fill and capacity claims | `PROXY_ECONOMICS_ONLY` |
| Missing perpetual funding, roll/contract continuity, financing or PIT source features required by the thesis | Acquire true observations or restrict to independently supported research questions. Never replace unknown economics with zeros | `BLOCKED_REQUIRED_FEATURE` |
| Vendor quota/network timeout or judge unavailable | Bounded backoff; use authorized local catalog/cache and continue offline independent work; record `JUDGE_UNAVAILABLE` if applicable | `EXTERNAL_BLOCKED` only for the dependent operation |
| Broken tests, import/package error, wrong source location | Inspect actual repo and dependencies; fix, test, resume | `ENGINEERING_BLOCKED` only if no safe remedy exists |
| Genuine Stage 1 rejection, DSR failure or robustness failure | Record falsification/attempt; proceed with next distinct approved hypothesis without relaxing thresholds or resetting counters | `FALSIFIED` for the tested hypothesis |
| Protected holdout, contract authority, live trading or irreversible change needs approval | Continue independent eligible work; request explicit principal approval only for that protected action | `AUTHORIZATION_REQUIRED` for the protected action |

**Fail closed on the affected claim, not on the entire research program.**

## Data-recovery authority

- The principal's **2026-11-01** standing approval covers **Dukascopy CFD OHLCV as a preliminary research proxy** for suitable index/FX strategies, not exchange futures/venue fills, and not changing an existing frozen Warden contract. It is not approval for live orders, protected holdout reads, silent data substitutions, weaker thresholds, arbitrary vendor charges, or deployment.
- Warden `src/warden/provenance.py` validates exact dataset path/digest/manifest and does **not** permit cross-contract fallback. A source change therefore needs a **new versioned contract, dataset manifest and provenance pin** approved under the existing contract-governance procedure. Existing history remains immutable. The agent should prepare and verify this work rather than repeatedly asking whether CFD data may be used.
- Historical CFD may be actual `verified_market` data for its own feed; that label is **not** a claim that futures/target-broker economics were observed. Keep `PROXY_SCREEN_PASS` and `TARGET_VENUE_UNVERIFIED` explicit in summaries.
- `NEXT_BAR` uses observed next-bar **open** prices; a signal at bar `t` cannot fill at a price from `t` before it was known. Protect `available_at`/decision-clock ordering and the Warden execution delay. Do not synthesize bid/ask or backdate publication times.
- Native Warden statistics, cost floors, 3-trial cap, trial/selection history, 1.5x/2x cost stress, +1-bar latency, six-check audit and one-shot protected holdout rules stay unchanged. Research passes and live-trading authorization remain different claims.

## Source of truth and closeout

Current code, the `ml4t-data` catalog and live stored quality evidence, SHA-pinned datasets and registry are authoritative for present facts. Repository docs govern process; OptMem is recall, not proof. An unavailable independent judge permits reversible work, never invented review or self-approval of a protected action.

Check the actual experiment with `uv run warden verify <experiment.yaml> --json`, `uv run warden screen <experiment.yaml> --json` and `uv run warden check-worktree` as appropriate. The agent must follow failures through to a verified remedy and resume the originally requested research. The protocol's own document change does not count as a completed research campaign.
