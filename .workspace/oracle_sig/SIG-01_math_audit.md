# SIG-01 — path-signature source and math audit

Status: **complete for what the source specifies**, retested. One source-level contradiction is
recorded and cannot be resolved from the clipping.

Inputs: `/home/quant/dev/obsidian-new/raw/clippings/delphicalpha/Path Signatures - Does the Shape
of Price Paths Predict Returns.md` (243 lines) and the plan's ticket SIG-01. No market returns read,
no hypothesis registered, no trial consumed, no contract edited.

Artifacts:

- `strategies/candidates/path_signature_common.py` — both conventions, seven signals, path standardization.
- `tests/experiments/test_path_signature_reference.py` — deterministic tests, all passing; `ruff check` clean.

## What the source actually specifies

| Source line | Statement |
|---|---|
| 52 | path = cumulative log return × cumulative normalised range `(high-low)/close`, per bar in window W; "standardised to unit variance per dimension" |
| 68–74 | level 1 = total displacement per dimension; level 2 = **sum over ordered pairs `t1 < t2`** of increments |
| 78 | `S(1,1) = Σ_{i<j} return_i·return_j` — described as return acceleration |
| 80 | `S(1,2) = Σ_{i<j} return_i·range_j` — return leading volatility |
| 82 | `S(2,1) = Σ_{i<j} range_i·return_j` — volatility leading return |
| 84 | `S(2,2) = Σ_{i<j} range_i·range_j` — volatility acceleration |
| 86 | Lévy area `A = S(1,2) - S(2,1)` |
| 88, 184 | "the level 2 signature is computed via the **iisignature** library, producing 6 components per window" |
| 94, 186 | each signal z-scored over a **500-bar rolling window**, then discretised `sign(z)` |
| 186 | positions entered **at bar close**, PnL realised on the subsequent bar |
| 188 | lookbacks 2/4/8/24h converted to bars; **at 5m, signatures computed every third bar and forward-filled**; 30m and 1h every bar |
| 191 | all results in-sample, no train/test split, no walk-forward |
| 222–240 | Appendix C: the seven signal definitions (below) |

### The seven signals, with the signs the source states (lines 92, 222–240)

| Signal | Definition | Role |
|---|---|---|
| Sig-Reversion | `-S(1)` = minus total log return | control |
| Sig-VolDrift | `S(2)` = total normalised range | control |
| Sig-Accel | `S(1,1)` | shape |
| Sig-LevyArea | `S(1,2) - S(2,1)` | shape |
| Sig-FadeRetVol | `-S(1,2)` | shape |
| Sig-FadeVolRet | `-S(2,1)` | shape, "strongest and most consistent" per the source |
| Sig-VolAccel | `S(2,2)` | shape |

Implemented as `source_signals()`, which returns exactly these seven names with these signs and
defaults to the strict convention because that is the only one the source fully specifies.

## The unresolved contradiction

The written definition (70–84) is the **strict-pairs** statistic: a plain double sum over bars, no
calculus, no 1/2 terms. The stated implementation (88, 184) is **iisignature**, which returns the
standard truncated signature of the interpolated path, where each intra-segment term carries 1/2.
These are different objects:

- cross terms differ by `0.5·Σ_k ΔX_k ΔY_k`
- diagonals differ by `0.5·Σ_k ΔX_k²`

Both are implemented (`strict_pair_signature`, `depth2_signature`), and a test pins the gap so they
can never be silently interchanged. Which one produced the published numbers cannot be determined
from the clipping; SIG-03 must declare the choice with its reason and report it as a deviation if it
differs from the written formulas. The economic reading changes with the choice: the source's own
sentences about `A = S(1,2)-S(2,1)` on line 86 only make geometric sense under the standard
convention, which is evidence — not proof — that iisignature produced the tables.

## Corrections to the earlier version of this audit

