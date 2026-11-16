---
name: vike-wallet-trace
description: Counterparty graph traversal from a root wallet outward. At each hop, expand to the top N counterparties by USD volume; edges carry the direction of the flow. Use when the user asks for "the counterparty graph of X" / "trace 3 hops around this wallet" / "show the network around X" / "who is connected to this wallet within N hops".
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-trace

## Command

```bash
vike-data wallet trace <address>
  [--chain ethereum|bsc|base|hyperevm]
  [--depth N]    (1-5, default 2)
  [--width N]    (1-10, default 5)
  [--window 24h|7d|30d|90d|all]
  [--min-usd N]  (default 10000; 0 selects the default, pass 0.01 to include small transfers)
  [--json]
```

Chains: `ethereum`, `bsc`, `base`, `hyperevm`; node labels come from that chain's own label table. The command expands each hop by combined volume in both directions; the tool also takes `direction` `out` (where funds went) or `in` (where they came from), reached with `vike-data call wallet_trace --args '{"address":"0x...","direction":"out","depth":2}'`.

## Output

```json
{
  "root": "0xabc...",
  "depth": 2, "width": 5, "window": "30d", "min_usd": 10000,
  "nodes": [
    { "address": "0xabc...", "depth": 0, "total_volume_usd": 450000, "label": null, "wallet_type": null },
    { "address": "0xdef...", "depth": 1, "total_volume_usd": 250000, "label": "binance", "wallet_type": "cex" },
    ...
  ],
  "edges": [
    { "from": "0xabc...", "to": "0xdef...", "volume_usd": 200000, "out_usd": 150000, "in_usd": 50000, "tx_count": 4 },
    ...
  ],
  "stats": { "node_count": 23, "edge_count": 31, "max_depth_reached": 2 }
}
```

## Reading the graph

- **Direction lives in `out_usd` / `in_usd`, not in `from` / `to`.** `from` is the side that was expanded first, not the sender. For an edge, `out_usd` flowed `from → to`, `in_usd` flowed `to → from`, and `volume_usd` is their sum. To answer "where did X's money go", rank the root's edges by the outflow side, not by `volume_usd`.
- **Root (depth 0)** = the wallet you traced from. Its `total_volume_usd` is the sum of its own edges in this graph (not zero, and not its whole activity). The same holds for every node.
- **Depth 1** = direct counterparties. Most useful layer.
- **Depth 2-3** = "friend-of-friend" — useful for finding where funds end up.
- **Edge `volume_usd`** = the volume between those two specific addresses, NOT cumulative. One edge per wallet pair.
- **`wallet_type = "cex"`** at depth 1 = funds went to / came from an exchange. Often the END of the trail (anonymous from there).
- **Unlabeled wallet with high volume at depth 1-2** = potentially related wallet, investigate with `vike-data labels`.
- Only transfers of at least `--min-usd` count, so an empty or small graph can mean "nothing above the floor".

## Parameter tuning

| Parameter | Effect | Use when |
|---|---|---|
| `--depth 1` | Direct counterparties only | Same data as `wallet counterparties`, plus the direction split |
| `--depth 2` | One degree of separation | Default. Reveals immediate networks. |
| `--depth 3+` | Wider net | Investigation / OSINT. Returns much more data; graph gets noisy. |
| `--width 3` | Top 3 per hop | Cleaner, focused on biggest flows |
| `--width 10` | Top 10 per hop | Wider scan, more noise |
| `--min-usd 50000` | High-value flows only | Strip out dust/airdrops |

## Cost note

4 credits per call (the heaviest tool). Cost grows with depth × width, but the server silently stops after 300 wallet expansions, so a wide, deep trace (for example depth 3 × width 10) is cut rather than unbounded. If `max_depth_reached` is below `--depth`, either the branches ran out under `--min-usd` or the cap cut the graph: say which you cannot tell. Use `--depth 2 --width 5` as the default.

## Anti-patterns

- Don't trace through CEX deposits. Once funds enter Binance/Coinbase, the trail goes opaque (CEX internal accounting, not on-chain). Note CEX nodes and stop following.
- Don't depth-5 by default. Graph blows up; results become noise.
- Don't ignore `min_usd` — without it, dust transfers create false-positive edges.
- Don't read `from` as the sender.

## Visual graph (web UI)

When the user wants to *see* the network rather than read JSON, surface the URL:

```
https://vike.io/spot/wallet/<address>
```

Tell them to click the **Graph** tab. It renders the same trace data as a force-directed graph (nodes coloured by hop depth, sized by USD volume, animated directional particles on edges). Clicking any non-root node navigates to that wallet's graph. Use this in chat replies when the user says "show me", "visualize", or pastes a wallet address asking for an exploration view — JSON is for downstream pipelines, the URL is for humans.

## Pairs well with

- `vike-data labels <addr>` / `vike-data wallet batch <addr...>` — identify the nodes
- `vike-data wallet compare <addr_a> <addr_b>` — confirm relatedness once trace surfaces candidates
- `vike-data wallet transactions <addr>` — drill into specific transfers between two nodes
- `vike-follow-the-money` — the incident playbook (order of calls, stop rules, a live watch); `vike-wallets-related` — evidence of a shared owner
