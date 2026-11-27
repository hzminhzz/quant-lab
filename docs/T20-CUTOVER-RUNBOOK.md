# T20 / #106 — contract lifecycle, exploratory fence and cutover runbook

## Scope and successor release plan

This records T20's implementation and host obligations; its commands and fixture
results are not evidence that a live cutover occurred. For the proposed coherent
agent-operated release/migration path, use [ARCHITECTURE.md](ARCHITECTURE.md) and
[AGENT-CONTROL-PLAN.md](AGENT-CONTROL-PLAN.md). The agent prepares exact artifacts
and evidence; the principal makes protected decisions through an authenticated
available channel. That target integration is not yet implemented.

Retain the obligations below in any new cutover packet. Release admission,
production migration, dataset/origin attestation and holdout authorization are
separate effects. No target document or green fixture substitutes for them.

Local implementation is complete and exercised on synthetic fixtures. **Nothing in this
document has been executed against production data.** The remaining rows are host steps
that need a principal, real vendor exports, the operator-held HMAC key, and a signed
manifest — they cannot be produced by a test run, and a green fixture suite is not
evidence that they happened.

## What is implemented (local, reversible)

| Piece | Where | What it refuses |
|---|---|---|
| Read-only lifecycle registry | `contracts/lifecycle/contract_lifecycle.json`, `src/warden/lifecycle.py` | a *new* hypothesis on any of the 10 `DEPRECATED_UNEXECUTABLE` or 3 `LEGACY_SURVIVORSHIP_BIASED` contracts (`register-hypothesis`, and `advance`'s auto-registration) |
| Lifecycle document validation | same | the whole gate when the document is unparseable/ambiguous (unknown status, duplicate id, unknown key, replacement that is not `ACTIVE_V2`) — corruption is never read as "no entries" |
| Exploratory fence | `warden data fence`, `src/warden_evidence/exploratory_fence.py` | exit ≠ 0 unless **every** `*.parquet` under `data/exploratory` is owned by exactly one manifest (digest equal) whose contract's `holdout_start` is strictly after the file's last bar |
| Signed-origin admission | `src/warden/provenance.py::resolve_admission` → `src/warden_evidence/data_admission.py` | market eligibility of a **v2** market dataset without a verified HMAC origin attestation. v1 contracts report `legacy_grandfathered` and keep their historical eligibility |
| Replacement fixtures | `tests/fixtures/p2_t20/{contracts,specs}` | — (rehearsal only; they are not installed under `contracts/`) |
| Protected inventory | `src/warden_ops/integrity.py` | deleting/editing the lifecycle document invalidates `warden check-worktree` once the manifest is re-signed |

Ordering invariant: the lifecycle document is consulted by the two *registration* paths only.
`status`, `spec-status`, `verify-causality` and `audit-check` never import it, so a trial
recorded under a deprecated contract reads back exactly as recorded and its v1 `spec_hash`
does not move (36/36 production contract hashes are byte-identical to the frozen base).

## Host steps still owed (in order)

1. **Re-sign the protected manifest** (human only):
   `uv run warden generate-manifest --human-approve-manifest`. Until then
   `warden check-worktree` exits 2 — that is the unsigned-candidate state, not a pass.
2. **Build the three real replacement datasets** with the reviewed specs, from real
   vendor exports, one at a time:
   - `crypto_perps_broad_1h_v2.yaml` → PIT Binance USD-M broad 1h (delisting-complete
     catalog, forward 8h funding, `missing_policy` as reviewed);
   - `fx_majors_1d_ny_close_v2.yaml` → Dukascopy daily on the 17:00 New York session clock;
   - `cme_es_nq_15m_syncster_v2.yaml` → `composite_union` of the two SHA-pinned Syncster
     CME parents.
   Each needs the real partition roots / parent exports and a declared fallback rate; the
   fixture paths under `tests/fixtures/p2_t20/specs` are shapes, not paths on the host.
3. **Pin the contracts**: set `dataset_id` + `dataset_manifest_sha256` in each v2 contract
   to the published manifest, then re-sign (step 1). A contract whose pin is empty is not
   admitted to a market funnel.
4. **Seal the holdouts** (human-only, per dataset):
   `warden data seal-holdout <spec> --seal-holdout --human-confirm-seal YES_SEAL_HOLDOUT`,
   then root ownership `install -o root -g trader -m 0640 data/holdout/<dataset_id>.parquet`.
   Sealing is refused before any input I/O unless the token, the manifest pin and the
   exploratory bytes all match.
5. **Attest origin** for each market dataset: place the operator-signed
   `contracts/attestations/<CONTRACT_ID>.json` (schema 1, HMAC over the canonical row) and
   export `WARDEN_ADMISSION_HMAC_KEY` in the operator shell only. Without it a v2 market
   dataset resolves `STAGED` and is not market eligible — by design, not by accident.
6. **Run the fence on the real store**: `uv run warden data fence` must exit 0 (every file
   owned, digest equal, last bar < `holdout_start`) before any hypothesis is registered on a
   replacement contract. Report the JSON payload verbatim in the cutover record.
7. **Archive the six legacy scripts** — only after steps 2–6 confirm the replacements:
   `scripts/salvage_crypto.py`, `scripts/salvage_crypto_perps.py`,
   `scripts/salvage_multi_asset.py`, `scripts/build_crypto_perps_broad_1h.py`,
   `scripts/build_fx_majors_1d_v2.py`, `scripts/prepare_syncster_cme.py`.
   They are listed in the lifecycle document as `pending_replacement` and are still present
   in the tree; moving them to a dated archive directory and marking each entry `archived`
   is a reviewed edit plus a re-sign. Do not archive them on fixture evidence: the V1 panels
   they produced are still pinned by contracts that carry historical trials.
8. **Record the cutover**: which commit, which manifest digest, which holdout digests, which
   attestation digests, and the fence payload. Absent those, #106 stays open.

## What a synthetic green suite does **not** prove

- No real dataset was built, sealed, attested or fenced; no holdout byte was opened.
- No market evidence exists for any replacement contract: `evidence_tier` in every fixture
  is `synthetic`, so the best a fixture funnel can reach is `PROXY_HOLDOUT_PASS`.
- The DSR inheritance is proven against *recorded trials* in a temporary SQLite file, never
  against production search history.
- Archival, contract activation, key custody and the host transition remain human authority.