1. **`S(1,1)` is not "acceleration" in either convention.** Under standard, `S(1,1) = ΔX²/2` —
   endpoint only. Under strict-pairs, `S(1,1) = (ΔX² - ΣΔX²)/2` — endpoint minus increment
   dispersion. Both are invariant to permuting the increments, so neither can tell an accelerating
   path from a decelerating one with the same increments; only dispersion moves them. Verified by
   `test_permuting_increments_changes_cross_terms_not_diagonals` and
   `test_strict_diagonal_equals_half_of_dispersion_identity`. The earlier audit overstated the case
   by testing only the standard convention and then generalising to "the source's claim".
2. **Standardisation was implemented wrongly and is now fixed.** The source scales the *cumulative
   path* (52). The first version centred the *increments*, which forces `ΣΔX ≈ 0`, zeroing the
   displacement that the whole level-2 endpoint term is built from — a signal-destroying bug, not a
   cosmetic one. `standardize_path()` now z-scores the path per dimension. A regression test
   (`test_increment_standardization_would_erase_the_endpoint_term`) pins the failure mode.
3. **Retracted: centring does not move the signature.** I claimed translating the path shifts
   `S(1,2)` by `-m_x·ΔY`. That is only true of an *unanchored* `∫X dY`, which was my first
   implementation. Both shipped conventions anchor at the first point (standard) or use increments
   alone (strict), so translation cancels exactly: measured `max|difference| = 3.6e-15` over shifts
   of ±5, ±0.01 and ±1e3, on both conventions and all seven signals. The dead `center` flag is
   removed from `standardize_path`, and `test_translating_the_path_leaves_the_signature_unchanged`
   pins it. Only the *scale* matters, because level 2 is homogeneous of degree 2.

## Verified identities (standard convention unless stated)

| Identity | Test |
|---|---|
| `S(1,2)+S(2,1) = ΔX·ΔY`, only when anchored at the first point | `test_shuffle_identity_holds_for_random_paths` |
| `-S(2,1) = ½(A − ΔXΔY)` | `test_minus_s21_equals_half_area_minus_endpoint_product` |
| straight path ⇒ `A = 0`, `S(1,2)=S(2,1)=½ΔXΔY` | `test_straight_path_has_zero_levy_area` |
| closed form equals independent refined-grid trapezoid integral | `test_matches_independent_riemann_integration` |
| inserting collinear points changes nothing | `test_refinement_invariance_inserting_collinear_points` |
| flat range dimension ⇒ no NaN, zero cross terms | `test_flat_range_dimension_produces_no_nan` |
| translating the whole path changes nothing | `test_translating_the_path_leaves_the_signature_unchanged` |

Consequence for the plan's primary candidate `Sig-FadeVolRet = -S(2,1)`: it is exactly half the Lévy
area minus half the endpoint product, so the `-S(1)` and endpoint-product controls are mandatory for
attribution, not optional diagnostics.

## Fidelity items SIG-03/SIG-04 must decide, not inherit silently

1. Convention (strict-pairs vs standard) — see contradiction above.
2. Scaling convention: per-window per-dimension standard deviation (source line 52). Translation is
   irrelevant, so there is nothing else to decide.
3. 5m cadence: source recomputes every third bar and forward-fills (188). Reproducing this changes
   the effective signal clock to 15m; declining it changes turnover. Either way it must be written
   down, not discovered later.
4. 500-bar z-score is a bar count, so 500 bars is ~1.7 trading days at 5m indices and ~10 weeks at
   1h indices. The source's wording (94) does not say it meant a clock window.
5. Entry at signal-bar close (186) versus Warden's mandatory one-bar delay. The source is one bar
   optimistic relative to anything this harness will produce.
6. Level 1 is a control (68), and `Sig-Reversion`/`Sig-VolDrift` are those controls — they are not
   shape features and must not be presented as such.

## Gate state

`uv run warden check-worktree` returns 2: one manifest entry unsatisfied — `AGENTS.md` (CHANGED,
pre-existing, unrelated). `verify`, `screen` and `backtest` all refuse before running, so SIG-03 and
SIG-04 stay blocked until the principal regenerates `.warden_manifest.sha256`.
