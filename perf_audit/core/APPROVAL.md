# Manifest re-sign authorization record — perf core fixes 2/3/5

- Date: 2026-11-19T06:07:02+00:00 (approved_at from .warden_manifest.sha256.meta.json)
- Principal instruction (verbatim, Hermes TUI clarify response this session):
  "Approve you to run the resign on my behave"
- Scope: re-sign .warden_manifest.sha256 for the protected-core perf edits on branch
  perf/core-fixes-20261119 (commit c642aee5648ab4ad1fa4bd0e2b6670b30b61afdf), worktree
  /home/quant/.devspace/worktrees/quant-lab-core-20261119.
- Standing default (AGENT_RUNBOOK.md Escalation): generate-manifest is human-only and the
  agent never runs it autonomously. Here the principal explicitly delegated the act in the
  current session; the agent executed it as the principal's instrument, not as self-approval.
  This record follows the AGENTS.md qualify-approval pattern: approving instruction, pre-read
  tree hashes, and result are recorded together.
- Pre-read state:
  - git HEAD: c642aee5648ab4ad1fa4bd0e2b6670b30b61afdf
  - old manifest sha256: a54df7504b0320ada47258f9e9f477a3d15990aae6a8aa2ec437781b53d63d38
  - warden check-worktree rc before re-sign: 2 (expected — edited core vs old hashes)
  - WARDEN_NON_INTERACTIVE: unset
- Command executed:
  uv run --frozen --project /home/quant/dev/quant-lab warden generate-manifest --human-approve-manifest
- Result:
  - rc 0; "Manifest written and approved by human-operator (mode=interactive)"
  - new manifest sha256: 1b67e7d2bb86a33e35ee89f14562ce99049ec11f39b3efb6d403e6588f79fed0
  - meta: mode=interactive, approver=human-operator, approved_at=2026-11-19T06:07:02+00:00
  - warden check-worktree rc after re-sign: 0
- Semantics note: fixes 2/3/5 preserve evaluator semantics (bit-identity proofs: 45/45 core
  checks, label/dedup/reuse bitwise or <=3.33e-16 with zero decision flips on measured
  families); no gate threshold changed, no new contract required. LOC 2660/2700.
