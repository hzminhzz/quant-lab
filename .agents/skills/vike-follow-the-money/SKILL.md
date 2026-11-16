---
name: vike-follow-the-money
description: Trace where the money of one wallet went - the outbound graph hop by hop, the actual transfers, every node labelled, stopping at an exchange - then watch the trail live. Use when the user asks "where did the funds from 0x... go" / "follow the money" / "trace this exploit or scam wallet" / "did it reach an exchange" / "investigate this address".
allowed-tools: Bash(vike-data:*)
---

# vike-follow-the-money

Incident-style trace from one root wallet. About 12 credits. Per-tool details live in `vike-wallet-trace`; this is the order to run them in and the stop rules.

## Chain

```bash
ADDR=0xeae7380dd4cef6fbd1144f49e4d1e6964258a4f4
vike-data labels $ADDR --json          # 1  is the root itself an exchange, MM or contract?
# 4 credits: direction "out" expands each hop by where the money WENT (the `wallet trace` command has no --direction)
vike-data call wallet_trace --args '{"address":"0xeae7380dd4cef6fbd1144f49e4d1e6964258a4f4","chain":"ethereum","direction":"out","depth":2,"width":5,"window":"30d","min_usd":100000}'
vike-data wallet txs $ADDR --chain ethereum --direction out --min-usd 100000 --days 30 --limit 50 --json   # 2  the real transfers
vike-data wallet batch <node addresses with an empty label> --chain ethereum --json                         # 2  label the rest
vike-data wallet exchange-usage <endpoint> --chain ethereum --window 30d --json                             # 3  did it reach an exchange
```

Chains: `ethereum`, `bsc`, `base`, `hyperevm` on every command above; `labels` reads the Ethereum label table, so on other chains label with `wallet batch --chain`. "Where did it come from" is the same chain with `"direction":"in"`, or `vike-data wallet first-funder $ADDR --chain <c>` for the earliest inbound transfers.

## Reading the trace

- Each edge carries `out_usd` (flowed `from -> to`) and `in_usd` (flowed `to -> from`); `from` is the side expanded first, not the sender. Rank the root's edges by `out_usd` for "where did it go".
- Nodes carry `label` and `wallet_type`; `wallet batch` is for the empty ones (adds `risk_flag`, 30-day flow).
- **Stop** at a node with `wallet_type: cex` (the trail goes off-chain; report exchange and time), at a `dex` router (a swap, not a destination), after depth 3, or after about 10 wallets. Unlabelled, high-volume nodes at depth 1-2 are the leads.
- Only transfers priced at or above `min_usd` count; a `value_usd` of 0 is UNPRICED, not worthless, and an unpriced token never shows in the graph. Say so if the loss was in an obscure token.
- `max_depth_reached` below `depth` = nothing above the floor beyond that hop, or the server's expansion cap cut the graph; you cannot tell which.
- `wallet txs` gives what the graph cannot: `tx_hash`, `time`, `token_address`, `value_usd`, `block` for each transfer. Quote hashes, not just totals.
- `exchange-usage` on an endpoint lists the exchanges it sent to with USD and shares; `pattern: "sweeps_to_single_cex"` is an observed pattern, not proof of a deposit address.

## Watch the trail live

```bash
# every transfer touching the root or its first hops (either side), pools and routers left out
vike-data stream onchain --channel chain.transfers --filter chain=eth --filter wallet=0xeae7380dd4cef6fbd1144f49e4d1e6964258a4f4,<hop1> --filter contract=false
```

- The stream spells the chain `eth`, not `ethereum`. Events arrive in bursts about 3 minutes after the block.
- **Never put `min_usd` on `chain.transfers`**: transfers are priced after they are streamed, so it drops almost every event. For one token use `--filter token=0x... --filter min_amount=<whole tokens>`.
- `contract=false` drops a transfer when either side is a known contract; no mark does not mean "not a contract".
- For a stored rule instead of a running process: `vike-data alerts create --label "Exploiter out" --chain eth --address $ADDR --address-role from --min-usd 10000 --channel email` (see `vike-alerts-setup`). Keep a `--min-usd`: an address alert notifies on EVERY matching transfer (no cooldown, no daily cap), and without a floor that includes dust and unpriced spam tokens. With it, only transfers that already carry a USD value match, so an unpriced token never fires.

## Answer template

> Root `<0x...>` (`<label or unlabelled>`). In the last `<window>` it sent `$X` to `N` counterparties; the largest: `<0x...>` `$A` (`<label>`), `<0x...>` `$B`. Trail: `<root> -> <hop 1> -> <exchange>` (last transfer `<time>`, tx `<hash>`). Unlabelled endpoints: `<list>`. Evidence from on-chain transfers above `$floor`; nothing below it or unpriced is included.

## Anti-patterns

- Don't continue past a CEX or call a deposit address a person.
- Don't trace at `min_usd` 0: dust edges bury the path.
- Don't present the trace as proof of intent or ownership.

## Pairs well with

- `vike-wallet-who-is` on each endpoint, `vike-wallets-related` to test whether two endpoints are one entity
- `vike-wallet-trace` (parameters), `vike-wallet-transactions` (filters), `vike-stream` (the live feed)
