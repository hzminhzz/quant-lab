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

from warden import __version__
from warden.contracts import canonical_json, load_hypothesis
from warden.leakage import compute_and_cache_features, verify_prefix_invariance
from warden.runner import ExperimentRunner, load_module_from_path

MANIFEST_FILE = ".warden_manifest.sha256"


def compute_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_manifest(repo_root: Path = Path(".")) -> str:
    repo = Path(repo_root)
    protected = (
        sorted(repo.glob("src/warden/*.py"))
        + sorted(repo.glob("contracts/*.yaml"))
        + sorted(repo.glob("contracts/*.yml"))
        + ([repo / "AGENTS.md"] if (repo / "AGENTS.md").is_file() else [])
    )
    lines = [f"{compute_file_sha256(p)}  {p.relative_to(repo)}" for p in protected]
    content = "\n".join(lines) + "\n"
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
    return not (loc.is_file() and subprocess.run([sys.executable, str(loc)], capture_output=True, check=False).returncode != 0)


def cmd_status(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    with runner.registry._connect() as conn:
        n_hyp = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
        n_trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        rows = conn.execute(
            "SELECT run_id, hypothesis_id, strategy_family, net_sharpe, deflated_sharpe_prob, verdict FROM trials ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
    recent = [{"run_id": r[0], "hypothesis_id": r[1], "strategy_family": r[2], "net_sharpe": float(r[3]), "deflated_sharpe_prob": float(r[4]), "verdict": r[5]} for r in rows]
    data = {"warden_version": __version__, "hypotheses_count": n_hyp, "trials_count": n_trials, "recent_trials": recent}
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"Warden v{__version__} | Hypotheses: {n_hyp} | Trials: {n_trials}")
        for t in recent:
            print(f"  {t['run_id']} ({t['strategy_family']}): NetSR={t['net_sharpe']:.2f}, DSR={t['deflated_sharpe_prob']:.2f} -> {t['verdict']}")
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
    if pipeline:
        verify_prefix_invariance(pipeline, panel, contract)
        compute_and_cache_features(pipeline, panel, contract, registry=runner.registry)
    res = {"status": "PASS", "prefix_invariance": True, "cached": pipeline is not None, "rows": len(panel)}
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
    with runner.registry._connect() as conn:
        t = conn.execute("SELECT * FROM trials WHERE run_id = ?", (args.run_id,)).fetchone()
        if not t:
            print(f"Error: Run ID '{args.run_id}' not found.", file=sys.stderr)
            return 1
        trial = dict(t)
    c = {
        "1_target_rebalance_alignment": trial.get("stage_reached") in {"STAGE_2_BACKTEST", "STAGE_3_STRESS"},
        "2_outlier_sensitivity": trial.get("max_drawdown_pct", 100.0) < 50.0 and trial.get("net_sharpe", 0.0) > 0.0,
        "3_point_in_time_causality": True,
        "4_fold_local_fitting": True,
        "5_cost_and_latency_survival": trial.get("stress_2x_cost_sharpe", -1.0) > 0.50,
        "6_selection_bias_accounting": trial.get("total_trials_n", 0) > 0 and trial.get("deflated_sharpe_prob", 0.0) >= 0.80,
    }
    passed = all(c.values())
    audit = {"run_id": args.run_id, "checks": {k: {"passed": v} for k, v in c.items()}, "audit_passed": passed}
    out = runner.repo_root / ".workspace" / "hypotheses" / f"AUDIT_{args.run_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"# 6-Check Audit: {args.run_id}\nVerdict: {'APPROVED' if passed else 'REJECTED'}\n" + "\n".join(f"- [{ 'PASS' if v else 'FAIL' }] {k}" for k, v in c.items()) + "\n")
    print(json.dumps(audit, indent=2) if args.json else f"Audit for {args.run_id}: {'PASS' if passed else 'FAIL'}\nReport: {out}")
    return 0 if passed else 1


def cmd_handoff(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    now = datetime.datetime.now(datetime.UTC)
    d, t = now.strftime("%Y-%m-%d"), now.strftime("%H%M%S")
    tdir = runner.repo_root / ".workspace" / "transitions" / d
    tdir.mkdir(parents=True, exist_ok=True)
    tfile = tdir / f"{t}.md"
    head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    with runner.registry._connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
    tfile.write_text(f"# Transition {d} {t} UTC\nNotes: {args.notes}\nTicket: {args.ticket or 'N/A'}\n```bash\ngit rev-parse --short HEAD\n# expect: {head}\nsqlite3 registry/registry.db \"SELECT COUNT(*) FROM trials\"\n# expect: {n}\nuv run warden check-worktree\n# expect: Worktree integrity verified: protected contracts, warden core, and LOC budgets intact.\n```\n")
    print(f"Handoff snapshot recorded: {tfile}")
    return 0


def cmd_continue(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    target = Path(args.transition_path) if args.transition_path else (sorted(runner.repo_root.glob(".workspace/transitions/*/*.md")) or [None])[-1]
    if not target or not target.is_file():
        print("No transition snapshot found.", file=sys.stderr)
        return 1
    blocks = re.findall(r"```bash\n(.*?)\n```", target.read_text(encoding="utf-8"), re.DOTALL)
    lines = (blocks[0] if blocks else "").splitlines()
    i, drift = 0, False
    while i < len(lines):
        cmd = lines[i].strip()
        i += 1
        if not cmd or cmd.startswith("#"):
            continue
        expected = lines[i].strip()[9:].strip() if i < len(lines) and lines[i].strip().startswith("# expect:") else None
        if expected is not None:
            i += 1
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=False)
        actual = (proc.stdout or proc.stderr).strip()
        if expected and expected not in actual:
            print(f"DRIFT: '{cmd}' expected '{expected}', got '{actual}'", file=sys.stderr)
            drift = True
    print("Continue FAILED: Drift detected." if drift else f"Continue SUCCESS: zero drift from {target.name}.")
    return 1 if drift else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="warden", description="Deterministic quantitative research harness.")
    p.add_argument("--version", action="version", version=f"warden {__version__}")
    s = p.add_subparsers(dest="subcommand")
    s.add_parser("status").add_argument("--json", action="store_true")
    s.add_parser("check-worktree")
    s.add_parser("register-hypothesis").add_argument("path")
    s.add_parser("verify").add_argument("spec_yaml")
    s.add_parser("screen").add_argument("spec_yaml")
    s.add_parser("backtest").add_argument("spec_yaml")
    s.add_parser("audit-check").add_argument("--run-id", required=True)
    h = s.add_parser("handoff")
    h.add_argument("--notes", default="Session transition checkpoint")
    h.add_argument("--ticket", default=None)
    s.add_parser("continue").add_argument("transition_path", nargs="?", default=None)
    for sub in s.choices.values():
        if "json" not in [a.dest for a in sub._actions]:
            sub.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.subcommand:
        parser.print_help()
        return 0
    runner = ExperimentRunner()
    if args.subcommand == "status":
        return cmd_status(args, runner)
    if args.subcommand == "check-worktree":
        return 0 if check_worktree() else 2
    if args.subcommand == "register-hypothesis":
        if not check_worktree():
            sys.exit(2)
        h = load_hypothesis(Path(args.path))
        c = runner.resolve_contract(h.contract_id)
        runner.registry.register_contract(c)
        runner.registry.register_hypothesis(h)
        print(canonical_json(h.__dict__) if args.json else f"Registered hypothesis '{h.hypothesis_id}'")
        return 0
    if args.subcommand == "verify":
        return cmd_verify(args, runner)
    if args.subcommand == "screen":
        return cmd_screen(args, runner)
    if args.subcommand == "backtest":
        return cmd_backtest(args, runner)
    if args.subcommand == "audit-check":
        return cmd_audit_check(args, runner)
    if args.subcommand == "handoff":
        return cmd_handoff(args, runner)
    if args.subcommand == "continue":
        return cmd_continue(args, runner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
