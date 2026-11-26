# Contract archive

Contracts the reviewed lifecycle document closes — `DEPRECATED_UNEXECUTABLE` and
`LEGACY_SURVIVORSHIP_BIASED` — live under `contracts/archive/`, with their
`contracts/datasets/*.json` manifests in `contracts/archive/datasets/`.
`contracts/archive/archive_map.json` records, per contract, the original path, the archive
path, the pre-move `spec_hash`, the YAML and manifest SHA-256, and the commit the move
started from. The move was byte-identical (`git diff -M` reports `R100`); nothing was
rewritten, and no lifecycle entry was deleted.

`warden.contract_source` is the only authority for resolving a contract document:

| Function | Use | Archived id |
|---|---|---|
| `load_active_contract` / `resolve_active_contract` | new work: verify, screen, backtest, registration | refused, error names the archive path |
| `load_recorded_contract` / `resolve_contract` | historical replay of a recorded trial | resolved from the archive; the recorded digest is checked against whichever document is returned (a live copy of an archived id must match it too) |
| `all_contract_paths` | enumerating every contract in the tree | included, active first |
| `dataset_manifest_path` | the `contracts/datasets` manifest | falls back to the archive |

Everything a refusal protects still works: `status`, `spec-status`, `verify-causality` and
`audit-check` never consult the lifecycle document, so a trial recorded under an archived
contract reads back exactly as recorded; `warden data fence` loads archived contracts and
manifests, so archived exploratory files stay owned; `warden data build` keeps archived
dataset paths in its pinned inventory, so a build cannot overwrite evidence a recorded
trial points at.

Do not edit a file under `contracts/archive/`, and do not copy one back into
`contracts/` to "restore" it: promotion is a reviewed edit plus a manifest re-sign, not a
file move. A corrupt or digest-drifted archive document fails closed rather than reading
as an empty archive, and a live copy of an archived id is refused unless its bytes still
match the recorded digest — so copying an edited document back does not replay it.

The archive is covered by the worktree manifest (`warden check-worktree`) alongside the
live contracts; the archive globs are in both `warden_ops.integrity` and
`scripts/preflight_trust_boundary.py`.

Run the lane's evidence with:

```sh
uv run --frozen --offline pytest -q tests/test_spec109_g_contract_archive.py
```
