"""Command-line interface for the Warden quantitative governance harness."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

from warden import __version__
from warden.contracts import canonical_json, load_hypothesis
from warden.leakage import compute_and_cache_features, verify_prefix_invariance
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner, load_module_from_path

MANIFEST_FILE = ".warden_manifest.sha256"
STATE_FILE = Path(".workspace") / "state" / "current.json"
HISTORY_FILE = Path(".workspace") / "state" / "history.jsonl"
WORKTREE_OK = "Worktree integrity verified: protected contracts, warden core, and LOC budgets intact."
ALLOWED_CMD_PREFIXES = ("git rev-parse", "sqlite3 ")


def compute_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_manifest(repo_root: Path = Path(".")) -> str:
    repo = Path(repo_root)
    protected = (
        sorted(repo.glob("src/warden/**/*.py"))
        + sorted(repo.glob("contracts/*.yaml"))
        + sorted(repo.glob("contracts/*.yml"))
    )
    protected += [
        p for p in (
            repo / "AGENTS.md",
            repo / "pyproject.toml",
            repo / "uv.lock",
            repo / "scripts" / "check_loc_budget.py",
            repo / "scripts" / "lock_holdout_permissions.sh",
            repo / "scripts" / "lock_governance_permissions.sh",
        ) if p.is_file()
    ]
    protected = list(dict.fromkeys(protected))
    content = "\n".join(f"{compute_file_sha256(p)}  {p.relative_to(repo)}" for p in protected) + "\n"
    (repo / MANIFEST_FILE).write_text(content, encoding="utf-8")
    return content


def check_worktree(repo_root: Path = Path(".")) -> bool:
    repo = Path(repo_root)
    manifest = repo / MANIFEST_FILE
    if not manifest.is_file():
        return False
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, rel = line.strip().split(maxsplit=1)
        target = repo / rel
        if not target.is_file() or compute_file_sha256(target) != expected:
            return False
    loc = repo / "scripts" / "check_loc_budget.py"
    if not loc.is_file():
        return False
    return subprocess.run([sys.executable, str(loc)], capture_output=True, check=False).returncode == 0


def cmd_check_worktree(args: argparse.Namespace, runner: ExperimentRunner | None = None) -> int:
    if not check_worktree():
        return 2
    print(WORKTREE_OK)
    return 0


def cmd_status(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    with runner.registry._connect() as conn:
        n_hyp = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
        n_trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        rows = conn.execute("SELECT run_id, hypothesis_id, strategy_family, net_sharpe, deflated_sharpe_prob, verdict FROM trials ORDER BY created_at DESC LIMIT 5").fetchall()
    recent = [{"run_id": r[0], "hypothesis_id": r[1], "strategy_family": r[2], "net_sharpe": float(r[3]), "deflated_sharpe_prob": float(r[4]), "verdict": r[5]} for r in rows]
    data = {"warden_version": __version__, "hypotheses_count": n_hyp, "trials_count": n_trials, "recent_trials": recent}
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"Warden v{__version__} | Hypotheses: {n_hyp} | Trials: {n_trials}\n" + "\n".join(f"  {t['run_id']} ({t['strategy_family']}): NetSR={t['net_sharpe']:.2f}, DSR={t['deflated_sharpe_prob']:.2f} -> {t['verdict']}" for t in recent))
    return 0


def cmd_verify(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    if not check_worktree():
        sys.exit(2)
    import polars as pl
    import yaml
    cfg = yaml.safe_load(Path(args.spec_yaml).read_text(encoding="utf-8"))
    contract = runner.resolve_contract(cfg["contract_id"])
    mod = load_module_from_path(runner.repo_root / cfg["strategy_module"])
    data_path = runner.repo_root / contract.exploratory_data_path
    if not data_path.is_file():
        data_path = runner.repo_root / "data" / "exploratory" / "crypto_perps_1h.parquet"
    panel = pl.read_parquet(data_path)
    pipeline = getattr(mod, "pipeline", None) or getattr(mod, "FeaturePipeline", None) or (mod if hasattr(mod, "compute") else None)
    if not pipeline:
        print("Error: Strategy module does not expose a FeaturePipeline or compute() method.", file=sys.stderr)
        return 1
    verify_prefix_invariance(pipeline, panel, contract)
    compute_and_cache_features(pipeline, panel, contract, registry=runner.registry)
    res = {"status": "PASS", "prefix_invariance": True, "cached": True, "rows": len(panel)}
    print(json.dumps(res, indent=2) if args.json else "PASS: Prefix-invariance verified.")
    return 0


def cmd_screen(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    if not check_worktree():
        sys.exit(2)
    report = runner.run_stage1_screen(Path(args.spec_yaml))
    print(canonical_json(report.to_dict()) if args.json else f"Screen Stage 1 complete: Family={report.strategy_family}, Passed={report.passed}, Notes={report.notes}")
    return 0 if report.passed else 1


def cmd_backtest(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    if not check_worktree():
        print("WARDEN_RUN_FAILED")
        sys.exit(2)
    try:
        res = runner.run_spec(Path(args.spec_yaml))
        print(canonical_json(res.to_dict()) if args.json else f"Backtest complete: Run={res.run_id}, NetSR={res.net_sharpe:.2f}, DSR={res.deflated_sharpe_prob:.3f}, Verdict={res.verdict}")
        print("WARDEN_RUN_COMPLETE")
        return 0
    except (ValueError, RuntimeError, KeyError, FileNotFoundError) as exc:
        print(f"Backtest failed: {exc}\nWARDEN_RUN_FAILED", file=sys.stderr)
        return 1


def cmd_audit_check(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    if not check_worktree():
        sys.exit(2)
    with runner.registry._connect() as conn:
        t = conn.execute("SELECT * FROM trials WHERE run_id = ?", (args.run_id,)).fetchone()
        if not t:
            print(f"Error: Run ID '{args.run_id}' not found.", file=sys.stderr)
            return 1
        trial = dict(t)
        f_row = conn.execute("SELECT prefix_invariance_passed FROM feature_sets WHERE contract_id = ? AND prefix_invariance_passed = 1", (trial["contract_id"],)).fetchone()
        has_causal_proof = f_row is not None

    contract = runner.resolve_contract(trial["contract_id"])
    r_path = runner.repo_root / trial.get("returns_parquet_path", "")
    trimmed_ok = False
    if r_path.is_file():
        import polars as pl
        r_series = pl.read_parquet(r_path)["net_return"].to_numpy()
        if len(r_series) > 0:
            p95 = float(np.percentile(r_series, 95.0))
            trimmed_ok = float(np.mean(r_series[r_series <= p95])) > -0.001

    c = {
        "1_target_rebalance_alignment": trial.get("stage_reached") in {"STAGE_2_BACKTEST", "STAGE_3_STRESS"} and contract.label_horizon_bars >= 1,
        "2_outlier_sensitivity": trimmed_ok and trial.get("net_sharpe", 0.0) > 0.0 and trial.get("verdict") == "CANDIDATE_FOR_QUALIFICATION",
        "3_point_in_time_causality": has_causal_proof and contract.execution_delay_bars >= 1,
        "4_fold_local_fitting": (contract.n_folds >= 2 and contract.embargo_bars >= 1) or trial.get("strategy_family") == "rules_ts",
        "5_cost_and_latency_survival": (trial.get("stress_2x_cost_sharpe", -1.0) > 0.50) and (trial.get("delay_plus1_sharpe", -1.0) > 0.0),
        "6_selection_bias_accounting": (trial.get("total_trials_n", 0) >= 1) and (trial.get("deflated_sharpe_prob", 0.0) >= 0.80),
    }
    passed = all(c.values())
    import time
    with runner.registry._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO audit_reports (run_id, contract_id, verdict, checks_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (args.run_id, trial["contract_id"], "APPROVED" if passed else "REJECTED", canonical_json(c), time.time()),
        )
    audit = {"run_id": args.run_id, "checks": {k: {"passed": v} for k, v in c.items()}, "audit_passed": passed}
    out = runner.repo_root / ".workspace" / "hypotheses" / f"AUDIT_{args.run_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"# 6-Check Audit: {args.run_id}\nVerdict: {'APPROVED' if passed else 'REJECTED'}\n" + "\n".join(f"- [{ 'PASS' if v else 'FAIL' }] {k}" for k, v in c.items()) + "\n")
    print(json.dumps(audit, indent=2) if args.json else f"Audit for {args.run_id}: {'PASS' if passed else 'FAIL'}\nReport: {out}")
    return 0 if passed else 1


def _contract_pins(repo_root: Path) -> dict[str, str]:
    import yaml
    pins = {}
    for p in sorted((repo_root / "contracts").glob("*.y*ml")):
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        pins[doc["contract_id"]] = doc.get("data_snapshot_sha256", "")
    return pins


def _build_state(repo_root: Path, ticket: str | None, notes: str) -> dict:
    """Typed, replayable checkpoint: evidence with timestamps, tool trace, explicit gates."""
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    registry = ResearchRegistry(repo_root / "registry" / "registry.db")
    with registry._connect() as conn:
        n_trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        n_hyp = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
    pins = _contract_pins(repo_root)
    trace = [
        {"cmd": "git rev-parse --short HEAD", "expect": head},
        {"cmd": 'sqlite3 registry/registry.db "SELECT COUNT(*) FROM trials"', "expect": str(n_trials)},
        {"cmd": "uv run warden check-worktree", "expect": WORKTREE_OK},
    ]
    return {
        "schema": 1, "created_at": now, "ticket": ticket or "N/A", "notes": notes,
        "evidence": [
            {"source": "git", "kind": "head", "value": head, "observed_at": now},
            {"source": "registry.db", "kind": "trials", "value": n_trials, "observed_at": now},
            {"source": "registry.db", "kind": "hypotheses", "value": n_hyp, "observed_at": now},
            {"source": "contracts", "kind": "pinned_snapshot_sha256", "value": pins, "observed_at": now},
        ],
        "tool_trace": trace,
        "quality_gates": {"worktree_integrity": check_worktree(repo_root), "contracts_pinned": all(pins.values())},
    }


def cmd_handoff(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    state = _build_state(runner.repo_root, args.ticket, args.notes)
    current = runner.repo_root / STATE_FILE
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    with (runner.repo_root / HISTORY_FILE).open("a", encoding="utf-8") as history:
        history.write(json.dumps(state, sort_keys=True) + "\n")
    print(f"Handoff checkpoint recorded: {STATE_FILE} (append-only log: {HISTORY_FILE})")
    print(f"Gates: {json.dumps(state['quality_gates'])}")
    return 0


def _legacy_trace(text: str) -> list[dict]:
    """Adapter for pre-schema-1 Markdown transitions: keep them auditable, never rewrite them."""
    block = (re.findall(r"```bash\n(.*?)\n```", text, re.DOTALL) or [""])[0].splitlines()
    entries, i = [], 0
    while i < len(block):
        cmd = block[i].strip()
        i += 1
        if not cmd or cmd.startswith("#"):
            continue
        expect = block[i].strip()[9:].strip() if i < len(block) and block[i].strip().startswith("# expect:") else None
        if expect is not None:
            i += 1
        entries.append({"cmd": cmd, "expect": expect})
    return entries


def _replay(entries: list[dict], runner: ExperimentRunner) -> bool:
    import shlex
    drift = False
    for entry in entries:
        cmd, expected = entry.get("cmd", ""), entry.get("expect")
        if not (cmd.startswith(ALLOWED_CMD_PREFIXES) or "warden check-worktree" in cmd):
            print(f"REFUSED: Command '{cmd}' is not in the read-only continuation allowlist.", file=sys.stderr)
            drift = True
            continue
        proc = subprocess.run(shlex.split(cmd), cwd=runner.repo_root, capture_output=True, text=True, check=False)
        actual = (proc.stdout or proc.stderr).strip()
        last_line = actual.splitlines()[-1] if actual.splitlines() else actual
        if expected and actual != expected and last_line != expected:
            hint = ""
            if cmd.startswith("sqlite3 ") and actual.isdigit() and str(expected).isdigit() and int(actual) > int(expected):
                hint = " (state advanced; regenerate with `uv run warden handoff` if legitimate)"
            print(f"DRIFT: '{cmd}' expected '{expected}', got '{actual}'{hint}", file=sys.stderr)
            drift = True
    return drift


def cmd_continue(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    if args.transition_path:
        target = Path(args.transition_path)
    elif (runner.repo_root / STATE_FILE).is_file():
        target = runner.repo_root / STATE_FILE
    else:
        legacy = sorted(runner.repo_root.glob(".workspace/transitions/*/*.md"))
        target = legacy[-1] if legacy else None
    if not target or not target.is_file():
        print("No handoff checkpoint found.", file=sys.stderr)
        return 1
    if target.suffix == ".json":
        state = json.loads(target.read_text(encoding="utf-8"))
        if state.get("schema") != 1:
            print(f"REFUSED: unsupported state schema {state.get('schema')!r}", file=sys.stderr)
            return 1
        drift = _replay(state.get("tool_trace", []), runner)
        failed = sorted(k for k, v in state.get("quality_gates", {}).items() if not v)
        if failed:
            print(f"DRIFT: recorded quality gates failed at handoff: {failed}", file=sys.stderr)
            drift = True
    else:
        drift = _replay(_legacy_trace(target.read_text(encoding="utf-8")), runner)
    print("Continue FAILED: Drift detected." if drift else f"Continue SUCCESS: zero drift from {target.name}.")
    return 1 if drift else 0


def cmd_qualify(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    if not check_worktree():
        sys.exit(2)
    if not getattr(args, "unseal_holdout", False):
        print("Error: Holdout qualification requires --unseal-holdout flag.", file=sys.stderr)
        return 2
    from warden.qualify import qualify_candidate
    res = qualify_candidate(args.run_id, args.human_confirm_unseal, runner)
    print(canonical_json(res) if args.json else f"Qualification complete: {res['verdict']}")
    return 0 if res["promoted"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="warden", description="Deterministic quantitative research harness.")
    p.add_argument("--version", action="version", version=f"warden {__version__}")
    s = p.add_subparsers(dest="subcommand")
    s.add_parser("status")
    s.add_parser("check-worktree")
    s.add_parser("register-hypothesis").add_argument("path")
    for name in ["verify", "screen", "backtest"]: s.add_parser(name).add_argument("spec_yaml")
    s.add_parser("audit-check").add_argument("--run-id", required=True)
    h = s.add_parser("handoff")
    h.add_argument("--notes", default="Session transition checkpoint")
    h.add_argument("--ticket", default=None)
    s.add_parser("continue").add_argument("transition_path", nargs="?", default=None)
    q = s.add_parser("qualify")
    q.add_argument("--run-id", required=True)
    q.add_argument("--human-confirm-unseal", default="")
    q.add_argument("--unseal-holdout", action="store_true")
    for sub in s.choices.values():
        if "json" not in [a.dest for a in sub._actions]: sub.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.subcommand:
        parser.print_help()
        return 0
    runner = ExperimentRunner()
    if args.subcommand == "register-hypothesis":
        if not check_worktree():
            sys.exit(2)
        h = load_hypothesis(Path(args.path))
        runner.registry.register_contract(runner.resolve_contract(h.contract_id))
        runner.registry.register_hypothesis(h)
        print(canonical_json(h.__dict__) if args.json else f"Registered hypothesis '{h.hypothesis_id}'")
        return 0
    cmds = {"status": cmd_status, "check-worktree": cmd_check_worktree, "verify": cmd_verify, "screen": cmd_screen, "backtest": cmd_backtest, "audit-check": cmd_audit_check, "handoff": cmd_handoff, "continue": cmd_continue, "qualify": cmd_qualify}
    fn = cmds.get(args.subcommand)
    return fn(args, runner) if fn else 0


if __name__ == "__main__":
    raise SystemExit(main())
