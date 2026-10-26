"""Durable trial ledger and reference effective-rank DSR; no harness dependencies."""

import hashlib
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, fields
from pathlib import Path

import numpy as np
import polars as pl
from ml4t.diagnostic.evaluation.stats import deflated_sharpe_ratio
from scipy import stats

from warden.contracts import (
    EvaluationContract,
    ExperimentResult,
    HypothesisSpec,
    canonical_json,
    valid_id,
)

BLOCKING = frozenset({"QUARANTINED_SYNTHETIC", "QUARANTINED", "REVOKED", "SUPERSEDED"})
MARKET = "verified_market"
LEGACY_RUNS = ("RUN-RULES-001", "RUN-RULES-002", "RUN-RULES-003", "RUN-ML-001", "RUN-ML-002")
LEGACY_REASON = "Synthetic benchmark evidence; not evaluated on verified exploratory market data"


class HypothesisBudgetExhaustedError(ValueError):
    pass


def compute_effective_trials_and_dsr(candidate_returns, historical_return_streams, bars_per_year, total_trials_n=None):
    """Candidate probability, noise SR, raw N, entropy rank with reject accounting."""
    streams = [np.asarray(s, dtype=float) for s in [*historical_return_streams, candidate_returns]]
    raw_stream_count = len(streams)
    n = int(total_trials_n if total_trials_n is not None else raw_stream_count)
    t = len(streams[-1])
    if any(s.ndim != 1 or len(s) != t or not np.isfinite(s).all() for s in streams):
        raise ValueError("Trial returns must be finite and temporally aligned")
    candidate = streams[-1]
    if t < 10 or np.std(candidate) < 1e-12:
        return 0.0, 0.0, n, 1.0
    k_eff, sr_std = 1.0, 1.0 / math.sqrt(t)
    if raw_stream_count > 1:
        matrix = np.column_stack(streams)
        centered = matrix - matrix.mean(axis=0)
        norm = np.linalg.norm(centered, axis=0)
        scaled = np.divide(centered, norm, out=np.zeros_like(centered), where=norm > 1e-12)
        corr = scaled.T @ scaled
        np.fill_diagonal(corr, 1.0)
        eigenvalues = np.maximum(np.linalg.eigvalsh(corr), 1e-12)
        p = eigenvalues / eigenvalues.sum()
        k_eff_base = float(np.clip(np.exp(-np.sum(p * np.log(p))), 1.0, raw_stream_count))
        missing_trials = max(0, n - raw_stream_count)
        k_eff = float(min(float(n), k_eff_base + missing_trials))

        sharpes = [float(np.mean(s) / (np.std(s, ddof=1) + 1e-12)) for s in streams]
        sr_std = max(float(np.std(sharpes, ddof=1)), sr_std)
        if np.all(norm > 1e-12):
            upstream = deflated_sharpe_ratio(matrix, periods_per_year=bars_per_year,
                                              correlation_method="effective_rank")
            if not np.isclose(upstream.n_trials_effective, k_eff_base, rtol=1e-6):
                raise ValueError("ML4T/reference effective-rank disagreement")
    else:
        missing_trials = max(0, n - 1)
        k_eff = float(min(float(n), 1.0 + missing_trials))

    sr = float(np.mean(candidate) / (np.std(candidate, ddof=1) + 1e-12))
    noise = 0.0
    if k_eff > 1.001:
        gamma = 0.5772156649
        noise = sr_std * ((1 - gamma) * stats.norm.ppf(1 - 1 / max(k_eff, 1.05))
                          + gamma * stats.norm.ppf(1 - 1 / (max(k_eff, 1.05) * math.e)))
    variance = 1 - stats.skew(candidate) * sr + (stats.kurtosis(candidate, fisher=False) - 1) / 4 * sr**2
    probability = float(stats.norm.cdf((sr - noise) / math.sqrt(max(variance, 1e-8) / (t - 1))))
    return probability, noise * math.sqrt(bars_per_year), n, k_eff


def _columns(cls):
    return ",".join(f"{f.name} {'TEXT' if 'str' in str(f.type) or 'datetime' in str(f.type) else 'REAL'}"
                    for f in fields(cls))


def _ensure_columns(conn, table, cls):
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for f in fields(cls):
        if f.name not in existing:
            kind = "TEXT" if ("str" in str(f.type) or "datetime" in str(f.type)) else "REAL"
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {f.name} {kind}")


def _insert(conn, table, values):
    conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                 tuple(values.values()))


