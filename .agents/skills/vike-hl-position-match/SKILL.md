---
name: vike-hl-position-match
description: Reverse-lookup a Hyperliquid open position from share-card details (coin + side + leverage + entry price). Use when the user pastes or describes a HL share card and asks "whose card is this?", "find this trader", or "who has a 25x ETH long open at $3,200?". Returns matching wallet(s) ranked by entry-price proximity.
allowed-tools: Bash(vike-data:*)
---

# vike-hl-position-match

Find the wallet(s) holding an open Hyperliquid perp position that match a share card.

## Command

```bash
vike-data perp find-position
  --coin <SYM>         # the asset symbol, e.g. BTC, ETH, SOL (sent to the tool as `symbol`)
  --side long|short
  --leverage <X>       # e.g. 25
  --entry <PRICE>      # entry price from the card
  [--mark <PRICE>]     # current mark price — sharpens tie-break when multiple wallets share entry
  [--limit N]          # max matches, default 50, max 500
  [--json]
```

## How matching works

The tool walks three widening entry-price tolerance bands until it finds at least one match:

| Band | Tolerance | When it's used |
|---|---|---|
| tight | ±0.05% | First pass — most share cards round-trip exactly |
| loose | ±0.5% | Fallback if no tight matches |
| fallback | ±1.0% | Last resort — anything wider is noise |

Leverage must be within **±0.5** of the queried value (so `--leverage 5` matches positions at 4.5×–5.5×). Tie-breaking inside a band is by closest live `mark_price` to the queried `--mark` (or to `--entry` if `--mark` is not given).

The response includes `tolerance_used` so the caller knows which band matched (`tight` / `loose` / `fallback` / `no_match`).

## Common queries

| User says | Flags |
|---|---|
| "Find this share card: 25x ETH long, entry $3,210" | `--coin ETH --side long --leverage 25 --entry 3210` |
| "Whose 10x BTC short is open at $58,400? Current mark $57,900" | `--coin BTC --side short --leverage 10 --entry 58400 --mark 57900` |
| "Top 5 matches for 50x SOL long around $145" | `--coin SOL --side long --leverage 50 --entry 145 --limit 5` |

## Output schema

```jsonc
{
  "match_count": 3,
  "tolerance_used": "tight",          // tight | loose | fallback | no_match
  "query": { "symbol": "ETH", "side": "long", "leverage": 25, "entry_price": 3210, "mark_price_hint": 3215 },
  "matches": [
    {
      "address": "0x...",
      "symbol": "ETH", "side": "long",
      "snapshot_ts": 1716700000,       // unix seconds — how fresh the snapshot is
      "size_token": 12.5, "size_usd": 40250,
      "entry_price": 3209.8, "mark_price": 3215.3,
      "leverage": 25.0,
      "liquidation_price": 3082.1,
      "unrealized_pnl": 68.75,
      "entry_dist_pct": 0.0062         // |query.entry - match.entry| / query.entry × 100
    }
  ]
}
```

## Reading the result

- **Multiple matches at the exact same entry** (e.g. five wallets long ETH at 3210.00) — popular round-number entries collide. Treat as ambiguous; surface all and let the user disambiguate by size or address. Don't claim "this is the wallet" when `match_count > 1` in the tight band.
- **`tolerance_used: fallback`** — the closest match is >0.5% off the queried entry. The card might be from a previous fill that's since been added to (size-weighted entry moves on add) or from a different exchange. Treat as low-confidence.
- **`tolerance_used: no_match`** — the position is closed, the entry was from a margin-changing event, or the wallet was outside our HL coverage. Don't suggest a wallet anyway.
- **`snapshot_ts`** is the snapshot time of that position row. Only positions seen within 15 minutes of the newest snapshot batch are matched (older rows are ghosts of closed positions), so a match is a position that was open in the last quarter hour; it does not mean the position is still open now.
- **Response keys:** each match and the `query` block name the asset `symbol`. Servers before 2026-10-19 called it `coin` (or `asset`) and renamed it on 2026-10-19, so read `symbol` and fall back to `coin` if it is missing.

## Anti-patterns

- Don't fabricate matches when the tool returns `match_count: 0`. Say "no open position matches that share card" — closed positions, post-card adds, or non-HL cards all produce empty results.
- Don't strip the address when displaying — share-card investigations always want the full 0x...40hex (users copy-paste it).
- Don't infer trader skill from a single share-card match. Profile the wallet with `vike-hl-trader-profile` (`vike-data hl wallet <address>`).

## Pairs well with

- `vike-hl-trader-profile` — track record, equity, open positions, funding and deposits of the matched wallet.
- `vike-hl-top-traders` — where the wallet ranks. (`vike-data wallet summary` has no Hyperliquid data: it shows on-chain transfer activity on one chain only.)
- `vike-wallet-label-provenance` — find out if the wallet is a known CEX/fund/MM rather than a retail trader.
