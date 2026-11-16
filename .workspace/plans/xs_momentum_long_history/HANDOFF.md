# Handoff — starting the next thesis (2026-10-29)

State of the repo for whoever picks this up: HYP-2026-125 is parked, nothing is mid-run, and the tree
is green (`warden check-worktree` exit 0). This note is the fast path to the next hypothesis, plus the
things that already cost one session to learn.

## Ready to reuse, no setup

| artifact | what it gives you |
|---|---|
| `scripts/xs_panel_diagnostics.py` | one command, per-formation IC + HAC t, net SR at 1x/2x/+1-bar, max DD, median week, **top-5%-of-bars trim**, DSR at a stated selection count, and the pump share of the long leg. Read-only; registers nothing. |
| `data/exploratory/crypto_perps_broad_1h.parquet` | 863 symbols, 2021-01-01 → 2025-06-30, 8,442,508 rows, `sha256 19a225c9fde6dc933b142fc4fc67f9a1de5ac0473adac1f44cc4e2f71866a268`. 30 delistings inside the window. Funding observed for 530 symbols, declared 1.0 bps/8h for the other 333. |
| `scripts/build_crypto_perps_broad_1h.py` | rebuilds that panel from the lake. Read-only by default; `--execute --force` to write; refuses the sealed holdout partition with exit 3. |
| `strategies/candidates/xs_weekly_momentum_1h.py` | reference `cross_sectional_ml` skeleton: causal `pipeline.compute()`, `generate_positions()` returning `positions` + `ic_series`, delisting-aware. |
| `tests/experiments/test_xs_weekly_momentum_delisting.py` | 4 tests for the position invariants every cross-sectional perp strategy needs: flat after a symbol's last bar, exact leg count, gross/net neutrality, no weight on a missing price. |

Usage of the diagnostic tool:

```bash
uv run python scripts/xs_panel_diagnostics.py \
  --panel data/exploratory/crypto_perps_broad_1h.parquet \
  --contract contracts/crypto_perps_top100_1h_v1.yaml \
  --formations 168,672 --rebalance 168 --json-out /tmp/diag.json
```

`--rebalance` is the holding period and is deliberately independent of the formation window: a 4-week
signal can be held one week, and conflating the two silently cuts the rebalance count.

## What the tool already answers on that panel

Window 2024-07 → 2025-07 (51 weekly rebalances, contract-pinned), top-25 by dollar volume, 10 legs:

| formation | Pearson IC (t) | rank IC (t) | net SR | 2x | +1 bar | DD | median week | trim5 | DSR k=1 / honest |
|---|---|---|---|---|---|---|---|---|---|
| 1w | +0.052 (1.09) | +0.036 (0.84) | +2.35 | +2.25 | +2.33 | 28.9% | +2.26% | -10.94 | 0.991 / 0.547 |
| 4w | +0.025 (0.51) | +0.018 (0.55) | +1.30 | +1.23 | +1.19 | 23.1% | +1.42% | -11.92 | 0.903 / 0.176 |

The recent year is strong and the cross-section is weak — IC t around 1 — while the top-5% trim stays
catastrophic at both horizons. That gap is the signature to watch for.

## Principal-only, still open

1. **`contracts/` is untouched.** The broad panel has no contract; `CRYPTO_PERPS_BROAD_1H_V1` is drafted in
   `.workspace/plans/xs_momentum_long_history/CONTRACT_PROPOSAL.md` with the pin
   `19a225c9fde6dc933b142fc4fc67f9a1de5ac0473adac1f44cc4e2f71866a268`. Only the principal may place it.
2. **The holdout file was never written.** `data/holdout/` is `root:trader`; the builder stopped there by
   design. Nothing in it has been read or listed.
3. **Crypto's 2025-07 → 2026-10 holdout is unread.** One read per lineage. Do not spend it on a claim
   from a blog post.

## Three traps this family already paid for

1. **A weekly rule on hourly bars has ~1 independent bet per 168 bars.** Report the number of
   rebalances next to every Sharpe. 208 weekly bets is the most our lake can supply for crypto.
2. **Rank IC is the wrong gate for a tails-only book.** Momentum lived in the extreme quintiles while the
   25-name cross-section was flat, so a 0.015 IC gate rejects a strategy whose tails are ordered — and an
   IC gate that *passes* says nothing about the book. Check both, and check the trim.
3. **Compute "net SR after removing the best 5% of bars" before Stage 3, not after.** It took this family
   from +1.04 to -13.1 in one line and would have saved thirty runs.

## Where the full record lives

- `.workspace/plans/xs_momentum_long_history/VERDICT.md` — verdict, robustness cuts, pump test, venue decision.
- `.workspace/plans/xs_momentum_long_history/CONTRACT_PROPOSAL.md` — the unsigned contract plus the build log.
- `.workspace/hypotheses/HYP-2026-125.md` — registration, disclosures, code-change record.
- `.workspace/memory/LESSONS.md` — the post-mortem and the standing rules.
