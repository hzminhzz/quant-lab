---
name: vike-wallet-who-is
description: Say who is behind one EVM address from vike's search, its label sources, ENS name, the chain it is active on, the exchanges it uses and its biggest counterparties. Tells an exchange, fund, market maker or contract from an ordinary user. Use when the user asks "who is 0x..." / "whose wallet is this" / "is this an exchange or a fund" / "which exchange does this wallet use" / "what kind of wallet is this".
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-who-is

Identity playbook for ONE address: what vike already calls it, labels, ENS, the chain that matters, which exchanges and wallets it moves money with. About 12 credits. For holdings and a flow snapshot use `vike-wallet-portfolio`.

## Chain

```bash
ADDR=0x28C6c06298d514Db089934071355E5743bf21d60
vike-data search $ADDR --json                    # 1  entity, HL trader, vault, builder, Polymarket wallet: whatever vike knows it as
vike-data labels $ADDR --json                    # 1  label sources + consensus (Ethereum label table)
vike-data wallet ens $ADDR --json                # 1  reverse ENS; "ens" can be null
vike-data wallet summary $ADDR --json            # 2  READ its `chain` field before anything else
vike-data wallet exchange-usage $ADDR --chain <summary.chain> --window 365d --json        # 3  which exchanges, with USD and shares
vike-data wallet counterparties $ADDR --chain <summary.chain> --window 90d --limit 20 --json   # 2
vike-data wallet batch <counterparties with an empty entity> --chain <summary.chain> --json    # 2, max 100 addresses
```

Chains: `ethereum`, `bsc`, `base`, `hyperevm` on every wallet command here. The user gave a name, not an address? `vike-data search vitalik.eth` searches the address an ENS name points to.

## Reading each step

- **`search`**: its `wallets`, `traders`, `vaults`, `builders` and `polymarket` sections say where vike already knows the address (a labelled entity, a Hyperliquid trader, a Polymarket proxy and its owner). Empty sections are not evidence of anything.
- **`labels`**: `found: false` = not in our label table; it is not proof the wallet is an ordinary user. Otherwise read `consensus` (`entity`, `wallet_type`, `confidence`, `risk_flag`) AND `sources[]`. When sources disagree the consensus type can be wrong: for `0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045` the entity is "vitalik buterin" but `wallet_type` read `dex` because one source says "sushiswap". Report the entity the sources agree on, not the type.
- **`summary.chain`** is the more recently active of ethereum and bsc, never a sum; base or hyperevm only for a wallet with no priced ethereum/bsc volume. If the figures look tiny, run `vike-data wallet balances $ADDR` (each holding carries its `chain`) and ask that chain directly: `vike-data call wallet_summary --args '{"address":"0x...","chain":"base"}'`.
- **`exchange-usage`**: `exchanges[]` with `sent_usd`, `recv_usd`, `sent_share`, first and last seen; `pattern: "sweeps_to_single_cex"` means 90%+ of its outgoing USD went to one exchange: an OBSERVED pattern, not proof it is a deposit address. Figures are a lower bound (an unlabelled exchange address is invisible; labels are sparse on hyperevm). A very active wallet is refused over a long window: narrow `--window`.
- **`counterparties`** rows already carry `entity` and `wallet_type` (empty string when unlabelled); only the empty ones need `wallet batch`, which adds `confidence`, `risk_flag` and 30-day flow from that chain's own tables.
- **The wallet itself is `cex`**: it is the exchange's own wallet, not a person. `wallet_type: dex` on a counterparty is a router or pool: many of them = an active trader, not a relationship.
- Optional, "who funded it": `vike-data wallet first-funder $ADDR --chain <chain>` gives the earliest inbound coin and token transfers with the senders' labels. It is not proof of the funder: contract calls and bridges are absent and history starts on a different date per chain (`coverage.history_from`).

## Answer template

> `<ENS or 0x...>` is `<entity>` (`<wallet_type>`, confidence `<c>`, `<n>` sources agree). Active on `<chain>`; 90d volume `$X`, last active `<date>`. Exchanges: `<entity>` `$sent` / `$received`. It mostly deals with `<top labelled counterparties>`; the largest unlabelled counterparty is `<0x...>` ($V). `<Not labelled: say so.>`

## Anti-patterns

- Don't name a real-world person from a label, and don't claim two wallets share an owner: that is `vike-wallets-related`, and it is evidence, not proof.
- Don't read `summary` as the wallet's whole activity: it is one chain.
- Don't call a wallet "clean" because `risk_flag` is empty or `found` is false.

## Pairs well with

- `vike-wallet-label-provenance` for the raw per-source labels, `vike-wallet-portfolio` for holdings
- `vike-follow-the-money` to see where its money went, `vike-wallets-related` to test a link between two addresses
- `vike-hl-trader-profile` when `search` lists it as a Hyperliquid trader, `vike-polymarket-trader` for a Polymarket wallet
