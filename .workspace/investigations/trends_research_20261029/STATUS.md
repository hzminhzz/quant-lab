# Status: blocked on data, not on code

Written after the goal loop returned to this investigation repeatedly. Read this before
re-running anything here.

## What is done

| deliverable | evidence |
|---|---|
| faithful port of the repo's own implementation | `strategies/candidates/s183_reference.py` imports `Live/2_Engine/trading_book.py` |
| composite assembly verified | `test_assembly.py`: exact, max diff 0.0 over 39,071 rows |
| IDM / FDM / sizing algebra verified | `test_parity.py`: 4.4e-16 / exact / exact |
| simulator agreement | `reconcile_sims.py`: warden exact vs hand P&L; engine 3.7e-4; gap 0.036 Sharpe after the short-selling fix |
| upstream pinned | `RUN-RU-001_provenance.json`: commit `0426bac`, `trading_book.py f7bfac29…` |
| pre-registration | `HYP-2026-126` (reconstruction), `HYP-2026-127` (port) |
| Stage 3, twice | `RUN-RU-001` net −0.455; `RUN-RU-002` net −0.300; both **REVISE**, audit 3/6 |
| trials remaining | **1 of 3** on HYP-2026-127 |

## Why it stops

A Stage-3 pass needs netSR ≥ **2.27** on this contract (k_eff 56.11, noise 1.4435, n = 806).
The best measured configuration is **−0.090** (engine, gate off, shorts enabled) and the best
warden configuration is **+0.310** — and that one requires removing the contract's mandated
one-bar delay, i.e. changing the contract, which is human-owned.

The distance is not closable by parameter choice. Every configuration measured lands between
−0.46 and +0.31.

## What is missing, and who owns it

| missing input | consequence | owner |
|---|---|---|
| futures term structure | carry sleeve (25% of the composite) cannot be built at all | data |
| contract-level / roll provenance | continuity of the 17 stores is uncertified (`continuous_series: false` in the catalog); effect on EWMAC unquantified | data |
| wider universe | 62 instruments would drop the hurdle from 2.27 toward ~1.0 | human — contracts are human-owned |
| drawdown gate inside warden | the reference's `gdd` needs an equity path the runner does not expose; implementing it inside the module was rejected as a hand-rolled P&L loop | human — protected core |
| integer contracts, roll costs | not modelled in weight space | — |

## What is NOT the problem

- Not the code: the assembly matches upstream exactly, and the simulators agree to 0.036.
- Not a sign error: every sleeve points the way the repo intends.
- Not the short-selling bug any more: that was found and fixed (`allow_short_selling`).
- Not the roll-adjustment question per se: no roll signature is detectable, and the metadata
  is silent — it is unresolved, not disproven.

## What would unblock

1. A futures panel with contract curves — restores carry, the largest single fidelity gain.
2. A wider-universe contract authored by the principal — lowers the hurdle to something the
   measured Sharpe could plausibly clear.
3. Explicit permission to change the contract's execution delay — not recommended; it is the
   parameter that decides this book's sign.

None of these is a parameter tweak, but several are code. Two of them are blocked on
authority rather than effort:

- **roll-gap sensitivity** — injecting a synthetic roll and re-scoring would bound the
  unresolved provenance question, by injecting a synthetic roll and re-scoring. It needs a
  `data_provenance: synthetic` contract, because injecting data into the pinned exploratory
  parquet is forbidden and a raw simulation outside warden is not evidence. Contract authoring
  is human-owned.
- **drawdown gate inside warden** — needs an equity path the runner does not expose; that is a
  protected-core change, also human-owned.

Re-running the same configuration will reproduce the same verdict, so the last trial should
not be spent until one of these lands.
