# Committed dataset publication — DG-01 / funding checkpoint

Status: candidate only; no canonical mutation, activation, or milestone closure.

## Real evidence

Disposable independent copies from the authorized ML4T producer source:
`crypto_futures_minute_BTCUSDT_PERP/year=2026/month={10,11}/data.parquet`.

Baseline on d17e689 / 2ccf10e: 83,341 rows, 3,151,067 bytes. Resolution incorrectly succeeded after a partition rename and removal of all partitions. Generic funding accepted without dedicated audit while dedicated publication refused; a fresh funding audit accepted 175 events with stale 93-row metadata. A real ES second partition (25,842 rows) was accepted without writer receipts. An unrelated funding publication invalidated BTC through the global readiness binding. Baseline initial audit/catalog/readiness elapsed 0.84 s (not a controlled performance comparison).

Candidate real BTC lifecycle:
- 80,461 -> 81,901 -> 83,341 rows; old October bytes SHA256 cf744d1903ee849f37f1e269bb6b88eacd435a566fdf4989aae70eb57b2c7b8b unchanged.
- Actual subprocess SIGKILL at generation before/after rename and catalog before/after replace. Reopen observes complete prior/new generation; retry converges.
- Concurrent same-dataset writer tails and independent publishers, stale audit/metadata, old pinned readers, extra/unlisted leaves, missing/renamed committed leaves, restored-mtime byte corruption, unrelated readiness/publication, legacy compile refusal.
- Native compiler accepted an isolated fixture-only authority covering the first complete October source day: 1,440 real minute bars -> 24 hourly bars. The second day was excluded by the policy fence. The resulting contract remains independently verifiable after removal of the producer lake. Full October independently refused 145 MAD outliers under unchanged threshold 8.0. No production signing authority, sealed holdout or scientific admission used.
- BTC generation identities before funding consolidation: b30d4a824f22fcfab3dce3f621f633dd342d7845bb5783e3a45a8bfe564b1301, a5ca3409d47309163f8256000ed9d3f90b3dda09157ded46c3c391ad3715482b, 02ffab8db440264d547add542283003124dadb8d0d22b987b858d021c207e537.
- Funding witness: 93 -> 175 actual settlement events; both publishers now share the dedicated-validator qualification policy and atomic catalog commit. Generic audit cannot substitute for dedicated evidence. Fresh audit with stale metadata retains the old 93-event commitment. Metadata repair then publishes 175 events; generic and dedicated retries produce identical entries. Generation IDs 49548dafcd0ff29816e3e80a357a5eecfc0cac7407014f66a3063cef90d68098 and 98088f554cbeee8f803f2dc1b98add6af467157eb84c4d65dc1a5277752b4dcc.
- Focused producer: 72 passed, 1 skipped. Focused consumer before explicit legacy test: 166 passed including lifecycle golden witness; compile suite after explicit legacy test: 42 passed.

Run with the existing producer interpreter from the producer worktree:
```sh
PYTHONPATH=.:scripts POLARS_MAX_THREADS=2 OPENBLAS_NUM_THREADS=1 \
python scripts/verify_committed_dataset_lifecycle.py \
  --source /home/quant/dev/quant/ml4t-data/.data/store \
  --warden /home/quant/.devspace/worktrees/quant-lab-5ef73d27 \
  --warden-python /home/quant/.devspace/worktrees/quant-lab-5ef73d27/.venv/bin/python
```
Add --funding for the independent funding witness. The harness never writes to the source root.

## Decisions and limitations

The existing catalog is the single visibility point. Immutable generation directories stage exact bytes/evidence; unchanged independently verified immutable leaves are reused. Reader-visible local stat bindings are separate from portable data identity. Warden verifies the actual bytes it supplies to the native loader only after policy/fence checks; signed and compiled contracts remain self-contained. No research lifecycle owner changed. Metadata repair no longer owns catalog mutation; funding CLI is an adapter.

This checkpoint is not full DG acceptance. Initial adoption still copies raw bytes and repeated inventory scans are not bounded to changed data. No full-lake adoption is authorized until measured and reconciled. Remaining provider-specific interval eligibility, direct catalog writers, native default precedence and aliases, full producer baseline failures, exact release verification, scheduled activation and scientific readiness replay are open. Process-crash recovery is evidenced, not power-loss durability. Existing primary checkouts and their concurrent dirty work are untouched.