class ResearchRegistry:
    def __init__(self, db_path=Path("registry/registry.db")):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS contracts ({_columns(EvaluationContract)},spec_hash TEXT NOT NULL,PRIMARY KEY (contract_id));
                CREATE TABLE IF NOT EXISTS hypotheses ({_columns(HypothesisSpec)},status TEXT NOT NULL DEFAULT 'ACTIVE',trials_used INTEGER NOT NULL DEFAULT 0,created_at REAL,attempts_json TEXT NOT NULL DEFAULT '{{}}',PRIMARY KEY (hypothesis_id));
                CREATE TABLE IF NOT EXISTS trials ({_columns(ExperimentResult)},config_json TEXT,returns_json TEXT,returns_parquet_path TEXT,trial_number_in_hyp INTEGER,git_commit_sha TEXT,created_at REAL,PRIMARY KEY (run_id));
                CREATE TABLE IF NOT EXISTS feature_sets (feature_hash TEXT PRIMARY KEY,contract_id TEXT,feature_names_json TEXT,ml4t_engineer_version TEXT,prefix_invariance_passed INTEGER,parquet_cache_path TEXT,created_at REAL);
                CREATE TABLE IF NOT EXISTS qualifications (qual_id TEXT PRIMARY KEY,run_id TEXT,contract_id TEXT,hypothesis_id TEXT,lineage_id TEXT UNIQUE,holdout_gross_sharpe REAL,holdout_net_sharpe REAL,holdout_max_dd_pct REAL,val_to_holdout_sharpe_ratio REAL,approved_by_human TEXT,qualified_at REAL);
                CREATE TABLE IF NOT EXISTS trial_quarantine (run_id TEXT PRIMARY KEY,reason TEXT,quarantined_at REAL);
                CREATE TABLE IF NOT EXISTS audit_reports (run_id TEXT PRIMARY KEY,contract_id TEXT,verdict TEXT,checks_json TEXT,created_at REAL);
                CREATE TABLE IF NOT EXISTS trial_dispositions (disposition_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,disposition TEXT NOT NULL,reason TEXT NOT NULL,attributed_to TEXT NOT NULL,created_at REAL NOT NULL,contract_id TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS audit_evidence (audit_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,contract_id TEXT DEFAULT '',verdict TEXT NOT NULL,checks_json TEXT NOT NULL DEFAULT '{{}}',reason TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL);
                CREATE TRIGGER IF NOT EXISTS trials_no_update BEFORE UPDATE ON trials BEGIN SELECT RAISE(ABORT,'Append-only violation: trials table is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS trials_no_delete BEFORE DELETE ON trials BEGIN SELECT RAISE(ABORT,'Append-only violation: trials table is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS audit_reports_no_update BEFORE UPDATE ON audit_reports BEGIN SELECT RAISE(ABORT,'Append-only violation: audit evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS audit_reports_no_delete BEFORE DELETE ON audit_reports BEGIN SELECT RAISE(ABORT,'Append-only violation: audit evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS audit_evidence_no_update BEFORE UPDATE ON audit_evidence BEGIN SELECT RAISE(ABORT,'Append-only violation: audit evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS audit_evidence_no_delete BEFORE DELETE ON audit_evidence BEGIN SELECT RAISE(ABORT,'Append-only violation: audit evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS dispositions_no_update BEFORE UPDATE ON trial_dispositions BEGIN SELECT RAISE(ABORT,'Append-only violation: dispositions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS dispositions_no_delete BEFORE DELETE ON trial_dispositions BEGIN SELECT RAISE(ABORT,'Append-only violation: dispositions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS hypotheses_no_budget_reset BEFORE UPDATE ON hypotheses
                BEGIN
                    SELECT RAISE(ABORT, 'Governance violation: trials_used cannot be decreased')
                    WHERE NEW.trials_used < OLD.trials_used;
                    SELECT RAISE(ABORT, 'Governance violation: rejected hypothesis cannot be reactivated')
                    WHERE OLD.status = 'REJECTED' AND NEW.status != 'REJECTED';
                END;
            """)
            for table, cls in (("contracts", EvaluationContract), ("hypotheses", HypothesisSpec),
                               ("trials", ExperimentResult)):
                _ensure_columns(conn, table, cls)
            for r_id in LEGACY_RUNS:
                conn.execute("INSERT OR IGNORE INTO trial_quarantine (run_id, reason, quarantined_at) VALUES (?, ?, ?)",
                             (r_id, LEGACY_REASON, time.time()))
            # Ticket 03: attributed, append-only superseding decision for the required regression case.
            legacy = conn.execute("SELECT contract_id, created_at FROM trials WHERE run_id='RUN-ML-001'").fetchone()
            if legacy:
                q = conn.execute("SELECT quarantined_at FROM trial_quarantine WHERE run_id='RUN-ML-001'").fetchone()
                self._append_disposition(
                    conn, "RUN-ML-001", "QUARANTINED_SYNTHETIC",
                    "Legacy trial recorded synthetic=0 with an empty data_snapshot_sha256, but the registry "
                    "quarantines it as synthetic benchmark evidence; a matching digest cannot prove market origin.",
                    "warden:migration:issue-4-03", legacy[0], (q[0] if q else legacy[1]) or time.time())
    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                conn.execute("PRAGMA journal_mode=MEMORY")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    # --- dispositions, audit evidence, effective eligibility --------------

    @staticmethod
    def _append_disposition(conn, run_id, disposition, reason, attributed_to, contract_id="", created_at=None):
        did = "DISP-" + hashlib.sha256(canonical_json([run_id, disposition, reason, attributed_to]).encode()).hexdigest()[:24]
        conn.execute("INSERT OR IGNORE INTO trial_dispositions VALUES (?,?,?,?,?,?,?)",
                     (did, run_id, disposition, reason, attributed_to, float(created_at or time.time()), contract_id))
        return did

    def record_disposition(self, run_id, disposition, reason, attributed_to):
        """Append a superseding/quarantine decision; original trial bytes never change."""
        if disposition not in BLOCKING and disposition != "NOTE":
            raise ValueError(f"unknown disposition '{disposition}'")
        if len(reason.strip()) < 20 or not attributed_to.strip():
            raise ValueError("a disposition needs a substantive reason and an attribution")
        with self._connect() as conn:
            row = conn.execute("SELECT contract_id FROM trials WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise ValueError(f"Run ID '{run_id}' not found in registry.")
            return self._append_disposition(conn, run_id, disposition, reason.strip(), attributed_to.strip(), row[0])

    def effective_eligibility(self, run_id: str) -> dict:
        """One promotion-eligibility decision, independent of historical verdict fields."""
        with self._connect() as conn:
            t = conn.execute("SELECT * FROM trials WHERE run_id=?", (run_id,)).fetchone()
            if not t:
                return {"run_id": run_id, "eligible": False, "reason": "unknown run_id",
                        "historical_verdict": None, "data_provenance": "unknown", "dispositions": []}
            trial = dict(t)
            dispositions = [dict(r) for r in conn.execute(
                "SELECT disposition,reason,attributed_to,created_at FROM trial_dispositions WHERE run_id=? ORDER BY created_at",
                (run_id,))]
            quarantine = [dict(r) for r in conn.execute(
                "SELECT reason,quarantined_at FROM trial_quarantine WHERE run_id=?", (run_id,))]
            audit = conn.execute("SELECT verdict FROM audit_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                                 (run_id,)).fetchone()
        blocking = [d["disposition"] for d in dispositions if d["disposition"] in BLOCKING]
        if quarantine:
            blocking.append("QUARANTINED")
        provenance = trial.get("data_provenance") or "unknown"
        if provenance == "unknown" and trial.get("synthetic") in (1, 1.0, True):
            provenance = "synthetic"
        checks = [
            (not blocking, f"blocking disposition(s): {', '.join(sorted(set(blocking)))}" if blocking else ""),
            (provenance == MARKET, f"provenance '{provenance}' is not verified market data"),
            (bool(trial.get("data_snapshot_sha256")), "trial has no pinned dataset digest"),
            (trial.get("verdict") == "CANDIDATE_FOR_QUALIFICATION",
             f"historical verdict {trial.get('verdict')} is not candidate-for-qualification"),
        ]
        reason = next((r for ok, r in checks if not ok), "verified market candidate evidence")
        return {"run_id": run_id, "eligible": all(ok for ok, _ in checks), "reason": reason,
                "historical_verdict": trial.get("verdict"),
                "historical_audit_verdict": audit[0] if audit else None,
                "data_provenance": provenance, "contract_id": trial.get("contract_id"),
                "dispositions": dispositions}

    def record_audit(self, run_id, contract_id, verdict, checks, reason=""):
        payload = canonical_json([run_id, verdict, checks, reason])
        aid = "AUD-" + hashlib.sha256(payload.encode()).hexdigest()[:24]
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO audit_evidence VALUES (?,?,?,?,?,?,?)",
                         (aid, run_id, contract_id, verdict, canonical_json(checks), reason, time.time()))
        return aid

    def register_contract(self, contract):
        with self._connect() as conn:
            row = conn.execute("SELECT spec_hash FROM contracts WHERE contract_id=?", (contract.contract_id,)).fetchone()
            if row:
                if row[0] != contract.spec_hash():
                    raise ValueError("Registered contract is immutable; choose a new contract_id")
                return
            _insert(conn, "contracts", {**json.loads(canonical_json(asdict(contract))), "spec_hash": contract.spec_hash()})

    def register_hypothesis(self, hyp):
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM contracts WHERE contract_id=?", (hyp.contract_id,)).fetchone():
                raise ValueError("Register the contract first")
            existing = conn.execute("SELECT * FROM hypotheses WHERE hypothesis_id=?", (hyp.hypothesis_id,)).fetchone()
            if existing:
                if any(existing[k] != v for k, v in asdict(hyp).items()):
                    raise ValueError("Registered hypothesis specification is immutable")
                return
            if hyp.parent_hypothesis_id and not conn.execute("SELECT 1 FROM hypotheses WHERE hypothesis_id=?", (hyp.parent_hypothesis_id,)).fetchone():
                raise ValueError("Register parent hypothesis first")
            _insert(conn, "hypotheses", {**asdict(hyp), "created_at": time.time()})

    def check_hypothesis_budget(self, hypothesis_id):
        with self._connect() as conn:
            row = conn.execute("SELECT trials_used,status FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown hypothesis")
            exhausted = row[0] >= 3 or row[1] != "ACTIVE"
            if row[0] >= 3:
                conn.execute("UPDATE hypotheses SET status='REJECTED' WHERE hypothesis_id=? AND status='ACTIVE'", (hypothesis_id,))
        if exhausted:
            raise HypothesisBudgetExhaustedError(hypothesis_id)

    def register_trial_start(self, hypothesis_id, run_id):
        valid_id(run_id)
        self.check_hypothesis_budget(hypothesis_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT trials_used,status,attempts_json FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
            if row[0] >= 3 or row[1] != "ACTIVE":
                raise HypothesisBudgetExhaustedError(hypothesis_id)
            attempts = json.loads(row[2])
            if run_id in attempts or conn.execute("SELECT 1 FROM trials WHERE run_id=?", (run_id,)).fetchone():
                raise ValueError("Duplicate run_id")
            attempts[run_id] = row[0] + 1
            conn.execute("UPDATE hypotheses SET trials_used=trials_used+1,attempts_json=? WHERE hypothesis_id=?", (canonical_json(attempts), hypothesis_id))
            return row[0] + 1

    def load_prior_trial_returns(self, contract_id, expected_len):
        with self._connect() as conn:
            rows = conn.execute("SELECT returns_json, stage_reached FROM trials WHERE contract_id=? ORDER BY created_at,run_id", (contract_id,)).fetchall()
        streams = []
        for r_json, stage in rows:
            if stage == "STAGE_1_SIGNAL" or not r_json or r_json == "[]":
                continue
            arr = np.array(json.loads(r_json), dtype=float)
            if float(np.std(arr)) < 1e-12:
                continue
            if len(arr) != expected_len:
                raise ValueError("Contract-wide trial histories are not aligned")
            streams.append(arr)
        return streams
    def record_trial(self, result, config_dict, net_returns, trial_number=None):
        valid_id(result.run_id)
        valid_id(result.contract_id)
        returns = np.asarray(net_returns, dtype=float)
        if returns.ndim != 1 or not np.isfinite(returns).all():
            raise ValueError("Returns must be a finite vector")
        if trial_number is None:
            trial_number = self.register_trial_start(result.hypothesis_id, result.run_id)
        path = self.db_path.parent / "returns" / result.contract_id / f"{result.run_id}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            row = conn.execute("SELECT contract_id,attempts_json FROM hypotheses WHERE hypothesis_id=?", (result.hypothesis_id,)).fetchone()
            if row is None or row[0] != result.contract_id or json.loads(row[1]).get(result.run_id) != trial_number:
                raise ValueError("Missing or mismatched trial reservation")
            with path.open("xb") as handle:
                pl.DataFrame({"net_return": returns}).write_parquet(handle)
            try:
                returns_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
                trial_dict = {**result.to_dict(), "config_json": canonical_json(config_dict),
                              "returns_json": canonical_json(returns.tolist()), "returns_parquet_path": str(path),
                              "returns_sha256": returns_sha256,
                              "trial_number_in_hyp": trial_number, "git_commit_sha": config_dict.get("git_commit_sha", ""), "created_at": time.time()}
                _insert(conn, "trials", trial_dict)
            except Exception:
                path.unlink()
                raise
