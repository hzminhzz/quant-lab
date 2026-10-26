# Trust boundary: evaluator ownership, human signing, holdout isolation

Status: **NOT APPLIED — BLOCKED pending the human operator.** The coding agent has not run
`scripts/apply_trust_boundary.sh --apply`, has not changed ownership or permissions, and has not
created an operator key. This document is the design the operator must ratify before cutover.

## 1. Why the current identity is not a boundary

The `quant` account is a member of `sudo` and `docker`; `/var/run/docker.sock` is `root:docker`.
Docker group membership is root-equivalent (mount the host filesystem, rewrite root-owned files),
and sudo is explicitly root. Therefore:

- any file `quant` can rewrite can be rewritten even if it is chmod-protected against `quant`;
- any manifest `quant` can write can be re-hashed by `quant`;
- any signature verifiable with a symmetric key held on this host can be produced on this host.

A self-generated checksum is integrity evidence, never approval. The current `quant` identity
**cannot** be trusted as-is for evaluator ownership, manifest approval or holdout isolation.

## 2. Required boundary

Two identities plus an offline human key, enforced by the OS and by external repository
controls — not by prompt text.

| Role | Identity | Owns | Must not have |
|---|---|---|---|
| Coding agent | `qagent` (unprivileged uid) | `experiments/active/`, `strategies/candidates/`, `tests/experiments/`, `.workspace/`, `cache/` | sudo, docker, `qtrust`, `qholdout`, `trader` groups; docker socket; any access to the signing key or holdout |
| Evaluator | `qeval` (human-owned uid) | `src/warden/`, `contracts/`, `contracts/datasets/`, `.warden_manifest*`, governance scripts, installed package | passwordless write access from `qagent` |
| Human operator | separate account on an operator-controlled machine | offline Ed25519 private key, `/data/holdout` (`root:qholdout 0700`), unseal decisions | agent access to the key or the holdout |

Details:

1. **Evaluator seam.** `qagent` may only invoke a `qeval`-owned wrapper
   (`/etc/sudoers.d/warden-eval`: `qagent ALL=(qeval) NOPASSWD: /usr/local/bin/warden-eval`).
   The wrapper allows `status|check-worktree|verify|screen|backtest|audit-check|register-hypothesis|handoff|continue`
   and hard-refuses `qualify` and `generate-manifest`. Evidence is written to a `qeval`-owned,
   `qagent`-readable setgid directory.
2. **Signing.** Ed25519 with the private key offline (operator machine / hardware token). The
   public key is pinned in `src/warden/trust_root.py`, which is itself part of the protected
   manifest set. `check_worktree` requires: manifest path set exactly equals the protected set,
   every digest matches, and the detached `.warden_manifest.sha256.sig` verifies over the exact
   manifest bytes with the pinned key. Missing/unpinned/invalid signature → exit 2, no promotion.
   There is no environment-variable key path and no interactive `--human-approve-manifest` flag.
3. **Holdout.** Production path `/data/holdout`, owned by the human principal, mode 0700, not
   traversable by `qagent`. Unsealing is an interactive human action with a signed unseal record;
   `WARDEN_NON_INTERACTIVE=1` refuses it and there is no API override.
4. **External authority.** Protected `main` branch with required review and required status
   checks, `.github/CODEOWNERS` on `src/warden/`, `contracts/`, `scripts/`, `.warden_manifest*`,
   and CI that runs a clean-checkout hermetic suite plus `check-worktree --json`.
5. **Scope escalations.** A new three-attempt lineage budget requires an append-only
   `scope_authorizations` record signed with the pinned human key (root hypothesis, new
   hypothesis, contract, module, rationale, approver, evidence digest). Without a verifying
   signature, registration stays fail-closed at the exhausted lineage.

## 3. Human cutover procedure (operator only)

```bash
# 0. Prepare identities and the offline key OUTSIDE the repository (never committed).
sudo useradd --system --create-home qagent          # no sudo, docker, or trust groups
sudo groupadd warden-trust && sudo usermod -aG warden-trust <operator-account>
test "$(stat -c '%a' "$WARDEN_HUMAN_KEY_FILE")" = 600

# 1. Dry run: validates every precondition without changing anything.
scripts/apply_trust_boundary.sh

# 2. Read the machine-readable assessment.
python3 scripts/preflight_trust_boundary.py --json --agent-uid "$WARDEN_AGENT_UID"

# 3. Apply only after recording the approval. The script refuses before any mutation if a
#    precondition fails, and records pre-apply uid/gid/mode for every protected path first.
WARDEN_HUMAN_APPROVED_CUTOVER=1 \
WARDEN_OPERATOR=qeval WARDEN_AGENT_UID=... WARDEN_HUMAN_KEY_FILE=/secure/human.ed25519 \
  sudo -E scripts/apply_trust_boundary.sh --apply
```

Verification after cutover:

```bash
sudo -u qagent -- uv run --frozen --no-sync warden generate-manifest   # must REFUSE
sudo -u qagent -- python3 scripts/preflight_trust_boundary.py --json   # COMPLIANT=true
uv run warden check-worktree --json                                    # signed manifest verifies
```

## 4. Rollback

`sudo scripts/apply_trust_boundary.sh --rollback` restores **exactly** the uid/gid/mode recorded
immediately before the apply (`.warden_state/trust_boundary/pre_apply.tsv` plus its digest). It
refuses to run without that record, refuses a digest mismatch, and never derives defaults from
root or from the current user. Signed manifest history and audit evidence are left untouched.

## 5. What remains true before cutover

- `warden check-worktree` reports **BLOCKED** (unsigned or manifest mismatch). That is the correct
  state; it must not be "fixed" by regenerating the manifest without a human signature.
- No candidate is qualified, no holdout is unsealed, no live order is placed.
- `scripts/apply_trust_boundary.sh` has never been run with `--apply` by an agent.
