# Sandbox contract promotion (`warden promote-contract`)

Human-only runbook for ticket #118 (parent #109). **Read the whole page before the first
promotion.** It is the procedure for the one step that turns a compiled *exploratory*
artifact into a production **candidate**. It is not a qualification, an approval, a
signature or a holdout release.

```bash
uv run warden promote-contract \
  --contract-id SANDBOX_FX_1H_C772F0D285 \
  --human-confirm-promote YES_PROMOTE \
  --json
```

| | |
|---|---|
| Written by | `src/warden/promote.py` (library) + `warden promote-contract` (CLI) |
| Policy class | `APPROVAL` — a human-only gate, like `qualify` and `data seal-holdout` |
| Token | `--human-confirm-promote YES_PROMOTE` — a flag is intent, the token is the decision |
| Agent callable | **No.** Refused under `WARDEN_NON_INTERACTIVE=1`, and absent from the MCP adapter |
| Registry | never opened, never written |
| Holdout | never opened, never statted, never created — a raw-input record that would leave the lake is refused *before* it is statted, and a manifest that declares a holdout pin is refused outright |
| Manifest | never generated, never signed, never re-signed |

## 1. Where a promotion starts and what it produces

A promotion starts from a `warden data compile` artifact (#117). Nothing else is promotable:
a live contract, a hand-written YAML and an archived id all refuse.

| | sandbox (source, untouched) | production (the copy) |
|---|---|---|
| contract | `contracts/sandbox/<contract_id lower>.yaml` | `contracts/<contract_id lower>.yaml` |
| dataset manifest | `contracts/sandbox/datasets/<CONTRACT_ID>.json` | `contracts/datasets/<CONTRACT_ID>.json` |
| Parquet | `data/exploratory/sandbox/<dataset_id>.parquet` | **not copied** — the contract keeps pointing at it |
| receipt | — | `contracts/promotions/<CONTRACT_ID>.json` |

The two copied documents are **byte-identical** to the sandbox originals, so the production
contract still reads `data/exploratory/sandbox/…`; that file is owned and fenced by the
exploratory audit as before. The receipt is the only new evidence: who, what, when, every
digest, the compile-time `contract_sha256` it verified, the `provenance` the artifact
declares, whether a holdout pin was present, and the repository HEAD (`repo_head`, recorded
as unavailable when the tree is not a git checkout).

Promotion is **write-once per contract id**. A second attempt refuses with
`already_promoted` (a production copy or a receipt exists) or `half_promoted` (a stranded
manifest copy with no contract and no receipt). Those three checks run *first*, before any
lake or policy read, so a re-run after a routine lake change still reports
`already_promoted`/2 rather than a data refusal. A stranded copy is never completed
automatically — that is a human triage decision.

## 2. The validation order (refuse before copying anything)

Every promotion re-validates the compiled-artifact contract
(`warden.compile.ARTIFACT_CONTRACT`) in the order it states. Each step raises one named
reason; nothing is copied until every step passes.

| # | Check | Named refusals |
|---|---|---|
| 0 | **Human gate** — token, and not `WARDEN_NON_INTERACTIVE=1` | `not_authorized` |
| 1a | **Request** — the id resolves to a compiled sandbox contract, and no production contract (`.yaml`/`.yml`), receipt or target manifest exists for it | `unknown_contract`, `already_promoted`, `half_promoted` |
| 1 | **Paths** — contract, dataset manifest and Parquet exist; the manifest's `contract_id` is the contract id; the manifest's dataset ids agree; the contract's `exploratory_data_path` is the sandbox dataset path | `unknown_contract`, `invalid_contract`, `missing_manifest`, `invalid_manifest`, `mismatched_identity`, `missing_dataset` |
| 1b | **Origin shape** — the manifest declares no `holdout_path`/`holdout_sha256`/`holdout.sealed`, and neither the manifest nor the contract claims a provenance other than `unknown` | `holdout_declared`, `claimed_provenance` |
| 2 | **Bytes** — `sha256(parquet) == manifest.sha256 == contract.data_snapshot_sha256` | `unpinned_dataset`, `tampered_parquet`, `mismatched_pin` |
| 3 | **Raw inputs** — every recorded path stays inside the lake and is one the artifact listed, and the Merkle root over those files, recomputed, still matches | `mismatched_merkle`, `lake_inputs`, `unconfined_raw_input`, `lake_provenance` |
| 4 | **Spec/contract identity** — one `spec_sha256`; `sandbox.contract_sha256` (the digest the compile recorded) equals the contract bytes; the contract's `spec_hash()` is derivable (this is what the receipt records) | `invalid_manifest`, `invalid_contract`, `mismatched_identity` |
| 5 | **Policy** — the artifact's policy digest is the current signed policy; its `selection_group_id` is what `require_selection_group` resolves; its frozen costs/leverage equal `rules_for` and the contract's own frozen floors | `stale_policy`, `invalid_manifest`, `mismatched_identity` |
| 6 | **Fence** — artifact fence, contract `holdout_start` and policy `holdout_start` are one instant; the **last bar is re-read from the Parquet** through the same reader `data fence` uses, must equal `dataset.last_bar`, and its close must be `<= holdout_start`; the window is unexpired and the reviewed lifecycle document still keeps the id open | `stale_fence`, `fence_violated`, `fence_unverified`, `retired_contract` |
| 7 | **Lake provenance** — the catalogue identity and per-store acceptance identity still match; recorded inputs still exist; an excluded partition never became an input | `lake_provenance`, `lake_inputs` |
| 8 | **Namespace** — still a `spec109.sandbox-artifact.v1` artifact that declares itself non-qualifying, and its id still resolves to the sandbox namespace | `wrong_namespace` |

"Unexpired" is deliberately **not** a wall-clock test: a fence is relative to the data, not
to today, so a 2021 holdout is still a holdout in 2026. What expires a contract is the
reviewed lifecycle document (`contracts/lifecycle/contract_lifecycle.json`) or the archive —
both refuse here as `retired_contract`.

Exit codes: `0` promoted · `1` the artifact no longer supports the promotion
(`unpinned_dataset`, `tampered_parquet`, `mismatched_*`, `stale_*`, `fence_violated`,
`fence_unverified`, `unconfined_raw_input`, `lake_*`, `missing_*`, `invalid_manifest`) or the
copy failed and was rolled back (`promotion_failed`) · `2` the request is refused
(`not_authorized`, `unknown_contract`, `invalid_contract`, `already_promoted`,
`half_promoted`, `retired_contract`, `wrong_namespace`, `holdout_declared`,
`claimed_provenance`) or the worktree gate is stale.

## 3. What the operator does next — in this order

1. **Read the receipt.** `contracts/promotions/<CONTRACT_ID>.json`. It records
   `manifest_signed: false`, `manifest_generated: false`, `holdout_opened: false`,
   `qualification_eligible: false`, `holdout_pin_present: false`, the `provenance` the
   artifact declares, the compile-time `contract_sha256` that was verified against the copied
   bytes, and `repo_head`. Those are facts about this command, not promises; a caller's extra
   block cannot overwrite any of them.
2. **Re-sign the protected inventory.** Promotion copies into `contracts/`, which is
   protected, so `warden check-worktree` now returns rc=2 *by design*:
   ```bash
   uv run warden generate-manifest --human-approve-manifest   # HUMAN ONLY
   uv run warden check-worktree                              # expect rc=0
   ```
   An agent may not do this, and re-signing is not an approval of the science — it is the
   integrity gate.
3. **Attest admission, separately.** The copied manifest still declares
   `provenance: unknown`, so the promoted contract is `STAGED` until a signed-origin
   admission attestation exists (`contracts/attestations/<CONTRACT_ID>.json`,
   `$WARDEN_ADMISSION_HMAC_KEY`). `promote-contract` never writes that file.
4. **Only then does qualification become a question.** `qualify` still needs a trial that is
   `CANDIDATE_FOR_QUALIFICATION`, market-eligible, with a finalized attempt receipt, and the
   explicit human instruction `AGENTS.md` requires.

## 4. Why promotion alone is not holdout eligibility — and what is *not* proved

What this command proves, on the bytes:

- The receipt writes `qualification_eligible: false`, and the copied contract carries no
  admission attestation, so `resolve_dataset` reports `eligible_market: false` with
  `provenance: unknown` — the ordinary gate refuses.
- A holdout read on the promoted copy is refused on the *manifest pin*
  (`holdout pin missing: manifest declares no 'holdout_path'`) before any holdout path is
  statted. The copied manifest is byte-identical, and step 1b refuses a manifest that
  declares a pin or a provenance claim at all, so a promotion cannot carry one into
  `contracts/`. (This is a refusal of the *shape*, which is the barrier the sandbox
  namespace supplied for free — not a check that the two claims agree.)
- The last bar is re-read from the Parquet: the manifest's `dataset.last_bar` is only a
  cross-check, so a re-pinned Parquet with a bar inside the holdout window refuses
  `fence_violated` instead of promoting.
- Every recorded raw input is confined to the lake and must be one the artifact listed, so a
  promotion cannot open (or measure) a file outside it, including a holdout byte.
- The sandbox original keeps its `contracts/sandbox/` document, so the trial that ran
  against it is still the sandbox contract's trial. After promotion its refusal *moves* from
  the namespace refusal to the ordinary provenance gate — it never becomes a claim. No
  `holdout_claims` or `qualifications` row is written, and `data/holdout/` is never created.
- The exploratory fence still owns the Parquet: the sandbox manifest and its promoted copy
  are byte-identical declarations of one artifact, so the audit reports one `fenced` owner
  (two *different* manifests claiming the same bytes remain `ambiguous_owner`), and the fence
  reads the **production** contract for a promoted id, never the sandbox copy.

What it does **not** prove — stated so the heading is not read as more than it is:

- It does not attest the *origin* of the bytes: `provenance: unknown` is copied verbatim and
  only a human admission attestation can change what a consumer sees.
- It does not re-derive the lake catalogue beyond its recorded acceptance identity, and it
  does not re-run the compile's quality gate; the artifact's own evidence is what is
  re-checked.
- It does not stop a human from signing a manifest that every gate here refused. The
  refusals bind this command, not the signing ceremony.

## 5. Never

- Never run `promote-contract` with `WARDEN_NON_INTERACTIVE=1` (it refuses) or paste the
  token into an agent prompt expecting a promotion.
- Never edit the production copy to "fix" a refusal. The refusal names a field; the fix is a
  recompile under the current policy (`warden data compile`) and then a fresh promotion.
- Never treat the receipt, the re-signed manifest or a passing `check-worktree` as an
  approval, an attestation or a holdout release.
- Never let an agent remove the three promotion files to "retry". Undoing a promotion is a
  human decision, and the receipt stays as the record of what was copied and when.
