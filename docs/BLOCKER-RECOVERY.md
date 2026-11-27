# Autonomous blocker recovery — quant-lab

Adapted from [copytrade-pm PR #17](https://github.com/hzminhzz/copytrade-pm/pull/17), merge `d3e0ce916c2f0b64ad36593f55b261607ec0cbdd`. This is an **execution/continuation protocol**, not an override of Warden, frozen contracts, or statistical thresholds.

## RECOVER — execute to objective completion

On `BLOCKED`, source/API failure, missing bid/ask, failed test, insufficient coverage, tool outage, `JUDGE_UNAVAILABLE`, invalid hypothesis, or Warden rejection:

1. **Keep the requested objective.** Record the failing stage, exact error, original hypothesis/contract ID, dataset path and SHA, decision cutoff, evidence location, and current Git/other-session changes. Protect immutable trial and raw-data records.
2. **Inspect effects, then diagnose narrowly.** Read the existing spec/attempt receipt before any retry. A timeout or lost response may follow a committed admission. Reproduce an engineering failure with an isolated fixture or a proven pre-admission check; never rerun scored work to diagnose it. Distinguish implementation fault, data/contract mismatch, external authority/outage and scientific falsification. Check actual catalog/quality evidence before declaring data unavailable.
3. **Compare remedies.** Inspect the native source and importer, a separately pinned CME source (see [SYNCSTER-CME-RESEARCH.md](SYNCSTER-CME-RESEARCH.md)) or market proxy (see [DATA-PROXY.md](DATA-PROXY.md)), and an unaffected symbol/window/candidate or prospective-data path. Choose the smallest reversible remedy. Respect provider quotas and frozen scopes.
4. **Implement and verify.** Correct the authorized catalog, parser, ingestion, test or configuration. A changed data basis needs a separately versioned contract and governed intent/revision; economically related work retains its existing lineage and selection debt. Preserve frozen bytes and record drift instead of replacing a failed hash. Test the original engineering failure and required causality/cost/provenance behavior in isolation.
5. **Resume the original campaign.** Call the real Warden CLI with the approved contract. Record the screen/attempt and move to the next eligible dependency. If one window/contract cannot be supported, label *that result* blocked and continue other eligible work. A hypothesis genuinely falsified at Stage 1 remains rejected; move to a **distinct thesis**, preserving selection counts and budgets.
6. **Close against the objective.** Report implemented changes, actual tests, trial/evidence IDs, the research-stage result, what remains unqualified, and the precise action only a principal/external provider can take. A chat session does not continue in the background without a verified automation.

Bound technical retries before admission. After a possibly admitted effect, inspect/reconcile its receipt; no new request, source or identifier may evade the original charge. Change a diagnosis when repeated checks add no information. A source/scope change still needs its applicable frozen policy and intent. Repair authorized reversible issues autonomously and resume the objective.

## Blocker routing

| Observation | Required recovery, then resume | Legitimate narrow status |
| --- | --- | --- |
| `QUOTE_SIDE` missing bid/ask, but vetted OHLCV exists | Preserve quote contract; use a separately versioned `NEXT_BAR` CFD proxy contract with explicit costs for Stage 0/1 and proxy-only Stage 3 stress | Original quote-lineage `BLOCKED_EXECUTION_DATA`; proxy `PROXY_SCREEN` / `PROXY_STRESS_ONLY` |
| Exchange futures signal history needed | Prefer the locally SHA-pinned Syncster CME ES/NQ 15m contract if appropriate; audit session and roll integrity first. Otherwise use vetted Dukascopy CFD as Stage 0/1 proxy and proxy-only Stage 3; check actual required mechanism | `CME_SOURCE_PINNED` / `PROXY_SCREEN`, **not** blanket `BLOCKED_DATA` |
| Missing most-recent data **outside** the fixed historical screen | Run on the approved historical study window; never require bars after its `val_end`; preserve protected holdout | `FUTURE_TAIL_NOT_NEEDED` |
| Corrupt candles, missing bars inside validation, ambiguous timestamp or stale catalog | Repair upstream/verify a bounded source generation; otherwise isolate the affected instrument and propose a separately frozen study | `BLOCKED_SOURCE` for affected dataset |
| No factual bid/ask or target-venue fill economics | Model spread/fee/slippage under a proxy contract, run sensitivity; withhold executable fill and capacity claims | `PROXY_ECONOMICS_ONLY` |
| Missing perpetual funding, roll/contract continuity, financing or PIT source features required by the thesis | Acquire true observations or restrict to independently supported research questions. Never replace unknown economics with zeros | `BLOCKED_REQUIRED_FEATURE` |
| Vendor quota/network timeout or judge unavailable | Bounded backoff; use authorized local catalog/cache and continue offline independent work; record `JUDGE_UNAVAILABLE` if applicable | `EXTERNAL_BLOCKED` only for the dependent operation |
| Broken tests, import/package error, wrong source location | Inspect actual repo and dependencies; fix, test, resume | `ENGINEERING_BLOCKED` only if no safe remedy exists |
| Genuine Stage 1 rejection, DSR failure or robustness failure | Record falsification/attempt; proceed with next distinct approved hypothesis without relaxing thresholds or resetting counters | `FALSIFIED` for the tested hypothesis |
| Protected effect needs additional authority | Complete a reviewable packet with exact subjects, evidence, consequences and missing authority; continue independent eligible work; the principal decides through an authenticated available channel | `AUTHORIZATION_REQUIRED` for the precise effect |

**Fail closed on the affected claim, not on the entire research program.**

## Data-recovery authority

- The principal's **2026-11-01** standing approval covers **Dukascopy CFD OHLCV as a research proxy through proxy-only robustness** for suitable index/FX strategies, not exchange futures/venue fills, and not changing an existing frozen Warden contract. It is not approval for live orders, protected holdout reads, silent data substitutions, weaker thresholds, arbitrary vendor charges, or deployment.
- Warden `src/warden/provenance.py` validates exact dataset path/digest/manifest and does **not** permit cross-contract fallback. A source change therefore needs a **new versioned contract, dataset manifest and provenance pin** approved under the existing contract-governance procedure. Existing history remains immutable. The agent should prepare and verify this work rather than repeatedly asking whether CFD data may be used.
- Historical CFD may be actual `verified_market` data for its own feed; that label is **not** a claim that futures/target-broker economics were observed. Require `resolve_dataset(...).eligible_market` to be true and inspect the reason **before** a market-data-backed screen; the current resolver may return ineligible metadata without raising. Keep proxy-only and `TARGET_VENUE_UNVERIFIED` explicit in summaries. These labels are report annotations, not persisted Warden verdicts.
- `NEXT_BAR` uses observed next-bar **open** prices; a signal at bar `t` cannot fill at a price from `t` before it was known. Protect `available_at`/decision-clock ordering and the Warden execution delay. Do not synthesize bid/ask or backdate publication times.
- Native Warden statistics, cost floors, RG-03/04 candidate/lineage limits and raw operation accounting, inherited selection debt, 1.5x/2x cost stress, +1-bar latency, six-check audit and one-shot holdout rules remain binding. The same frozen screen/full candidate shares one slot; a fixed stress is not a free selectable revision. Research passes and live-trading authorization remain different claims.

## Source of truth and closeout

Current code, the `ml4t-data` catalog and live stored quality evidence, SHA-pinned datasets and registry are authoritative for present facts. Repository docs govern process; OptMem is recall, not proof. An unavailable independent judge permits reversible work, never invented review or self-approval of a protected action.

**Durable failed-strategy memory (2026-11-02):** Before choosing, registering or screening another candidate, run `uv run --frozen python -m warden_ops.research_outcome_memory --inventory`, consult `docs/RESEARCH-OUTCOME-MEMORY-20261102.md`, and check the separate `docs/research-outcomes/branch-result-index-v1.json` for PR #40–42 outcomes not yet in the primary SQLite. Warden's advance path consults outcome memory before scientific progression, refusing known identical failures, duplicate identities and unresolved prior blockers. A repository wrapper does not prove that a host schedule is installed or active. No dataset refresh, hypothesis alias or branch change silently turns REJECT/REVISE into a new opportunity or resets trial/selection debt. A distinct, independently justified study needs prospective governed scope/cutoffs; related work retains its lineage and debt. A Stage 1 PASS is not a Stage 3 qualification.

Resume from the current receipt state using [WORKFLOW.md](WORKFLOW.md). Verify integrity and prospective prerequisites before a new action; retrieve existing results instead of repeating them. NEW approved work may enter the funnel, an identical STAGE1_PASS may advance once, and REJECT/terminal/uncertain work follows its recorded disposition or reconciliation path. A proxy result remains proxy evidence. Preserve the objective until its completion condition is met or only specific external/principal blockers remain. The [target control contracts](AGENT-CONTROL-CONTRACTS.md) formalize this loop; they are not yet executable capabilities.
