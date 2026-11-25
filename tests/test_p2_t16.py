"""Issue #102: reporting, preflight, campaign and calibration read the ledger read-only.

Three gates, in the order they fail:

1. **Static (alias-aware AST):** no module under ``scripts/`` may open SQLite itself,
   except the two utilities whose job *is* a byte-for-byte copy (``registry_evidence_snapshot``
   and the migration rehearsal) and the backup-API snapshot inside ``preflight_trial``. Aliases
   (``import sqlite3 as x``, ``from sqlite3 import connect as c``, ``getattr(sqlite3, "connect")``)
   are resolved, so renaming the import does not launder a direct connection.
2. **Read-only helper:** the projections in ``warden_ops.readonly`` neither change the ledger
   bytes nor its row counts — the reporting path cannot mutate scientific state.
3. **Fixture parity:** every migrated consumer renders byte-identical output against a
   synthetic registry. The goldens in ``tests/fixtures/p2_t16`` were captured from the
   pre-migration scripts (``P2_T16_SCRIPTS_DIR`` point at an exported copy of the originals,
   ``P2_T16_WRITE_GOLDENS=1`` rewrites them); this test proves the migration changed no byte
   a human reads.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

from warden.contracts import HypothesisSpec, load_contract
from warden.registry import ResearchRegistry

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get("P2_T16_SCRIPTS_DIR", REPO / "scripts"))
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "p2_t16"
WRITE_GOLDENS = os.environ.get("P2_T16_WRITE_GOLDENS") == "1"

# The only direct connections the ticket allows, and only inside these callables.
DIRECT_SQLITE_ALLOWED = {
    # A WAL snapshot is a byte copy: it needs a real source/target connection pair.
    "registry_evidence_snapshot.py": {"backup_readonly"},
    # Rehearsal migrates an isolated copy and re-reads it to prove the original is untouched.
    "rehearse_state_machine_migration.py": {"rehearse", "_ro_present_tables", "_read_state"},
    # `preflight` runs a spec against a throwaway snapshot, so the snapshot must be a copy.
    "preflight_trial.py": {"snapshot"},
}


def _sqlite_names(tree: ast.AST) -> tuple[set[str], set[str], list[str]]:
    """Aliases for the sqlite3 module, names bound to ``connect``, and dynamic-library names."""
    modules = {"sqlite3"}
    connect_names: set[str] = set()
    dynamic: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3":
                    modules.add(alias.asname or "sqlite3")
                elif alias.name.startswith("sqlite3.") and alias.name == "sqlite3.connect":
                    connect_names.add(alias.asname or "connect")
        elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            for alias in node.names:
                if alias.name == "connect":
                    connect_names.add(alias.asname or "connect")
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            # `importlib.import_module("sqlite3")` / `__import__("sqlite3")`
            func = node.value.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"import_module", "__import__"} and node.value.args:
                arg = node.value.args[0]
                if isinstance(arg, ast.Constant) and arg.value == "sqlite3":
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            modules.add(target.id)
    return modules, connect_names, dynamic


def _direct_connections(path: Path) -> list[tuple[str, int]]:
    """(enclosing callable, lineno) for every direct sqlite3 connect in one script."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules, connect_names, _ = _sqlite_names(tree)
    hits: list[tuple[str, int]] = []
    stack: list[str] = ["<module>"]

    class Visitor(ast.NodeVisitor):
        def _enter(self, node) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_FunctionDef(self, node) -> None:
            self._enter(node)

        def visit_AsyncFunctionDef(self, node) -> None:
            self._enter(node)

        def visit_Call(self, node) -> None:
            func = node.func
            direct = False
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id in modules and func.attr == "connect"):
                direct = True
            if isinstance(func, ast.Name) and func.id in connect_names:
                direct = True
            if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
                target, attr = node.args[0], node.args[1]
                if (isinstance(target, ast.Name) and target.id in modules
                        and isinstance(attr, ast.Constant) and attr.value == "connect"):
                    direct = True
            if direct:
                hits.append((stack[-1], node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)
    return hits


def test_no_script_opens_sqlite_outside_the_backup_and_migration_utilities() -> None:
    offenders, allowed = [], set()
    for path in sorted(SCRIPTS.glob("*.py")):
        for where, lineno in _direct_connections(path):
            if where in DIRECT_SQLITE_ALLOWED.get(path.name, set()):
                allowed.add(path.name)
            else:
                offenders.append(f"{path.name}:{lineno} in {where}()")
    assert offenders == [], (
        "reporting/preflight scripts must read the ledger through warden_ops.readonly; "
        f"direct sqlite3 connections found: {offenders}")
    # The allowlist must not rot: each entry has to still guard a real connection.
    assert allowed == set(DIRECT_SQLITE_ALLOWED)


def test_the_ast_guard_resolves_aliases() -> None:
    source = (
        "import sqlite3 as s\n"
        "from sqlite3 import connect as c\n"
        "def one():\n    return s.connect('x')\n"
        "def two():\n    return c('x')\n"
        "def three():\n    return getattr(s, 'connect')('x')\n"
        "def four():\n    return 1\n"
    )
    tmp = REPO / "tests" / "_p2_t16_alias_probe.py"
    tmp.write_text(source, encoding="utf-8")
    try:
        found = {(where, line) for where, line in _direct_connections(tmp)}
    finally:
        tmp.unlink()
    assert found == {("one", 4), ("two", 6), ("three", 8)}


# --- fixtures ------------------------------------------------------------

CONTRACT_ID = "CRYPTO_PERPS_1H_V1"


def _contract_registry(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "registry.db"
    reg = ResearchRegistry(db)
    contract = load_contract(REPO / "contracts" / "crypto_perps_1h_v1.yaml")
    reg.register_contract(contract)
    for hid in ("HYP-PASS", "HYP-REJECT", "HYP-INTERRUPTED", "HYP-PENDING"):
        reg.register_hypothesis(HypothesisSpec(
            hid, contract.contract_id, "Campaign", "rules_ts",
            "A causal mechanism whose effect may cross many trading days",
            "Fees, volatility and slippage may erase the entire signal", 8,
            "Reject when the strategy has insufficient executable edge",
        ))
    reg.record_selection_attempt(hypothesis_id="HYP-PASS", contract_id=CONTRACT_ID,
                                 kind="standalone_screen", stage="STAGE_1_SIGNAL", outcome="PASS",
                                 strategy_module="strategies/candidates/vol_breakout_1h.py")
    reg.record_selection_attempt(hypothesis_id="HYP-REJECT", contract_id=CONTRACT_ID,
                                 kind="standalone_screen", stage="STAGE_1_SIGNAL", outcome="REJECT",
                                 strategy_module="strategies/candidates/vol_breakout_1h.py")
    reg.register_trial_start("HYP-INTERRUPTED", "RUN-INTERRUPTED")
    reg.reconcile_attempt("RUN-INTERRUPTED", confirm_interrupted=True)
    return db


def _campaign_specs(tmp_path: Path) -> list[Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    out = []
    for hid in ("HYP-PASS", "HYP-REJECT", "HYP-PENDING"):
        path = tmp_path / f"{hid}.yaml"
        path.write_text(yaml.safe_dump({
            "hypothesis_id": hid, "contract_id": CONTRACT_ID,
            "strategy_module": "strategies/candidates/vol_breakout_1h.py"}), encoding="utf-8")
        out.append(path)
    return out


CLOSURE_SCHEMA = """
CREATE TABLE hypotheses (hypothesis_id TEXT, strategy_module TEXT);
CREATE TABLE trials (hypothesis_id TEXT, run_id TEXT, net_sharpe REAL,
    stress_2x_cost_sharpe REAL, delay_plus1_sharpe REAL, annualized_turnover REAL,
    breakeven_cost_bps REAL, max_drawdown_pct REAL, param_neighbor_stability REAL,
    deflated_sharpe_prob REAL, verdict TEXT, created_at REAL, stress_1p5_cost_sharpe REAL);
CREATE TABLE selection_attempts (hypothesis_id TEXT, kind TEXT, outcome TEXT,
    diagnostics_json TEXT);
CREATE TABLE audit_evidence (run_id TEXT, verdict TEXT);
"""


def _closure_fixture(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "closure.db"
    con = sqlite3.connect(db)
    con.executescript(CLOSURE_SCHEMA)
    for i in range(94, 112):
        con.execute("INSERT INTO hypotheses VALUES (?,?)",
                    (f"HYP-2026-{i:03d}", f"strategies/candidates/s{i:02d}.py"))
    con.execute("INSERT INTO hypotheses VALUES ('HYP-2026-112','strategies/candidates/s02.py')")
    for i in range(120, 125):
        con.execute("INSERT INTO hypotheses VALUES (?,?)",
                    (f"HYP-2026-{i}", f"strategies/candidates/carver_daily/s{i - 119:02d}_x.py"))
    # HYP-2026-095: a Stage-3 REVISE of its own, with an audit row (audit verdict rendering).
    con.execute("INSERT INTO trials VALUES ('HYP-2026-095','RUN-X-1',1.3,1.3,1.3,0.4,100,8.0,1.0,0.4,'REVISE',1,NULL)")
    con.execute("INSERT INTO audit_evidence VALUES ('RUN-X-1','REJECTED')")
    for hid in (112, 113, 114):
        con.execute("INSERT INTO trials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"HYP-2026-{hid}", f"RUN-Y-{hid}", 1.2, 1.2, 1.2, 1.0, 200, 10.0, 0.95, 0.97,
                     "CANDIDATE_FOR_QUALIFICATION", 1, 1.19))
        con.execute("INSERT INTO audit_evidence VALUES (?, 'APPROVED')", (f"RUN-Y-{hid}",))
    con.execute("INSERT INTO selection_attempts VALUES ('HYP-2026-100','standalone_screen','REJECT',?)",
                (json.dumps({"profit_factor": 1.03, "bootstrap_p_value": 0.31}),))
    con.execute("INSERT INTO selection_attempts VALUES ('HYP-2026-101','standalone_screen','PASS',?)",
                (json.dumps({"profit_factor": 1.20, "bootstrap_p_value": 0.01}),))
    for src, pf, p in ((95, 1.258, 0.0), (96, 1.252, 0.0), (97, 1.213, 0.01),
                       (98, 1.213, 0.01), (99, 1.154, 0.045)):
        con.execute("INSERT INTO selection_attempts VALUES (?, 'standalone_screen', 'PASS', ?)",
                    (f"HYP-2026-{src:03d}", json.dumps({"profit_factor": pf, "bootstrap_p_value": p})))
    con.commit()
    con.close()
    return db


def _decisions_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "registry.db"
    ResearchRegistry(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO contract_search_state VALUES (?,?,?,?)",
                ("MULTI_ASSET_1D_V1", 11.61, 1.057, 0.0))
    con.execute(
        "INSERT INTO hypotheses (hypothesis_id,contract_id,title,strategy_family,economic_mechanism,"
        "counterparty_or_friction,expected_holding_bars,falsification_criteria,trials_used,status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("HYP-2026-095", "MULTI_ASSET_1D_V1", "AF30 S01 Buy and Hold", "rules_ts", "mechanism",
         "friction", 312, "profit factor < 1.15", 1, "ACTIVE"))
    trial = {"run_id": "RUN-HYP095-001", "hypothesis_id": "HYP-2026-095",
             "contract_id": "MULTI_ASSET_1D_V1", "trial_number_in_hyp": 1, "net_sharpe": 1.317,
             "stress_2x_cost_sharpe": 1.317, "delay_plus1_sharpe": 1.317,
             "deflated_sharpe_prob": 0.663, "total_trials_n": 29, "effective_trials_keff": 11.61,
             "expected_max_sharpe_noise": 1.0567,
             "diagnostics_json": json.dumps({"selection": {"unvectorized_attempts": 17,
                                                           "prior_max_keff": 2.8345}}),
             "breakeven_cost_bps": 2728.6, "annualized_turnover": 0.39,
             "param_neighbor_stability": 1.0, "stage_reached": "STAGE_3_STRESS", "verdict": "REVISE"}
    con.execute(f"INSERT INTO trials ({','.join(trial)}) VALUES ({','.join('?' * len(trial))})",
                tuple(trial.values()))
    screen = {"attempt_id": "ATT-1", "attempt_base": "base-1", "hypothesis_id": "HYP-2026-095",
              "contract_id": "MULTI_ASSET_1D_V1", "kind": "standalone_screen",
              "stage": "STAGE_1_SIGNAL", "outcome": "REJECT", "created_at": 1.0,
              "diagnostics_json": json.dumps({"profit_factor": 1.03, "conditional_edge": 4.4e-05,
                                              "trade_edge": 1.5e-04, "time_in_market": 1.0,
                                              "bootstrap_p_value": 0.31, "avg_holding_bars": 35.1})}
    con.execute(f"INSERT INTO selection_attempts ({','.join(screen)}) VALUES ({','.join('?' * len(screen))})",
                tuple(screen.values()))
    con.commit()
    con.close()
    audits = tmp_path / "audits"
    audits.mkdir()
    (audits / "AUDIT_RUN-HYP095-001.md").write_text(
        "# 6-Check Audit: RUN-HYP095-001\nVerdict: REJECTED\n"
        "- [PASS] 1_target_rebalance_alignment\n- [FAIL] 2_outlier_sensitivity\n"
        "- [PASS] 5_cost_and_latency_survival\n- [FAIL] 6_selection_bias_accounting\n",
        encoding="utf-8")
    return db, audits, tmp_path / "revisions"


def _revision_fixture(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "registry.db"
    ResearchRegistry(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO hypotheses (hypothesis_id,contract_id,title,strategy_family,economic_mechanism,"
        "counterparty_or_friction,expected_holding_bars,falsification_criteria,trials_used,status) "
        "VALUES ('HYP-2026-099','MULTI_ASSET_1D_V1','S05','rules_ts','m','f',312,'c',1,'ACTIVE')")
    row = {"run_id": "RUN-HYP099-001", "hypothesis_id": "HYP-2026-099",
           "contract_id": "MULTI_ASSET_1D_V1", "trial_number_in_hyp": 1, "created_at": 1.0,
           "stage_reached": "STAGE_3_STRESS", "verdict": "REVISE", "net_sharpe": 0.8,
           "stress_2x_cost_sharpe": 0.40, "delay_plus1_sharpe": -0.10,
           "breakeven_cost_bps": 12.0, "param_neighbor_stability": 0.45,
           "diagnostics_json": json.dumps({"neighbor_tested": True})}
    con.execute(f"INSERT INTO trials ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                tuple(row.values()))
    for i, sharpe in enumerate((0.30, -0.10)):
        con.execute(
            "INSERT INTO selection_attempts (attempt_id,attempt_base,hypothesis_id,contract_id,"
            "kind,stage,outcome,created_at,diagnostics_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"ATT-G{i}", "base", "HYP-2026-099", "MULTI_ASSET_1D_V1", "param_grid",
             "STAGE_3_STRESS", "PASS", 1.5 + i * 0.01, json.dumps({"net_sharpe": sharpe})))
    con.commit()
    con.close()
    base = {"fast": 64, "slow": 256, "vol_span": 35, "vol_target": 0.16,
            "per_instrument_cap": 0.5, "warmup": 1}
    prereg = {"hypothesis_id": "HYP-2026-099", "contract_id": "MULTI_ASSET_1D_V1",
              "revisions": [
                  {"run_id": "RUN-HYP099-002", "axis": "2x_cost_survival",
                   "parameters": {**base, "fast": 62}, "rationale": "addresses 2x cost"},
                  {"run_id": "RUN-HYP099-003", "axis": "param_neighbour_stability",
                   "parameters": {**base, "fast": 66}, "rationale": "addresses stability"}]}
    path = tmp_path / "prereg.md"
    path.write_text("# prereg\n\n```yaml\n" + yaml.safe_dump(prereg) + "```\n", encoding="utf-8")
    return db, path


ROM_SCHEMA = """
CREATE TABLE hypotheses (hypothesis_id TEXT PRIMARY KEY, contract_id TEXT, title TEXT,
    status TEXT, strategy_module TEXT, parent_hypothesis_id TEXT);
CREATE TABLE trials (hypothesis_id TEXT, stage_reached TEXT, verdict TEXT, run_id TEXT);
CREATE TABLE selection_attempts (hypothesis_id TEXT, stage TEXT, outcome TEXT, attempt_id TEXT);
INSERT INTO hypotheses VALUES
    ('HYP-TEST-FAIL','CONTRACT-A','Rejected experiment','ACTIVE','strategies/candidates/failed.py',NULL),
    ('HYP-TEST-EARLY','CONTRACT-A','Rejected screen','ACTIVE','strategies/candidates/early.py',NULL),
    ('HYP-TEST-NEVER','CONTRACT-A','Unseen strategy','ACTIVE',NULL,NULL),
    ('HYP-TEST-PASS','CONTRACT-A','Predictive unqualified','ACTIVE',NULL,NULL);
INSERT INTO trials VALUES ('HYP-TEST-FAIL','STAGE_3_STRESS','REVISE','RUN-TEST-FAIL');
INSERT INTO selection_attempts VALUES
    ('HYP-TEST-EARLY','STAGE_1_SIGNAL','REJECT','ATT-TEST-EARLY'),
    ('HYP-TEST-PASS','STAGE_1_SIGNAL','PASS','ATT-TEST-PASS');
"""


def _rom_fixture(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "registry.db"
    with sqlite3.connect(db) as con:
        con.executescript(ROM_SCHEMA)
    return db


CALIBRATION_SCHEMA = """
CREATE TABLE contracts (contract_id TEXT PRIMARY KEY, asset_class TEXT, frequency TEXT,
    bars_per_year REAL, label_horizon_bars REAL);
CREATE TABLE trials (run_id TEXT PRIMARY KEY, hypothesis_id TEXT, contract_id TEXT,
    strategy_family TEXT, stage_reached TEXT, verdict TEXT, deflated_sharpe_prob REAL,
    expected_max_sharpe_noise REAL, total_trials_n REAL, effective_trials_keff REAL,
    ic_hac_tstat REAL, returns_parquet_path TEXT, returns_json TEXT, diagnostics_json TEXT,
    created_at REAL);
CREATE TABLE selection_attempts (attempt_id TEXT PRIMARY KEY, hypothesis_id TEXT,
    contract_id TEXT, strategy_family TEXT, kind TEXT, outcome TEXT, returns_observed INTEGER,
    returns_json TEXT, diagnostics_json TEXT, created_at REAL);
"""


def _calibration_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "cal"
    (root / "registry").mkdir(parents=True, exist_ok=True)
    (root / "returns").mkdir()
    db = root / "registry" / "registry.db"
    con = sqlite3.connect(db)
    con.executescript(CALIBRATION_SCHEMA)
    con.execute("INSERT INTO contracts VALUES ('C1','crypto_perps','1h',8760,1)")
    walk = [((i % 7) - 3) / 1000.0 for i in range(40)]
    for i, run_id in enumerate(("RUN-C1-001", "RUN-C1-002")):
        series = [x + i * 0.0012 for x in walk]
        con.execute("INSERT INTO trials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, "H1", "C1", "rules_ts", "STAGE_3_STRESS", "REVISE", 0.41 + i * 0.01,
                     1.10, 3.0, 2.5, 1.4, "", json.dumps(series), "{}", 1.0 + i))
    con.execute("INSERT INTO selection_attempts VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("ATT-G1", "H1", "C1", "rules_ts", "param_grid", "PASS", 1,
                 json.dumps([x * 0.5 for x in walk]), "{}", 0.5))
    con.commit()
    con.close()
    return db


def _report_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "registry").mkdir(parents=True, exist_ok=True)
    (root / ".workspace" / "af30_campaign").mkdir(parents=True, exist_ok=True)
    db = root / "registry" / "registry.db"
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE contract_search_state (contract_id TEXT PRIMARY KEY, max_keff_gate REAL,
        max_noise REAL, updated_at REAL);
    CREATE TABLE trials (run_id TEXT PRIMARY KEY, hypothesis_id TEXT, contract_id TEXT,
        strategy_family TEXT, strategy_module TEXT, config_json TEXT, net_sharpe REAL,
        gross_sharpe REAL, stress_2x_cost_sharpe REAL, verdict TEXT, stage_reached TEXT,
        deflated_sharpe_prob REAL, delay_plus1_sharpe REAL, annualized_turnover REAL,
        breakeven_cost_bps REAL, max_drawdown_pct REAL);
    """)
    con.execute("INSERT INTO contract_search_state VALUES ('MULTI_ASSET_1H_V1',22.5,1.224,0.0)")
    for i, run_id in enumerate(("RUN-HYP091-001", "RUN-HYP091-002")):
        con.execute("INSERT INTO trials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, "HYP-2026-091", "MULTI_ASSET_1H_V1", "rules_ts",
                     "strategies/candidates/carver_daily/af30_book.py",
                     json.dumps({"leverage": 1.0}), -0.495 - i * 0.01, 0.0953 - i * 0.01,
                     -1.0836 - i * 0.01, "REVISE", "STAGE_3_STRESS", 0.0033, -0.5185,
                     320.27, 1.02, 18.5 + i))
    con.commit()
    con.close()
    screens = [{"hypothesis_id": "HYP-2026-071", "strategy_name": "AF30_S01_SCREEN",
                "module": "strategies/candidates/carver_daily/ts_s01_buy_hold_single_1h.py",
                "profit_factor": 1.21, "conditional_edge": 0.00012, "trade_edge": 0.00031,
                "time_in_market": 0.92, "avg_holding_bars": 24.0, "verdict": "PASS"},
               {"hypothesis_id": "HYP-2026-072", "strategy_name": "AF30_S02_SCREEN",
                "module": "strategies/candidates/carver_daily/ts_s02_buy_hold_risk_scaled_1h.py",
                "profit_factor": None, "conditional_edge": None, "trade_edge": None,
                "time_in_market": None, "avg_holding_bars": None, "verdict": "REJECT"}]
    (root / ".workspace" / "af30_campaign" / "screens.json").write_text(
        json.dumps(screens), encoding="utf-8")
    return root


def _parity_trials_fixture(tmp_path: Path) -> Path:
    db = tmp_path / "registry" / "registry.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE trials (run_id TEXT PRIMARY KEY, hypothesis_id TEXT, contract_id TEXT,
        strategy_family TEXT, strategy_module TEXT, config_json TEXT, net_sharpe REAL,
        gross_sharpe REAL, stress_2x_cost_sharpe REAL, verdict TEXT);
    """)
    con.execute("INSERT INTO trials VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("RUN-MINI-001", "HYP-MINI-001", "C1", "rules_ts",
                 "strategies/candidates/mini.py", json.dumps({"leverage": 1.2}), 0.4, 0.6, 0.2,
                 "REVISE"))
    con.commit()
    con.close()
    return db


