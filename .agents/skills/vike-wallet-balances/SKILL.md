---
name: vike-wallet-balances
description: Current token balances for a wallet on Ethereum, BSC, Base and HyperEVM, read from the chain at request time, with a USD value where the token is priced. Use when the user asks "what does this wallet hold" / "current portfolio of X" / "show top holdings".
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-balances

## Command

```bash
vike-data wallet balances <address>
  [--chains ethereum,bsc,base,hyperevm]   (default: all four)
  [--min-usd 1]             (hide holdings worth less; default 1)
  [--json]
```

Scanning all four chains is slow (tens of seconds for a busy wallet): pass `--chains` with the chain you need. Any other chain (arbitrum, polygon...) is not skipped silently: it is named in `chain_errors`. Each holding carries its own `chain`, so this is also the quickest way to see WHERE a wallet's value is before choosing `--chain` for the per-chain wallet commands.

## Example

```bash
vike-data wallet balances 0xd8da6bf26964af9d7eed9e03e53415d37aa96045 --chains ethereum
```

## Output

```json
{
  "address": "0x...",
  "total_usd": 10532.82,
  "chains_scanned": ["ethereum"],
  "holdings": [
    { "chain": "ethereum", "symbol": "ENS", "name": "Ethereum Name Service", "address": "0xc183...", "balance": 1144.04, "balance_raw": "1144036076063671431479", "usd_value": 6843.62, "usd_price": 5.982 },
    { "chain": "ethereum", "symbol": "WEI", "address": "0x0000...", "balance": 1.0, "balance_raw": "1", "usd_value": null, "usd_price": null },
    { "chain": "ethereum", "symbol": "ETH", "name": "ETH", "address": null, "balance": 2.5, "balance_raw": "2500000000000000000", "usd_value": 9500.0, "usd_price": 3800.0 }
  ]
}
```

Holdings are sorted by `usd_value` descending with unpriced rows last. `chain_errors` appears only when a requested chain was refused or failed.

## Reading

- **`usd_value: null` is a holding, not an error**: the token has no price feed. `total_usd` sums only priced holdings, so read it as a floor, never as net worth, and never infer "worthless" from a null.
- **The chain's native coin (ETH, BNB, HYPE) is a row** whenever its balance is not zero, whatever `--min-usd` says, with a USD value (counted in `total_usd`) where a price exists.
- **On `base` and `hyperevm` a row has `symbol`, `name`, `balance` and `usd_value` only for tokens we map and price** (about 170 on base, about 50 on hyperevm). `--min-usd 0` adds every other token the wallet holds, with its contract `address` and `balance_raw` and the rest `null`: often thousands of airdrop-spam rows, so use it deliberately.
- `hyperevm` is the HyperEVM chain only: HyperCore (Hyperliquid spot or perp accounts) is not included; Hyperliquid perp equity is `vike-data hl wallet <address>`.
- **Any `--min-usd` above 0 also drops unpriced holdings** (an unpriced holding cannot show it clears the floor). `--min-usd 0` lists everything, including airdrop spam.
- `total_usd` covers `chains_scanned` only. The wallet may hold value on chains the tool does not scan: say so when relevant.
- Stablecoin-heavy wallet → likely waiting for a buy / parked dry powder
- WETH/WBTC + LSTs (stETH, wstETH, rETH) → "saver" wallet, low-risk
- 50+ random tokens → airdrop farmer
- Empty `holdings` array → no priced balance above the floor, or a very new wallet we have not seen yet.

## Anti-patterns

- Don't report `total_usd` as the wallet's value without "of priced tokens".
- For wallets with 100+ tokens, the response is large — use `--min-usd 100` or higher to focus on meaningful holdings.
- This is a point-in-time read, not history. For how the wallet got here use `wallet pnl-history`.

## Pairs well with

- `vike-data wallet pnl-history <addr>` — how the wallet got to its current state
- `vike-data labels <addr>` — institutional context
- `vike-data wallet ens <addr>` — friendly identifier
