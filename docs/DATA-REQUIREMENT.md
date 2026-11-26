# Data requirements (`warden resolve-data`, `warden data compile`)

A *data requirement* is the declarative answer to "what data does this hypothesis need",
written before any strategy code runs. It never states what the data costs, when the
holdout starts, how much leverage is allowed or which search-debt group pays for the
attempt: those belong to the signed venue policy (`docs/VENUE-POLICY.md`), and a
requirement that tries to set one is refused.

```yaml
# requirement.yaml
asset_class: fx
symbols: [EURUSD]           # or `all_in_family: true` (never both)
frequency: 1h
min_start: '2021-01-01T00:00:00Z'
price_basis: ohlcv
execution_mode: NEXT_BAR
```

```sh
uv run warden resolve-data --requirement requirement.yaml --json
```

## What the resolver answers

| decision | meaning | exit |
|---|---|---|
| `reuse_signed` | an **active** signed contract already carries exactly these symbols at this cadence, price basis and execution mode, and its first bar precedes `min_start` | 0 |
| `compile_required` | no reusable contract, but the lake advertises every required symbol with enough history, so `warden data compile` can build one | 0 |
| `fail_closed` | the requirement cannot be served: a symbol the lake does not carry, history that starts too late, an unreadable lake, a policy domain with no selection group, a symbol outside the declared sub-family | 3 |

The report names the evidence the decision rests on — the lake root and per-symbol store,
the matched contract and its dataset digest, `missing_symbols`, `earliest_common_start`,
the resample block and every rejected candidate with its reason. Resolution is read-only:
no contract, dataset, Parquet or registry row is written, and no holdout byte is opened.

## Superset reuse is opt-in

A candidate contract whose basket is a **superset** of the requirement is a wider universe
than the requirement declared. By default it is *not* reuse:

```json
{
  "decision": "compile_required",
  "reason": "superset_requires_opt_in",
  "symbols_to_drop": ["GBPUSD", "XAUUSD"],
  "rejected_candidates": [
    {"contract_id": "FX_WIDE_1H_V1",
     "reason": "superset_requires_opt_in: contract carries 3 symbols for a 1-symbol requirement; ...",
     "symbols_to_drop": ["GBPUSD", "XAUUSD"]}
  ]
}
```

Pass `--allow-superset` to `resolve-data` (or to `data compile`) to accept the smallest
such contract instead. The drop set stays in the report either way, so the caller can
narrow its panel to the symbols it actually declared — and an `fx` thesis can never be
handed a precious metal (XAUUSD/XAGUSD) without saying so. An exact match always wins over
a superset, with or without the flag.

## Exit codes

`resolve-data` and `data compile` share the vocabulary in `docs/EXIT-CODES.md`:
`0` success (including "nothing to compile"), `2` the request was refused or its input was
untrusted, `3` the requirement is not satisfiable, `1` the compile ran and its evidence was
refused (fence, write-once).