def _preflight_trial_fixture(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "registry.db"
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE trials (run_id TEXT, contract_id TEXT, returns_json TEXT);
    CREATE TABLE selection_attempts (contract_id TEXT, kind TEXT, returns_json TEXT);
    """)
    con.execute("INSERT INTO trials VALUES ('RUN-A','C1',?)", (json.dumps([0.1] * 12),))
    con.execute("INSERT INTO trials VALUES ('RUN-B','C1',?)", (json.dumps([0.2] * 12),))
    con.execute("INSERT INTO trials VALUES ('RUN-C','C1','[]')")
    con.execute("INSERT INTO trials VALUES ('RUN-D','C1',?)", (json.dumps([0.3] * 30),))
    con.execute("INSERT INTO selection_attempts VALUES ('C1','param_grid',?)",
                (json.dumps([0.4] * 12),))
    con.execute("INSERT INTO selection_attempts VALUES ('C1','standalone_screen',?)",
                (json.dumps([0.5] * 7),))
    con.commit()
    con.close()
    return db


# --- cases ---------------------------------------------------------------

def _load(script: str, at: Path | None = None):
    source = Path(at) if at is not None else SCRIPTS / script
    name = f"_p2_t16_{script.replace('.py', '')}"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _cases(tmp_path: Path) -> dict[str, str]:
    cases: dict[str, str] = {}

    projection = _load("campaign_projection.py")
    cases["campaign_projection.json"] = json.dumps(
        projection.project(_campaign_specs(tmp_path / "specs"), _contract_registry(tmp_path / "cp")),
        indent=2, sort_keys=True)

    af30 = _load("build_af30_step3_closure.py")
    fixture = _closure_fixture(tmp_path / "af30")
    cases["af30_step3_closure.md"] = af30.build(fixture)
    cases["af30_step3_closure_rendered.md"] = af30.render(
        "prologue\n" + af30.build(fixture) + "\nepilogue\n", af30.build(fixture))

    fx = _load("build_fx_stage3_closure.py")
    cases["fx_stage3_closure.md"] = fx.build(fixture, why="ExecutionDataError: no quotes")
    cases["fx_stage3_closure_unblocked.md"] = fx.build(fixture, why="")

    decisions = _load("build_carver_daily_decisions.py")
    db, audits, revisions = _decisions_fixture(tmp_path / "dec")
    cases["carver_daily_decisions.md"] = "\n".join(
        text for _path, text in decisions.collect(db, audits, revisions, ("HYP-2026-095",)))

    revision = _load("check_revision_prereg.py")
    rdb, prereg = _revision_fixture(tmp_path / "rev")
    base = {"fast": 64, "slow": 256, "vol_span": 35, "vol_target": 0.16,
            "per_instrument_cap": 0.5, "warmup": 1}
    probes = [
        {"hypothesis_id": "HYP-2026-099", "contract_id": "MULTI_ASSET_1D_V1",
         "run_id": "RUN-HYP099-002", "parameters": {**base, "fast": 62}},
        {"hypothesis_id": "HYP-2026-099", "contract_id": "MULTI_ASSET_1D_V1",
         "run_id": "RUN-HYP099-003", "parameters": {**base, "fast": 66}},
        {"hypothesis_id": "HYP-2026-099", "contract_id": "MULTI_ASSET_1D_V1",
         "run_id": "RUN-HYP099-001", "parameters": base},
    ]
    cases["revision_prereg.json"] = json.dumps(
        [revision._check_spec(revision.load_prereg(prereg), probe, rdb) for probe in probes],
        indent=2, sort_keys=True)

    rom = _load("research_outcome_memory.py")
    rom_db = _rom_fixture(tmp_path / "rom")
    cases["research_outcome_inventory.json"] = json.dumps(
        rom.inventory(rom_db, REPO / "docs/research-outcomes/branch-result-index-v1.json"),
        indent=2, sort_keys=True)

    calibration = _load("shadow_diagnostic_calibration.py")
    cal_db = _calibration_fixture(tmp_path / "calibration")
    cases["shadow_calibration.json"] = json.dumps(
        calibration.run_shadow_calibration(cal_db, cal_db.parent / "returns",
                                           pbo_blocks=4, ras_simulations=50),
        indent=2, sort_keys=True)

    preflight = _load("preflight_thesis.py")
    cp_db = _contract_registry(tmp_path / "cards")
    cases["preflight_thesis_cards.json"] = json.dumps(
        [preflight.hypothesis_card("HYP-PASS", cp_db),
         preflight.hypothesis_card("HYP-ABSENT", cp_db),
         preflight.hypothesis_card("HYP-PASS", tmp_path / "cards" / "missing.db")],
        indent=2, sort_keys=True)

    trial_preflight = _load("preflight_trial.py")
    pt_db = _preflight_trial_fixture(tmp_path / "ptrial")
    cases["preflight_trial_streams.json"] = json.dumps(
        {"C1": trial_preflight.prior_stream_lengths(pt_db, "C1"),
         "C2": trial_preflight.prior_stream_lengths(pt_db, "C2"),
         "excluded": trial_preflight.prior_stream_lengths(pt_db, "C1", "RUN-D")},
        indent=2, sort_keys=True)

    report_root = _report_fixture(tmp_path / "report")
    # `main()` derives the repo root from its own path, exactly as the CLI does, so the case
    # copies the script into the fixture tree instead of reaching into its internals.
    report_scripts = report_root / "scripts"
    report_scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPTS / "generate_campaign_report.py", report_scripts / "generate_campaign_report.py")
    report = _load("generate_campaign_report.py", at=report_scripts / "generate_campaign_report.py")
    report.main()
    cases["campaign_report.md"] = (
        report_root / ".workspace" / "af30_campaign" / "report.md").read_text(encoding="utf-8")

    parity = _load("shadow_engine_parity.py")
    parity_db = _parity_trials_fixture(tmp_path / "parity")
    cases["parity_trial_metadata.json"] = json.dumps(
        parity._load_trial_metadata(parity_db.parent.parent, "RUN-MINI-001"),
        indent=2, sort_keys=True)
    # Absolute pytest paths are the one thing that legitimately differs per run.
    return {name: text.replace(str(tmp_path), "<tmp>") for name, text in cases.items()}


def test_reporting_output_is_byte_identical_on_fixture_registries(tmp_path: Path) -> None:
    produced = _cases(tmp_path)
    if WRITE_GOLDENS:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        for name, text in produced.items():
            (GOLDEN / name).write_text(text, encoding="utf-8")
        pytest.skip(f"wrote {len(produced)} goldens for the pre-migration scripts")
    expected_names = sorted(path.name for path in GOLDEN.iterdir())
    assert expected_names == sorted(produced), (
        "golden set drifted; a case was added or removed without re-pinning")
    for name in expected_names:
        expected = (GOLDEN / name).read_text(encoding="utf-8")
        assert produced[name] == expected, f"{name} changed bytes; the migration altered output"


def test_readonly_projections_leave_the_ledger_untouched(tmp_path: Path) -> None:
    from warden_ops.readonly import ReadOnlyResearchRegistry

    db = _contract_registry(tmp_path / "pure")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    before_rows = _counts(db)
    ledger = ReadOnlyResearchRegistry(db)
    assert ledger.read_hypothesis("HYP-PASS")["contract_id"] == CONTRACT_ID
    ledger.read_hypothesis_inventory()
    ledger.read_trials()
    ledger.read_attempts()
    ledger.read_contracts()
    ledger.read_evidence_counts()
    ledger.read_contract_evidence_counts(CONTRACT_ID)
    ledger.read_hypothesis_evidence_counts(("HYP-PASS", "HYP-REJECT"), attempt_kind="param_grid")
    ledger.read_latest_trial("HYP-PASS", by_created_at=True)
    ledger.read_selection_attempts("HYP-PASS", CONTRACT_ID)
    ledger.read_standalone_screen("HYP-REJECT")
    ledger.read_param_grid_diagnostics("HYP-PASS")
    ledger.read_recorded_trials("HYP-PASS")
    ledger.read_recorded_screens("HYP-PASS")
    ledger.read_pending_attempts("HYP-INTERRUPTED", CONTRACT_ID)
    ledger.read_recorded_return_vectors(CONTRACT_ID)
    ledger.read_search_state(CONTRACT_ID)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert _counts(db) == before_rows


def _counts(db: Path) -> dict[str, int]:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        return {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("hypotheses", "contracts", "selection_attempts")}
