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

## Continued verification checkpoint

Code pair: producer 6192fd34851abcc07ca0d84cce97e53fb99282cb, consumer 9f6ab7a0c4d744c598795c2759877cde1915f343. Both are pushed to the existing draft PR branches. The original primary worktrees, services and canonical state were not changed.

- Real index: ES, 1,335 October 8 minute bars accepted using the original writer receipt. October 12 lacks that receipt and is refused. Legacy second-cadence ES aggregate lacks required categorical per-unit receipts and remains unqualified.
- Real FX: EURUSD, GBPUSD and USDJPY each returned 1,440 actual bars and resolved with spot-FX semantics. Their original writer acceptance/content bindings were independently checked on disposable copies of 5,373, 5,372 and 5,373 partitions. GBPUSD excludes the recorded December 24 2023 unresolved day. Complete audit/publication/query/resolve elapsed 34.70, 34.70 and 33.69 seconds, respectively.
- Five specialized catalog writers were redirected through the common publication owner. Nonmigrated families remain explicitly acquisition-only; no new compilation from mutable legacy sources.
- Latest exact-pair BTC lifecycle re-run: 80,461 / 81,901 / 83,341 rows; generation bytes 3,042,800 / 3,096,427 / 3,151,067. The full adversarial harness took 37.90 s, reported max RSS 285,568 KiB, no swaps, and reproduced the generation IDs above. Individual mixed-operation phases took 8.01, 8.71, 8.25 s; these include fault/consumer work and are not pure publication benchmarks.
- Producer collection errors resolved by reconciling already-retired LSE/PMXT source and tests, tracking the byte-identical active supervisor, installing locked Node dependencies and fixing CCXT request symbol identity. Complete applicable tests split into 575 passed/3 skipped general suite and 37 passed live read-only query/freshness suite (explicit ML4T_DATA_STORE). No live lake was linked into an isolated test root. No failures in either run.
- Consumer focused: 167 passed including the golden lifecycle. Fast exact clean-checkout verifier: 1,492 collected, protected inventory/AST, fatal lint, installed imports/bytes and locked package preflight passed. Unsigned maintenance checksum inventory refreshed; no scientific signature changes.

### Remaining release gates

Do not activate or merge this pair. The migrated-family set still excludes aggregate Binance 1h, other active crypto/Hive and daily routes; XAUUSD/XAGUSD lack the required live acceptance receipts. Their actual data is discoverable, not newly qualified. Complete per-family adoption, exact original receipt retention, bounded changed-data reads/hashes/copies and rollback/retention under the real service budgets remain unproven. An intact pinned generation remains readable while a later acquisition fails, but this is not a whole-lake compatible cutover proof. Existing native query plans validate bytes before constructing the lazy scan; Warden additionally validates the actual bytes passed to the compiler. Review must address remaining mutable-read and publication resource risks. Exact immutable Warden gate and actual scheduled compatible operation are distinct from the green focused and producer tests. No scientific qualification, live orders, sealed-data access or production deployment occurred. All milestone issues remain open until their complete acceptance criteria are verified.

## Incremental and compact-reference continuation

- Producer de8275330b7c1052542bc380989b8bdcffb30301 implemented exact changed-leaf observation, audit reuse, native pinned-byte scans and preserved provider precedence. Trusted filesystem identity is a reuse hint, never a substitute for verified consumed bytes. Native committed lazy scans have an explicit 256 MiB compressed-input bound; request smaller date ranges for larger reads.
- Controlled BTC experiment (previous producer a940e9b vs optimized de82753, identical real inputs): Parquet hashed bytes initial 15,214,000 -> 6,085,600; each of three unchanged republishes 9,128,400 -> 0; tails 18,578,562 -> 2,836,916 and 18,906,402 -> 2,946,196. Same final 83,341 rows, data identity 048b894e91f6e009c1ea93a8aee491fcd859e584e4207ba77bfe95e9041c4102. Snapshot allocated bytes 5,943,296 both. Peak RSS 281,936 vs 284,684 KiB. Publication elapsed initial .0218 -> .00673 s; unchanged .0178/.0123/.0135 -> .00342/.00294/.00284 s; tails .0228/.0151 -> .00511/.00427 s. Audit and native query timings remain separate. Raw benchmark logs /tmp/dg-publication-bench.RdtbTb/{baseline,candidate}.log. Changed native quality checks still run; whole-store metadata work is not claimed eliminated.
- Catalog now indexes digest-addressed portable manifests and local observation sidecars instead of embedding every inventory. The same generation identity is used; readers fail closed on missing/damaged manifest bindings. Earlier inline committed records remain readable through the same validation seam. BTC, ES and funding real witnesses passed after this change (latest BTC funding 176 events).
- Central family policy routes additional typed crypto and daily sources through the common publication owner. Real Hyperliquid BTC minute 609,642 rows/424 leaves; EURUSD daily 14,809/645; ES daily 12,465/563; XAUUSD daily 8,337/330. Native query counts and Warden compile-required resolution agree, with original writer mappings and bid/proxy semantics retained. Repeat publication copied and hashed zero Parquet bytes for all four. This is engineering availability, not admission or scientific quality.
- Quarantine is explicit by producer family/owner: aggregate Binance hourly and mark archives need exact dedicated member/source/receipt binding; second indices need categorical receipts; untyped and event/archival routes are discovery-only. Metal minute stores lacking live writer acceptance remain unqualified; daily metal data is a distinct writer route, not a workaround for minute evidence.
- Latest producer suite: 577 passed, 3 skipped (isolated general); 37 passed (explicit read-only live lake). The earlier unsplit test invocation had 33 absent-lake failures; the correct split is green. Warden focused 167 passed after compact-reference changes; lint and maintenance checksum update passed. Previous full immutable release PASSED for 9f6ab7a only, receipt /tmp/dg-warden-final.M9vA32/appliance/verification/de595b0976d509a38f7cf585cadaaed59bb6ddb61dbf85907d185d6095ca6ad0/attempt-kx7yt_6t/receipt.json; it cannot certify newer source.
- Primary producer checkout 26bdea6304371deb3b03208b16dd3780ae988533 has extensive overlapping dirty/untracked writer changes including an independently developed metals route; primary QL b1322ff996b0e31dfd6ac8576f59f1dd063e557b is diverged and dirty. Neither was overwritten. Existing scheduled producer units remain on their existing versions. Full eligible-lake adoption and measured service-budget/cutover/rollback evidence remain required; no merge/activation/issue closure or scientific state mutation.
