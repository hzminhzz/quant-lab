---
name: vike-wallet-ens
description: Reverse ENS lookup — turn a 0x address into a human-readable name like `vitalik.eth`. Cheap, single API call. Use whenever you have an address and want to display a friendlier identifier alongside it.
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-ens

## Command

```bash
vike-data wallet ens <address> [--json]
```

## Example

```bash
vike-data wallet ens 0xd8da6bf26964af9d7eed9e03e53415d37aa96045
# → { "address": "0xd8da...", "ens": "vitalik.eth", "source": "local" }

vike-data wallet ens 0x28c6c06298d514db089934071355e5743bf21d60
# → { "address": "0x28c6...", "ens": null, "source": "local" }      (most exchange wallets have no ENS)
```

## When to use

- Right after any tool returns a wallet address — show ENS as a secondary label
- When the user pastes an address and asks "what is its ENS name" (for "who is this", use `vike-wallet-who-is`; ENS is a display name, not an identity)
- When you're about to display a leaderboard with addresses — enrich each row with ENS if available

## Direction

**Reverse only: address → name.** This command has no name → address lookup; for `vitalik.eth` run `vike-data search vitalik.eth`, which searches the address the name points to. The only parameter is `address`.

## Anti-patterns

- Don't replace the address with the ENS name in tool output — show BOTH. The address is canonical; ENS is a display layer that can change ownership.
- Don't ENS-lookup hundreds of addresses in a leaderboard at once — credits add up. Usually ENS-lookup only the top 5-10 rows the user is likely to act on.
- A `null` response is normal, not an error. Most wallets don't have ENS. The exception: `ens: null` with `source: "rpc"` may only mean the resolvers were unreachable, so retry once before saying the wallet has no name.

## Pairs well with

- `vike-data labels <addr>` — combine ENS + labels for the fullest identity view
- `vike-data wallet summary <addr>` — ENS + activity profile
