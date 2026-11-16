# Warden governance incident — reconciliation, 30 October 2026

**Disposition:** Historical XSRR trials quarantined. Protected manifest reconciled. Stale HYP-2026-126 verify remains scoped/incomplete. Distinct new research is eligible under the guarded VPS writer lock.

## Ledger reconciliation

- Verified `uv run warden check-worktree`: protected Warden core/contracts intact.
- Registry remains **137 hypotheses / 71 full trials**, with no full trial deleted or rewritten.
- `RUN-XSRR-001` and `RUN-XSRR-002` belong to `HYP-2026-128`; both historical verdicts are `REVISE`, and both six-check audits are `REJECTED`. Both previously surfaced as `eligible=true`, a dangerous interpretation of verified market provenance.
- On principal's 2026-10-30 incident-fix instruction, append-only Warden dispositions `QUARANTINED` were added as `DISP-5e36a725a0aaed91313e2a67` and `DISP-061d71716e4967380371b073`. Warden now reports `eligible=false` for both.
- The original Stage 1 selection `ATT-8c8fd4bbd6d93b7d-001` remains counted. The **159 unregistered diagnostic cells** are still a selection-bias debt; a new contract cannot erase this history. The 24h/72h holding logic ran with a four-bar label/purge contract. The family remains barred from automatic retesting or promotion.

## Registry delta (131 -> 137 hypotheses)

Six additions after the previous 2026-10-29 10:05 UTC checkpoint are accounted for:

| Approx UTC registration | Hypothesis | Source |
| --- | --- | --- |
| 10:23 | `HYP-AIR-20261029-102247` | Earlier automated campaign |
| 11:16 | `HYP-AIR-20261029-111618` | Earlier automated campaign |
| 11:21 | `HYP-AIR-20261029-112141` | Earlier automated campaign |
| 11:47 | `HYP-AIR-20261029-114744` | Earlier automated campaign |
| 17:14 | `HYP-SOC-STOIC-XSMOM19-20261030` | Source-based Stoic adaptation |
| 17:37 | `HYP-AUTO-FUNDING7D-BINANCE-20261030` | Source-based funding adaptation |

No new full trial occurred after `RUN-XSRR-002` in the current 71-trial registry snapshot.

## Manifest reconciliation

- Current manifest digest: `f38e8901905f71d3b92d5be43c033148cfcb691174a59424f0d1a69e6ba9d473` matches Warden approval metadata.
- **84** existing Git-pinned protected entries are unchanged. Exactly **two** appended research contract files match their independently recomputed SHA-256 entries.
- Added: `contracts/auto_crypto_perps_top100_1h_v1_h24_e24_f45_s20_v1.yaml` and `contracts/datasets/AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1.json`.
- Source: delegated principal authorization for research contracts documented in the prior conversation. Mode: `delegated_principal_chat_approval_unsigned`. This is **not** independent cryptographic signing and grants no holdout access.
- The remaining dirty/untracked files in the divergent quant-lab checkout were left untouched; a dirty count alone is not an integrity violation.

## Timer topology and HYP-2026-126

- Verified on actual VPS hostname `vmi2637217`: `systemctl --user list-timers --all quant-social-research.timer` finds the service. Earlier absent-unit status from another execution context does not mean the VPS service is absent.
- The timer was temporarily stopped to perform registry reconciliation without racing a Warden writer. The deployment now includes shared lock `/home/quant/dev/quant-lab/registry/warden-writer.lock` and guarded `ExecStartPre` verification. A tested durable `systemd-run` job submission/poll mechanism now exists for future long Warden verifies.
- `HYP-2026-126`: old `warden verify` request timed out outside Warden and left **no recoverable terminal receipt**. No corresponding full trial exists; two separate Stage 1 screen attempts were recorded as `REJECT`. The candidate must **not** be retried or qualified without its own durable job/reconciliation. That incomplete candidate is not a global blocker for unrelated ideas.

## Implemented recovery control

`AI-research-loop/src/workflow/governance_guard.py` performs read-only baseline manifest/digest verification, quarantine reconciliation and scoped blocker reporting. Its `run` path accepts only safe, allowlisted Warden research commands and guards the entire subprocess under a fail-fast file lock. Service file uses the same lock. It prohibits `warden qualify`, HYP-2026-128/126 aliases, unsafe spec paths and future unknown manifest edits.

Testing: 112 automated tests passed in AI-research-loop; protected Warden check passed; one guarded Warden `status --json` returned counts 137/71 and both prior XSRR trials ineligible.

## Residual limitations

1. Cooperative writer lock cannot force independent third-party agents to participate. Hourly director must be instructed to use the guarded CLI and connected VPS, not a local shell.
2. The unknown terminal result for the old HYP-2026-126 verify is **not recovered**; candidate remains blocked. Future long-run verifies use `src.workflow.durable_jobs` with stable systemd job IDs and terminal records (harmless `systemd-run` protocol smoke passed).
3. The unregistered 159 diagnostics are acknowledged but not fabricated into pseudo-return streams. They must be accounted for in any future DSR inference on that contaminated family.
4. No protected holdout was opened and no live trading was performed during reconciliation.
