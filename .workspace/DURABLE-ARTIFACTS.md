# Quant-Lab durable DevSpace artifacts index
Audited 2026-11-04 UTC. Preserve trial/holdout governance and Warden authority.

## Verified copied documentation

These are byte-identical copies of previously worktree-only files from `/home/quant/.devspace/worktrees/quant-lab-0342d36e/`; the originals remain in place:

| Canonical path (relative to /home/quant/dev/quant-lab) | SHA-256 |
| --- | --- |
| `.workspace/plans/carver_daily_decision/plan.md` | `476f7d77a2d26506e5a949876c311d973c0efe4463543452fe769c5f6b9a9340` |
| `.workspace/plans/carver_daily_decision/spec.md` | `ea8ed8f06270c13d29193d1c1d4c8b31fd21de814114da02cd1b58aa9b3de1b4` |
| `.workspace/af30_campaign/carver_daily_decision_report.md` | `0b88018e333b52df54719035ba94077f937bf628dcf8127c0bf7aface85f1389` |

The carver daily rebuild plan/spec and existing AF30 screens report already matched or existed in the main project; no replacement was made.

## Warden databases — separate lineages, NOT merged

| Location | SHA-256 | Hypotheses seen |
| --- | --- | ---: |
| `/home/quant/dev/quant-lab/registry/registry.db` | `7b100ec72c623f18f91c9da341fd5c08ece375ac90ffb45293fc3b3d08192eba` | 149 |
| `/home/quant/.devspace/worktrees/quant-lab-0342d36e/registry/registry.db` | `ee6c505545fbb1b6d5e369665d4df1d9aabf6d83e0be070f370f649961808f98` | 153 |
| `/home/quant/.devspace/worktrees/quant-lab-759d9266/.workspace/stage1_optimization/registry.db` | `1163eb106d336c0306f6e050bdff4c92e52bebd13a0820cf78d6f72733c98bac` | 154 |
| `/home/quant/.devspace/worktrees/quant-lab-759d9266/registry/registry.db` | `e590eda9caefe205f8b8de78351995e20061893e1804d93a5a9b0fffa9527b77` | 4 |

These are nonidentical read-only observations from different branch states. Do not promote later hypothesis counts into canonical trial history without verifying chain, Warden state, and admissibility. No DB was moved or modified.

## Worktree-only data to retain pending approved archive

- `quant-lab-0342d36e/data/exploratory/crypto_perps_1m.parquet` (about 106 MB).
- `quant-lab-4ac4b629/data/exploratory/crypto_perps_top100_1h_orderflow_v1.parquet` (about 94 MB).
- `quant-lab-759d9266/cache/features/` contains generated feature Parquet files (not automatically canonical).
- `ml4t-workflow-* /artifacts/` holds independent historical experiment datasets, some tens of MB each.

Do not relocate existing data files while contracts, signed hashes, notebooks or trial IDs depend on their absolute location. Copy to a stable immutable archive only with raw SHA/size verification and corrected references; never rerun research or touch sealed holdouts as part of file housekeeping.

## Entry path for future agents

Read `AGENTS.md`, this index, `registry/registry.db`, the current project `.workspace/plans`, and exact trial manifests before deciding what evidence is authoritative. This index is an audit record, not a scientific acceptance or approval to unseal holdout data.
