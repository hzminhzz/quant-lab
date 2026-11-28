# Producer / consumer compatibility — 2026-11-28

Producer candidate: `hzminhzz/ml4t-data-project@de21906e51ce1b0bc0661b7925025cea6d130e6c`. The exact Phase 2 source `b141a6f82931281620f81816cdcd9f267c560983` was reconciled with current main `bd222e29c4371ee3ec6e0deee03c28ca8efe68d9`. Phase 2 synthetic demonstrations remain demonstrations, not scored research.

Read the producer's `docs/cross-repo-data-compat-20261128.md` for reproduction and native real-data evidence: three successive BTC publications retaining historical partition bytes; 100 stores, 187,617,288 rows reconstructed from actual Parquet; 1,035 catalog entries with valid identities; real native reads of seven FX/metals/futures instruments. The final six-request report is at `/tmp/ml4t-compat-full-20261128.json` (SHA-256 `8e88d62bd32c5f1c4f291a5757ada631f23a386f9483b7c5fd6d939c89860a4f`).

The resolver verifies catalog, metadata and acceptance identity, uses declared data kind and instrument semantics, preserves source coverage and distinguishes missing symbols, incompatible basis, unsupported semantics, insufficient history and untrusted policy. Compiler re-resolves caller reports and binds producer metadata before reads. No arbitrary dataset substitutions. Legacy requirements without an instrument constraint retain their serialized identity. No new orchestration layer.

Focused verification: 107 tests passed; fast Warden verification collected 1,490 and passed locked installed byte, import, inventory, lint and AST checks. Producer focused gate: 134 passed, 1 skipped, 2 subtests passed. Full producer verification is not green; see producer evidence. The active producer checkout contains concurrent changes that overlap the writer/scheduler files, and active units point to it. This document does not authorize activation.

## Research recovery

The original six requirements were replayed against the reconstructed native catalog. Weekend crypto and index proxy reached compile_required; crypto panel correctly failed insufficient_history; funding majors and FX quotes were incompatible_price_basis; commodity daily lacked ten symbols and had continuous GC/SI, not individual maturities. Additional FX/metals OHLCV requirements reached compile_required. Actual native compile attempts for weekend crypto, index proxy and FX OHLCV refused at the trusted-policy gate before reading research prices.

All fourteen tracks were revisited without changing their frozen comparators, targets, or economic hypotheses:
- R01: weekend crypto and index price data discovered; variance target, exact parent/comparator and policy needed.
- R02: European-open drift requires its exact approved session/horizon contract and after-cost economics.
- R03: index proxy discovered; CFD OHLCV is not exchange traded volume, exact baseline/child approval required.
- R04: funding history present; mark, funding-clock and native combined-price basis unresolved.
- R05: insufficient panel history; point-in-time listing/delisting and frozen universe remain necessary.
- R06: dependent multi-mechanism predictor remains premature before component evidence.
- R07: aim execution requires eligible parent and executable cost/calibration evidence.
- R08: continuous futures cannot establish individual curve maturities/rolls.
- R09: COT product identity and release-time alignment must be established.
- R10: indexed commodity daily and roll evidence incomplete.
- R11: dynamic spot/perp carry requires synchronized spot, borrow and funding events.
- R12: bid FX OHLCV discovered, executable fixing quotes not established.
- R13: settlement-cycle point-in-time contract remains deferred.
- R14: dealer exposure and expiry data remain deferred.

Canonical policy approval, exact prospective research intents, independent conformance and eligible scientific operations are absent. No approved score, ledger write, sealed holdout access or live order was attempted. Research blockers must be resolved through existing Warden governance, not by changing requirements to accept convenient proxies.
