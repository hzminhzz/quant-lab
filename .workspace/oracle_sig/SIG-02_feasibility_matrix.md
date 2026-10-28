# SIG-02 — data and governance preflight (inspection only, no decision taken)

Status: inspection complete. The three choices below the matrix are the principal's, not the
agent's; nothing here registers a hypothesis, reads a protected window, or consumes a trial.

Method: every number is read from `contracts/*.yaml`, `contracts/datasets/*.json`,
`data/exploratory/*.parquet` and `registry/registry.db` on 2026-10-28. Execution compatibility is
checked with `warden.runner.require_execution_columns`, i.e. the same call the runner makes.

## Runnable contracts (execution columns verified)

Cost is per side, `taker_fee_bps + slippage_bps`; round-trip is double. `W=4h` is the source's
fixed lookback converted to bars. `z=500` is the trailing z-score window in calendar time, measured
from bars-per-asset-per-day in the data, not assumed. Family floor is the inherited
`prior_search_floor(contract)` — `k_eff` and expected-max-Sharpe noise, taken from the ledger.

| Contract | Assets | Span (exploratory) | Cost/side | Label | W=4h | z=500 | Family floor |
|---|---|---|---|---|---|---|---|
| `INDICES_1M_V1` | 6 | 2024-01-01..2025-06-30 | 0.5bps | 15 | 240 bars | 0.5d | clean (1.0 / 0.00) |
| `INDICES_5M_V1` | 6 | 2015-01-01..2025-06-30 | 1.0bps | 96 | 48 bars | 2.6d | clean |
| `INDICES_30M_V1` | 6 | 2015-01-01..2025-06-30 | 1.0bps | 16 | 8 bars | 15.6d | clean |
| `INDICES_1H_V1` / `_V2` | 6 | 2015-01-01..2025-06-30 | 1.0bps | 8 | 4 bars | 30.5d | **51.25 / 1.88** |
| `INDICES_4H_V1` | 6 | 2015-01-01..2025-06-30 | 1.0bps | 2 | 1 bar | — | clean |
| `INDICES_1D_V1` | 6 | 2015-01-01..2025-06-30 | 1.0bps | 5 | 0.2 bar | — | clean |
| `CRYPTO_PERPS_1M_V1` | 8 | 2024-01-01..2025-06-30 | 6.0bps | 15 | 240 bars | 0.5d | clean |
| `CRYPTO_PERPS_5M_V1` | 8 | 2024-01-01..2025-06-30 | 6.0bps | 48 | 48 bars | 1.7d | clean |
| `CRYPTO_PERPS_30M_V1` | 8 | 2024-01-01..2025-06-30 | 6.0bps | 8 | 8 bars | 10.4d | clean |
| `CRYPTO_PERPS_1H_V1` | 8 | `crypto_perps_1h.parquet` 2020-01-01..2025-03-25 (val 2025-01-26..2025-03-25) | 6.0bps | 4 | 4 bars | 20.8d | **7.80 / 1.45** |
| `CRYPTO_PERPS_1H_V2` | 8 | `crypto_perps_1h_v2.parquet` 2024-01-01..2025-06-30 (val 2025-01-01..2025-07-01) | 6.0bps | 4 | 4 bars | 20.8d | **7.80 / 1.45** |
| `CRYPTO_PERPS_4H_V1` | 8 | 2024-01-01..2025-06-30 | 6.0bps | 2 | 1 bar | — | clean |
| `CRYPTO_PERPS_1D_V1` | 8 | 2024-01-01..2025-06-30 | 6.0bps | 5 | 0.2 bar | — | clean |
| `CRYPTO_PERPS_TOP100_1H_V1` | 100 | 2023-01-01..2025-06-30 (val 2024-07..2025-07) | 6.0bps | 4 | 4 bars | 20.8d | **7.80 / 1.45** |
| `CRYPTO_MAJORS_1H_V1` | 23 | 2020-01-01..2024-12-31 (val 2023-01..2025-01) | 6.0bps | 4 | 4 bars | 20.8d | **7.80 / 1.45** |
| `MULTI_ASSET_5M_V1` | 17 | 2015-01-01..2025-06-30 | 1.0bps | 96 | 48 bars | 2.6d | clean |
| `MULTI_ASSET_30M_V1` | 17 | 2015-01-01..2025-06-30 | 1.0bps | 16 | 8 bars | 15.6d | clean |
| `MULTI_ASSET_1H_V1` | 17 | 2015-01-01..2025-06-30 | 1.0bps | 8 | 4 bars | 30.5d | **22.50 / 1.22** |
| `MULTI_ASSET_1D_V1` | 17 | 2015-01-01..2025-06-30 | 1.0bps | 5 | 0.2 bar | — | **45.61 / 1.39** |
| `METALS_1M_V1` | 2 (XAU, XAG) | 2024-01-01..2025-06-29 | 0.6bps | 15 | 240 bars | 0.5d | clean |
| `COMMODITIES_1M_V1` | 1 (CL) | 2024-01-01..2025-06-30 | 1.0bps | 15 | 240 bars | 0.5d | clean |

