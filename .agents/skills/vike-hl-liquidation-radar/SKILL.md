---
name: vike-hl-liquidation-radar
description: Where Hyperliquid liquidations are likely - which liquid symbols have the most open interest near liquidation, where one symbol's liquidation clusters sit around the mark, which liquidations just happened, and a live feed of positions entering the liquidation zone, of liquidations as they print and of leveraged position changes. Use when the user asks "which coins are closest to a liquidation cascade" / "where are ETH liquidations clustered" / "liquidation heatmap" / "who just got liquidated on Hyperliquid" / "alert me to big liquidations".
allowed-tools: Bash(vike-data:*)
---

# vike-hl-liquidation-radar

Three reads of the same idea and a live feed, 1 credit per call. Risk and the heatmap come from 5-minute position snapshots, so they are inferred; the liquidation events are read from the liquidated accounts' own fills since 2026-08-03 (inferred from snapshots before).

## Chain

```bash
vike-data hl liquidations --view risk --min-value-usd 50000000 --json       # symbols with $50M+ open, ranked by percent_risk
vike-data hl liquidations --view heatmap --symbol ETH --bins 20 --json      # liquidation value per price bin
vike-data hl liquidations --view fills --symbol ETH --direction long --limit 50 --json   # recent long liquidations
vike-data candles hl ETH --interval 1h --limit 48 --json                    # distance from the mark to the clusters
```

## Reading

- **`risk`**: rows `symbol`, `total_value_usd`, `risk_value_usd`, `percent_risk` (a **fraction**, 0.234 = 23.4%), `positions_count`, `at_risk_count`. A position is at risk when it sits within half of its liquidation buffer. The ranking is by `percent_risk`, so without `--min-value-usd` thin HIP-3 and micro-cap markets lead. Add `--axis size|pnl|pnl_abs --cohort <label>` to ask the same of one cohort.
- **`heatmap`** (`--symbol` required, `--bins` 5-500): bins are equal-width over +-50% of `mark_price`, each with `liquidation_value_usd`, `positions_count`, `long_value_usd`, `short_value_usd`, `most_impacted_cohort`. **Long liquidations sit below the mark** (price falling), shorts above. `coverage` says what the map leaves out (positions beyond +-50% and those with no liquidation price), so a cluster is a floor.
- **`fills`**: `--direction` is the side of the position that was CLOSED (`long` = longs liquidated = price falling). `--wallet <addr>` asks whether one wallet was liquidated. Most rows are small: sort by size before quoting.
- Liquidation prices move with margin, price and funding; a cluster is a snapshot, not a level that will be hit.

## Live

```bash
vike-data stream hl --channel hl.liq_risk hl.liquidations --filter coin=BTC,ETH,HYPE --filter min_usd=1000000
```

- **`hl.liq_risk`**: a position of $100,000 or more announced once as its `liq_ratio` reaches 0.80 (0 = at entry, 1 = at the liquidation price), not again until it drops below 0.70 or closes. It comes with each position capture, about every 12 minutes. The first capture after the channel starts sends every position already in the zone with `prev: null` (about 300 on 2026-11-02); after that `prev: "out"` = just entered. `data` has `direction`, `liq_ratio`, `mark_price`, `entry_price`, `liquidation_price`, `size_usd`, `leverage`.
- **`hl.liquidations`**: each liquidation from the account's own fill, typically within a minute or two, `data.direction` the closed side, `size_usd`, `mark_price`; no size floor of its own.
- `min_usd` is the position size on `hl.liq_risk` and the liquidation size on `hl.liquidations`. A stored rule for one wallet: `vike-data alerts create --type hl_liq_proximity --wallet 0x... ...` (see `vike-alerts-setup`).
- **`hl.positions`**: the fills that change a big position (a wallet of $250,000 or more, or a position that size before or after the fill), each with `data.leverage` and `data.liquidation_px` of the wallet's position BEFORE the fill (the last capture, up to about 25 minutes old; `null` for an `open`; a `liquidation_px` of 0 means none) and `labels.tags`. `min_leverage=20` keeps the fills of positions at 20x or more (a `null` never matches); `tag` filters the wallet's trader tags; `min_usd` is the FILL's size (a floor of $1,000 you cannot lower). `hl.liq_risk` and `hl.liquidations` refuse `min_leverage` and `tag`, so it cannot share the command above:

```bash
# instead of the line above (one connection per key and path: a second `stream hl` replaces the first with 4410), or under its own key
vike-data stream hl --channel hl.positions --filter coin=BTC,ETH,HYPE --filter min_leverage=20
```

## Answer template

> Closest to a cascade among symbols with at least $50M open: `<symbol>` `<percent_risk>` (`$risk` of `$total`). `<ETH>`: `$V` of long notional liquidates between `<p1>` and `<p2>` (`<x%>` below the mark), `$W` of shorts above `<p3>`; the map covers `<c>` of open notional. Last hour: `<n>` long liquidations, largest `$M`.

## Anti-patterns

- Don't rank micro-cap or HIP-3 markets next to BTC without the notional floor.
- Don't write `percent_risk` as a percent without multiplying, or treat the heatmap as complete.
- Don't predict a cascade: say where open interest is exposed.

## Pairs well with

- `vike-hl-asset-briefing`, `vike-hl-cohort-positioning` (who holds the exposed side), `vike-candles`, `vike-stream`
