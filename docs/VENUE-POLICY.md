# Venue policy (#112)

The single runtime input is `contracts/policy/venue_policy.yaml`; the exact path is
protected by the Warden worktree manifest and is not in the active-contract glob.
This change does not install a production policy. The example below is **unsigned,
synthetic, and not approved for research**. There are no built-in defaults.

```yaml
version: 1
train_start: '2021-01-01T00:00:00Z'
val_start: '2022-01-01T00:00:00Z'
val_end: '2023-01-01T00:00:00Z'
holdout_start: '2023-01-01T00:00:00Z'
holdout_end: '2024-01-01T00:00:00Z'
asset_classes:
  fx:
    taker_fee_bps: 0.6
    slippage_bps: 0.7
    funding_bps_per_8h: 0.0
    allow_leverage: false
    max_gross_leverage: 0.9
    selection_groups:
      intraday: EXAMPLE_FX_INTRADAY
      daily: EXAMPLE_FX_DAILY
```

`load_venue_policy(repo_root)` in `warden_ops.venue_policy` verifies the manifest's
HMAC and the exact policy bytes before parsing. Missing policy, unsigned or
unverifiable manifest, absent manifest entry, a changed digest, and invalid
schema each raise `VenuePolicyError`. Interactive approval metadata alone is
not a signature. Loading does not replace `warden check-worktree`, which checks
the full protected tree and import restrictions.

For new-contract admission and compilation, call `policy.rules_for(asset_class,
frequency)` to obtain frozen cost floors, leverage settings and
`selection_group_id`, or call `policy.selection_group_id(asset_class, frequency)`
for debt grouping alone. `1m`, `5m`, `15m`, `30m`, `1h` and `4h` share the `intraday`
bucket; `1d` uses `daily`. Unsupported frequencies and missing mappings are refused.
The `VenuePolicy` instance exposes the five boundaries as UTC datetimes and
`sha256` as the exact policy document digest. Train, validation, and holdout fences
must satisfy `train_start < val_start < val_end <= holdout_start < holdout_end`.
Costs must be finite; taker fee and slippage must be positive, funding may be zero.
The gross cap must be finite and positive and cannot exceed 1 without explicit
leverage authorization. Unknown and duplicate keys are refused.

## Operator-only adoption

1. Review the actual per-asset-class cost floors, leverage bounds and global UTC
   fences against approved research standards. Choose the stable debt group for
   each admitted asset-class/cadence pair, and reconcile existing group IDs and
   inherited debt before activation. Do not change historical contract digests,
   trial rows, or artifacts to fit a new policy. Do not use the sample values.
2. Install the reviewed document at the exact runtime path. An operator must use
   the existing HMAC manifest signing procedure from the trusted checkout. The
   manifest must cover both the adopted YAML and the changed protected source.
   Keep the checkout, approval artifacts, and their parent directories under the
   existing operator-controlled filesystem protections so agents cannot replace them.
3. Verify the whole worktree and load the policy in an operator-controlled runtime.
   The existing HMAC uses `WARDEN_HUMAN_SIGNING_KEY` for both verification and
   signing. **Do not expose it to an agent or any process running agent-authored
   code.** Keyless agent-side use needs a separately approved verification path
   (such as an operator-isolated verifier or a public-key trust cutover); without
   that path, keyless callers fail closed. Do not treat fixture tests or an unsigned
   checksum as production approval.