Floors are recorded facts, not selection criteria: a clean family floor is **not** a reason to
choose a frequency, and choosing a contract to dodge an inherited floor would be selecting the gate
rather than the hypothesis.

## Not runnable

All seven `FX_*` contracts are `QUOTE_SIDE` and their Parquet has no `bid`/`ask` columns;
`require_execution_columns` raises `ExecutionDataError` for every one. The failure is not specific
to 5m or 30m — the **entire FX asset class is currently unusable**, which also removes the FX leg
of the source's cross-asset claim and the C17/C25 candidates.

## Constraints the matrix imposes on the source design

1. **W=4h cannot exist on 4h or 1d contracts** — one bar (or 0.2) gives a single path point, and a
   depth-2 signature needs at least two. W=24h would be 6 bars at 4h.
2. **Bar-count windows on gapped index data are clock-longer than stated.** Indices trade
   ~189.5 bars/asset/day at 5m and ~16.4 at 1h (no Saturday rows, Sunday reopen). So W=4h is
   48 bars = ~6.1 calendar hours at 5m and 4 bars = ~5.9 hours at 1h, versus exactly 4h on the
   24/7 crypto contracts. Either the window is defined in bars (source-faithful, clock-stretched)
   or in clock time (not source-faithful); SIG-03 must pick and say so.
3. **Retracted: `CRYPTO_PERPS_1H_V2`'s window is covered.** The earlier claim read
   `crypto_perps_1h.parquet` (V1's file) instead of the V2-pinned
   `crypto_perps_1h_v2.parquet`, which spans 2024-01-01..2025-06-30 23:00 and fully covers
   `val_start 2025-01-01` / `val_end 2025-07-01`; its pinned digest
   `7d8751eb…13e2` matches the file on disk. What is true, and is a property of V1 alone, is that
   `CRYPTO_PERPS_1H_V1` validates over **2025-01-26..2025-03-25** — two months, pinned exactly to
   where `crypto_perps_1h.parquet` stops. Two months is a short validation window, not an
   undeclared gap.
4. Exploratory files were checked for post-`holdout_start` rows: none of the contracts above carries
   any (e.g. `crypto_majors_1h` stops 2024-12-31, `crypto_perps_top100_1h` stops 2025-06-30, both
   before their respective `holdout_start`). The holdout Parquets are the unreadable ones.
5. Universe mismatch is unchanged from the inventory: 6 index assets and 8 crypto perps against the
   source's 27 instruments across four classes. `MULTI_ASSET_*` widens this to 17 but mixes indices,
   FX, metals and crude, executed `NEXT_BAR` on close/open rather than quote-side.

## Decisions the principal owns (SIG-02 acceptance gate)

| # | Decision | Why it cannot be defaulted |
|---|---|---|
| D1 | one contract and frequency | fixes universe, cost floor, session clock and validation window |
| D2 | one-bar horizon against the declared `label_horizon_bars` (8/16/48/96/240 depending on contract) | declared semantics of the contract; the engine's PnL is one-bar regardless, so this is a validation-semantics choice, not an engine one |
| D3 | new contract, or accept a declared horizon that does not match the source's 1-bar hold | new contract = human authorization, fake version bump is forbidden |
| D4 | the SIG-01 fidelity items: strict-pairs vs standard convention, path centring, 5m every-third-bar forward-fill | these change the signal; they belong in the pre-registration, not in the implementation |

## Governance state

- `data/holdout/` is `0750 root:trader`; the agent cannot list or resolve it. No protected row read.
- No hypothesis registered, no trial consumed, `trials_used` unchanged by this ticket.
- `uv run warden check-worktree` returns 2 on the `AGENTS.md` hash, so `verify`/`screen`/`backtest`
  refuse. Regenerating the manifest is a human-only gate (`approver: human-operator` is recorded in
  `.warden_manifest.sha256.meta.json`); the agent must not self-attest it. Unblock with either:

```bash
cd /home/quant/dev/quant-lab && uv run warden generate-manifest --human-approve-manifest
# or, if you hold a signing key: WARDEN_HUMAN_SIGNING_KEY=... uv run warden generate-manifest
```
