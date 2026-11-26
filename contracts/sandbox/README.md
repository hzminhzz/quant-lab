# Sandbox Contracts (`contracts/sandbox/`)

Agent-writable **exploratory** evaluation contracts and their dataset manifests
(`contracts/sandbox/datasets/`), compiled by `warden data compile` from a declarative
`DataRequirement` (#117).

This namespace does **not** qualify.  Enforced by Warden:

- `warden check-worktree` does not seal `contracts/sandbox/*.yaml` or
  `contracts/sandbox/datasets/*.json`, so compiling does not invalidate (or re-sign) the
  human manifest.  Compiles therefore stay cheap; promotion re-validates every digest.
- A sandbox contract runs the ordinary pipeline (`register-hypothesis`, `verify`, `screen`,
  `backtest`, `advance`) with no production signing.  Its dataset is `unknown` provenance,
  so a Stage 3 verdict is `NON_PROMOTABLE`: observed market history, no attested origin.
- Production wins a name clash: `contracts/<id>.yaml` is always resolved before
  `contracts/sandbox/<id>.yaml`.
- `warden qualify` hard-refuses any contract resolved from this namespace.  Promotion into
  `contracts/` + `contracts/datasets/`, plus the human re-sign, is a separate step.
- Each artifact is write-once: the Parquet digest, the raw-input Merkle root, the policy
  digest, the fence and the contract bytes are re-checked before a second compile reuses
  it; anything that moved is refused rather than overwritten.
