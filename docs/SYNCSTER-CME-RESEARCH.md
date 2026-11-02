# Syncster CME data — quant-lab research admission (2026-11-02)

This page defines the **research-only** bridge from the user's verified local
Syncster datasets to Warden. The user authorized querying
`syncster-data/historical-market-data` and downloading individual missing
research series when a concrete hypothesis needs them. Downloading is **not**
an approval to read protected holdouts, to change already frozen contracts, or
to place trades.

## Live local sources (checked on VPS)

| Instrument | Canonical input | Input SHA-256 |
| --- | --- | --- |
| ES E-mini continuous 1m | `ml4t-data/data/futures/ES.parquet` | `e6320a6772fc5819b12605ffc63f88ac1861c26b8fa528d052e1b21d914438a8` |
| NQ E-mini continuous 1m | `ml4t-data/data/futures/NQ.parquet` | `35071bdad8b4abefabdf2543543d56b39c54dd56ee0ae7251ffed73107fb1681` |

Each is a **Syncster-provided CME-labeled continuous** series, rather than
independently attested exchange fill data. The original files retain the
`ts`, `symbol`, OHLCV schema. User-installed alternatives
`quant-lab/data/exploratory/cme_{es,nq}_1m.parquet` are accepted only when
their bytes have the exact same source digest. The CLI selects the existing
canonical `ml4t-data` file first and fails closed if that file's hash differs.

## Prepared Warden research lineages

| Contract | Prepared bars | Exploratory SHA-256 | Incomplete 15m bars |
| --- | ---: | --- | ---: |
| `CME_ES_15M_SYNCSTER_V1` | 213,127 | `9510c42b8172d300f8dbfd047a6561270c8b6e2e391f50fbc3d0ae28fd8cfb63` | 5,668 |
| `CME_NQ_15M_SYNCSTER_V1` | 212,434 | `7a976688ea097f181203626a50dc54e983b4fb27a887502f0ddbad84d7b4e915` | 8,779 |

The data files are **derived, local, ignored Parquet artifacts**; they must
be built before screening a worktree that lacks them. Their dataset manifests
in `contracts/datasets/` and contract YAML in `contracts/` pin the exact
derivatives and upstream files. The Warden `data_provenance` enum only
accepts `verified_market`/`synthetic`/`unknown`: use
`verified_market` for digest-pinned observed prices, and put
`source: syncster_market_data`, `market_type: cme_futures` and
`raw_source_sha256` in the dataset manifest. This does **not** assert
independent CME exchange origin, roll-adjustment fidelity or executable
broker costs.

Historical boundaries: train starts `2016-06-01T00:00:00Z`, validation
starts `2023-01-01T00:00:00Z`, validation ends **exclusive**
`2025-07-01T00:00:00Z`. Source rows after this cutoff are never included in
the prepared exploratory dataset. The recorded holdout path is absent and
untouched; no protected reading or new holdout authorization follows.

### Commands

From `quant-lab` (using its locked Python environment):

```bash
# Read-only source audit, including exact hash and derived panel digest
PYTHONPATH=src uv run --frozen python scripts/prepare_syncster_cme.py --symbol ES --frequency 15m
PYTHONPATH=src uv run --frozen python scripts/prepare_syncster_cme.py --symbol NQ --frequency 15m

# Materialize missing local exploratory data. Write-once; existing mismatches abort.
PYTHONPATH=src uv run --frozen python scripts/prepare_syncster_cme.py --symbol ES --frequency 15m --prepare
PYTHONPATH=src uv run --frozen python scripts/prepare_syncster_cme.py --symbol NQ --frequency 15m --prepare

# Full Warden SHA/manifest resolution and protected-file integrity check
uv run --frozen warden check-worktree
```

Run the CLI from the intended worktree. It reads only exact local source
Parquet and writes only the new contract, manifest and exploratory panel
when `--prepare` is supplied. It **never** reads or writes
`data/holdout/`, modifies existing contracts or silently replaces a changed
dataset. Source checks reject wrong hashes, null/duplicate rows, bad prices,
invalid OHLC, or zero/negative minute volume. Gap counts and incomplete
aggregates are **reported**, never patched by interpolation.

For new timeframes, start a new contract ID and pinned panel rather than
overwriting these frozen 15m files. The CLI also supports `--frequency 1h`
for a separately named, separately pinned future study, subject to the same
research governance.

### Selective remote discovery/downloads when genuinely needed

The user's upstream downloader currently exists in the live
`ml4t-data/scripts/download_syncster_dataset.py` checkout, with Hugging Face
authentication configured there. Probe only the symbol/prefix needed by a
registered economic mechanism:

```bash
cd /home/quant/dev/quant/ml4t-data
uv run python scripts/download_syncster_dataset.py --list WTICO_USD
# After inspecting remote metadata, source, date coverage and space:
uv run python scripts/download_syncster_dataset.py --file data/commodities/1m/WTICO_USD.parquet --dest data/commodities/1m/WTICO_USD.parquet
```

The read-only probe on 2026-11-02 found
`data/commodities/1m/WTICO_USD.parquet` (~7.44 MB). No commodity file was
downloaded as part of the CME ES/NQ research onboarding. Avoid broad
`--prefix` downloads until expected total bytes and license are checked.
Before creating a research contract for a new symbol, attest the actual
source SHA-256, UTC schema, history, and membership/coverage quality; avoid
using missing data as a reason to terminate unrelated research.

## Explicit limitations and next research stages

- **Continuous rolls:** The source provides a continuous market-price series
  but the roll dates, contract identifiers and adjustment method have not been
  independently verified. Price jumps around quarterly rolls can be
  artificial. Do not call this an executable front-contract, carry or
  basis-curve backtest without the underlying individual-contract and roll
  evidence.
- **Missing minutes:** The frozen panel reports 1,592,530 ES and 1,614,935
  NQ minutes absent from the full calendar grid during the study, including
  normal market closures. Among derived bars, 5,668 ES and 8,779 NQ have
  fewer than 15 observed minutes; they are **not silently completed**.
  Calendar-aware gap audit is required before candidate admission.
- **Execution:** The approved contract is `NEXT_BAR`, 1-bar lag, with
  **assumed** one-way 0.5 bp fees + 1.5 bp slippage including spread
  allowance. This is not an observed CME fee/slippage model. No bid data
  is present in the user's installed tick schema (trade price, volume,
  ask), so those files cannot be substituted for a true `QUOTE_SIDE`
  bid/ask contract. Tick-level fills and contract-size/stop-loss constraints
  need a separate execution-qualified stage.
- **PIT:** Aggregated `timestamp` marks the start of a completed interval.
  Features may be evaluated only **after that interval closes**; Warden's
  `NEXT_BAR` delay then applies. No feature may use later observations.
  The longer gap between adjacent observed bars may include weekend/session
  exposures; a bar-count delay is not constant wall-clock latency.
- **Warden selection debt:** Existing rejected CFD variants remain frozen and
  counted. The consumed holdouts of `HYP-2026-112/113/114` and
  `HYP-2026-066` must not be reopened. CME-research variants must be
  faithfully documented and registered under their own contract, with prior
  related search counted in scientific interpretation.

Priorities: (1) ES/NQ 15m directional/ORB independent hypotheses;
(2) CME transfer check for the ES down-day rebound that passed CFD Stage 1
but returned Stage 3 `REVISE`; (3) only when authentic bid/ask or execution
constraints are available, source-faithful NQ stop-order strategies.
Neither dataset creation nor Warden's `verified_market` label is alpha
qualification, protected holdout authorization, or live-trading permission.
