"""Durable trial ledger and reference effective-rank DSR; no harness dependencies."""

import hashlib
import json
import math
import os
import sqlite3
import tempfile
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


PER_HYPOTHESIS_CAP = 3
LINEAGE_CAP = 6            # principal-authorised hard cap: root + descendants + module aliases
CORR_DUPLICATE = 0.9995    # |corr| at or above this is the same trial, not a new one
UNVECTORIZED_PENALTY = 0.50


class HypothesisBudgetExhaustedError(ValueError):
    pass


class ScreenAlreadyAttemptedError(ValueError):
    """This exact validation-consuming screen was already admitted."""


class SelectionHistoryError(ValueError):
    """Selection history is not comparable; refusing to pad, slice or zero-fill it."""


def _eigen_rank(matrix: np.ndarray) -> float:
    centered = matrix - matrix.mean(axis=0)
    norm = np.linalg.norm(centered, axis=0)
    scaled = np.divide(centered, norm, out=np.zeros_like(centered), where=norm > 1e-12)
    corr = scaled.T @ scaled
    np.fill_diagonal(corr, 1.0)
    eigenvalues = np.maximum(np.linalg.eigvalsh(corr), 1e-12)
    p = eigenvalues / eigenvalues.sum()
    return float(np.clip(np.exp(-np.sum(p * np.log(p))), 1.0, matrix.shape[1]))


def distinct_stream_indices(matrix: np.ndarray, tol: float = CORR_DUPLICATE) -> list[int]:
    """Indices of distinct columns; |corr| >= tol against an earlier column is a duplicate."""
    keep: list[int] = []
    for j in range(matrix.shape[1]):
        col = matrix[:, j]
        if any(abs(float(np.corrcoef(col, matrix[:, k])[0, 1])) >= tol for k in keep):
            continue
        keep.append(j)
    return keep


def compute_selection_report(candidate_returns, historical_return_streams, bars_per_year: int,
                            raw_attempt_count: int | None = None, prior_max_keff: float = 1.0,
                            prior_max_noise: float = 0.0) -> dict:
    """Auditable selection hurdle: raw N, dedup effective rank, and the applied gate penalty."""
    streams = [np.asarray(v, dtype=float) for v in [*historical_return_streams, candidate_returns]]
    n = len(streams[-1])
    if any(v.ndim != 1 or len(v) != n or not np.isfinite(v).all() for v in streams):
        raise ValueError("Trial returns must be finite and temporally aligned")
    candidate = streams[-1]
    raw = int(raw_attempt_count if raw_attempt_count is not None else len(streams))
    unvectorized = max(0, raw - len(streams))
    floor = max(1.0, float(prior_max_keff))
    report = {"raw_attempt_count": raw, "streams_used": len(streams), "unvectorized_attempts": unvectorized,
              "prior_max_keff": float(prior_max_keff), "prior_max_noise": float(prior_max_noise),
              "k_eff_dedup": 1.0, "k_eff_gate": floor + UNVECTORIZED_PENALTY * unvectorized,
              "probability": 0.0, "expected_max_sharpe_noise": 0.0, "noise_floor_applied": False,
              "correction_policy": "dedup@0.9995 + max(prior floor) + 0.50/unvectorized",
              "non_promotable_reason": ""}
    if n < 10 or float(np.std(candidate)) < 1e-12:
        report["non_promotable_reason"] = "candidate return stream is empty or degenerate"
        return report
    matrix = np.column_stack(streams)
    k_dedup = _eigen_rank(matrix[:, distinct_stream_indices(matrix)])
    k_gate = max(k_dedup, floor) + UNVECTORIZED_PENALTY * unvectorized
    sharpes = [float(np.mean(v) / (np.std(v, ddof=1) + 1e-12)) for v in streams]
    sr_std = max(float(np.std(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0, 1.0 / math.sqrt(n))
    if matrix.shape[1] > 1:
        upstream = deflated_sharpe_ratio(matrix, periods_per_year=bars_per_year,
                                         correlation_method="effective_rank")
        if not np.isclose(upstream.n_trials_effective, _eigen_rank(matrix), rtol=1e-6):
            raise ValueError("ML4T/reference effective-rank disagreement")
    sr = float(np.mean(candidate) / (np.std(candidate, ddof=1) + 1e-12))
    noise = 0.0
    if k_gate > 1.001:
        gamma, kk = 0.5772156649, max(k_gate, 1.05)
        noise = sr_std * ((1 - gamma) * stats.norm.ppf(1 - 1 / kk)
                          + gamma * stats.norm.ppf(1 - 1 / (kk * math.e)))
    # The stored floor is annualised, so compare and re-apply it in annualised units.
    noise_ann = noise * math.sqrt(bars_per_year)
    if prior_max_noise > noise_ann:
        noise_ann, report["noise_floor_applied"] = float(prior_max_noise), True
    noise = noise_ann / math.sqrt(bars_per_year)
    variance = 1 - stats.skew(candidate) * sr + (stats.kurtosis(candidate, fisher=False) - 1) / 4 * sr ** 2
    probability = float(stats.norm.cdf((sr - noise) / math.sqrt(max(variance, 1e-8) / (n - 1))))
    report.update(probability=probability, expected_max_sharpe_noise=noise_ann,
                  k_eff_dedup=k_dedup, k_eff_gate=k_gate, sr_std=sr_std)
    return report


def compute_effective_trials_and_dsr(candidate_returns, historical_return_streams, bars_per_year,
                                     total_trials_n=None, prior_max_keff=1.0, prior_max_noise=0.0):
    """Backward-compatible 4-tuple: (probability, annualised noise SR, raw N, applied k_eff gate)."""
    r = compute_selection_report(candidate_returns, historical_return_streams, bars_per_year,
                                 total_trials_n, prior_max_keff, prior_max_noise)
    return r["probability"], r["expected_max_sharpe_noise"], r["raw_attempt_count"], r["k_eff_gate"]


def normalise_module(module: str | None) -> str:
    return (module or "").strip().removeprefix("./")


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
                CREATE TABLE IF NOT EXISTS spec_causality (proof_key TEXT PRIMARY KEY,hypothesis_id TEXT NOT NULL,contract_id TEXT NOT NULL,strategy_module TEXT NOT NULL,config_hash TEXT NOT NULL,data_sha256 TEXT NOT NULL,module_sha256 TEXT NOT NULL,feature_identity TEXT NOT NULL,artifact_sha256 TEXT NOT NULL,proof_sha256 TEXT NOT NULL,verified_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS qualifications (qual_id TEXT PRIMARY KEY,run_id TEXT,contract_id TEXT,hypothesis_id TEXT,lineage_id TEXT UNIQUE,holdout_gross_sharpe REAL,holdout_net_sharpe REAL,holdout_max_dd_pct REAL,val_to_holdout_sharpe_ratio REAL,approved_by_human TEXT,qualified_at REAL);
                CREATE TABLE IF NOT EXISTS holdout_claims (lineage_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,state TEXT NOT NULL,claimed_at REAL NOT NULL,read_started_at REAL,finalized_at REAL);
                CREATE TABLE IF NOT EXISTS attempt_receipts (run_id TEXT PRIMARY KEY,hypothesis_id TEXT NOT NULL,contract_id TEXT NOT NULL,trial_number INTEGER NOT NULL,state TEXT NOT NULL CHECK(state IN ('RESERVED','FINALIZED','INTERRUPTED_REVIEW')),artifact_path TEXT DEFAULT '',artifact_sha256 TEXT DEFAULT '',reason TEXT DEFAULT '',reserved_at REAL NOT NULL,finalized_at REAL);
                CREATE TABLE IF NOT EXISTS screen_receipts (screen_key TEXT PRIMARY KEY,hypothesis_id TEXT NOT NULL,contract_id TEXT NOT NULL,strategy_module TEXT NOT NULL,parameters_json TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('RESERVED','FINALIZED','INTERRUPTED_REVIEW')),attempt_id TEXT DEFAULT '',data_sha256 TEXT DEFAULT '',reserved_at REAL NOT NULL,finalized_at REAL);
                CREATE TABLE IF NOT EXISTS trial_quarantine (run_id TEXT PRIMARY KEY,reason TEXT,quarantined_at REAL);
                CREATE TABLE IF NOT EXISTS audit_reports (run_id TEXT PRIMARY KEY,contract_id TEXT,verdict TEXT,checks_json TEXT,created_at REAL);
                CREATE TABLE IF NOT EXISTS trial_dispositions (disposition_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,disposition TEXT NOT NULL,reason TEXT NOT NULL,attributed_to TEXT NOT NULL,created_at REAL NOT NULL,contract_id TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS audit_evidence (audit_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,contract_id TEXT DEFAULT '',verdict TEXT NOT NULL,checks_json TEXT NOT NULL DEFAULT '{{}}',reason TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS selection_attempts (attempt_id TEXT PRIMARY KEY,attempt_base TEXT NOT NULL,run_id TEXT DEFAULT '',hypothesis_id TEXT NOT NULL,contract_id TEXT NOT NULL,strategy_family TEXT DEFAULT '',strategy_module TEXT DEFAULT '',kind TEXT NOT NULL,stage TEXT NOT NULL,outcome TEXT NOT NULL,config_hash TEXT DEFAULT '',parameters_json TEXT DEFAULT '{{}}',data_provenance TEXT DEFAULT 'unknown',returns_observed INTEGER NOT NULL DEFAULT 0,returns_json TEXT DEFAULT '',returns_sha256 TEXT DEFAULT '',diagnostics_json TEXT DEFAULT '{{}}',created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS contract_search_state (contract_id TEXT PRIMARY KEY,max_keff_gate REAL NOT NULL,max_noise REAL NOT NULL,updated_at REAL NOT NULL);
                CREATE TRIGGER IF NOT EXISTS spec_causality_no_update BEFORE UPDATE ON spec_causality BEGIN SELECT RAISE(ABORT,'Append-only violation: causality proof is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS spec_causality_no_delete BEFORE DELETE ON spec_causality BEGIN SELECT RAISE(ABORT,'Append-only violation: causality proof is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS trials_no_update BEFORE UPDATE ON trials BEGIN SELECT RAISE(ABORT,'Append-only violation: trials table is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS trials_no_delete BEFORE DELETE ON trials BEGIN SELECT RAISE(ABORT,'Append-only violation: trials table is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS audit_reports_no_update BEFORE UPDATE ON audit_reports BEGIN SELECT RAISE(ABORT,'Append-only violation: audit evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS audit_reports_no_delete BEFORE DELETE ON audit_reports BEGIN SELECT RAISE(ABORT,'Append-only violation: audit evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS audit_evidence_no_update BEFORE UPDATE ON audit_evidence BEGIN SELECT RAISE(ABORT,'Append-only violation: audit evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS audit_evidence_no_delete BEFORE DELETE ON audit_evidence BEGIN SELECT RAISE(ABORT,'Append-only violation: audit evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS dispositions_no_update BEFORE UPDATE ON trial_dispositions BEGIN SELECT RAISE(ABORT,'Append-only violation: dispositions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS dispositions_no_delete BEFORE DELETE ON trial_dispositions BEGIN SELECT RAISE(ABORT,'Append-only violation: dispositions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS attempts_no_update BEFORE UPDATE ON selection_attempts BEGIN SELECT RAISE(ABORT,'Append-only violation: selection attempts are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON selection_attempts BEGIN SELECT RAISE(ABORT,'Append-only violation: selection attempts are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS search_state_no_relax BEFORE UPDATE ON contract_search_state
                BEGIN SELECT RAISE(ABORT,'Governance violation: contract search floor may not be relaxed')
                WHERE NEW.max_keff_gate < OLD.max_keff_gate OR NEW.max_noise < OLD.max_noise; END;
                CREATE TRIGGER IF NOT EXISTS hypotheses_no_budget_reset BEFORE UPDATE ON hypotheses
                BEGIN
                    SELECT RAISE(ABORT, 'Governance violation: trials_used cannot be decreased')
                    WHERE NEW.trials_used < OLD.trials_used;
                    SELECT RAISE(ABORT, 'Governance violation: rejected hypothesis cannot be reactivated')
                    WHERE OLD.status = 'REJECTED' AND NEW.status != 'REJECTED';
                END;
            """)
            # Additive legacy migration: a prior qualification is irrevocably
            # spent even if the newer pre-read claim table did not yet exist.
            conn.execute("""INSERT OR IGNORE INTO holdout_claims (lineage_id,run_id,state,claimed_at,read_started_at,finalized_at)
                            SELECT lineage_id,run_id,'LEGACY_SPENT',COALESCE(qualified_at,0),
                                   COALESCE(qualified_at,0),COALESCE(qualified_at,0)
                              FROM qualifications WHERE lineage_id IS NOT NULL""")
            for table, cls in (("contracts", EvaluationContract), ("hypotheses", HypothesisSpec),
                               ("trials", ExperimentResult)):
                _ensure_columns(conn, table, cls)
            # Idempotent legacy reconstruction: never reset counters or overwrite
            # old verdicts; unfinalized consumed attempts remain review-required.
            conn.execute("""INSERT OR IGNORE INTO attempt_receipts
                            (run_id,hypothesis_id,contract_id,trial_number,state,artifact_path,artifact_sha256,reserved_at,finalized_at)
                            SELECT run_id,hypothesis_id,contract_id,COALESCE(trial_number_in_hyp,0),'FINALIZED',
                                   COALESCE(returns_parquet_path,''),COALESCE(returns_sha256,''),
                                   COALESCE(created_at,0),COALESCE(created_at,0) FROM trials""")
            for hyp_id, contract_id, attempts_json in conn.execute(
                    "SELECT hypothesis_id,contract_id,attempts_json FROM hypotheses"):
                for run_id, number in json.loads(attempts_json or "{}").items():
                    conn.execute("""INSERT OR IGNORE INTO attempt_receipts
                                    (run_id,hypothesis_id,contract_id,trial_number,state,reason,reserved_at)
                                    VALUES (?,?,?,?,'INTERRUPTED_REVIEW','legacy consumed attempt without trial',0)""",
                                 (run_id, hyp_id, contract_id, int(number)))
            # Backfill old screens without replaying them or modifying search debt.
            for sr in conn.execute("SELECT attempt_id,hypothesis_id,contract_id,strategy_module,parameters_json,created_at FROM selection_attempts WHERE kind='standalone_screen'"):
                sk = self.screen_key(sr[1], sr[2], sr[3], json.loads(sr[4] or '{}'))
                conn.execute("""INSERT OR IGNORE INTO screen_receipts
                                (screen_key,hypothesis_id,contract_id,strategy_module,parameters_json,state,attempt_id,reserved_at,finalized_at)
                                VALUES (?,?,?,?,?,'FINALIZED',?,?,?)""",
                             (sk, sr[1], sr[2], normalise_module(sr[3]), sr[4] or '{}', sr[0],
                              sr[5] or 0, sr[5] or 0))
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

    # --- exclusive pre-read admission of a one-shot holdout lineage --------

    def reserve_holdout_lineage(self, lineage_id: str, run_id: str) -> None:
        """Burn one immutable lineage claim before any holdout data is opened.

        Crashes and uncertain executions intentionally remain claimed. There is
        no automatic release API: all repeats require explicit operator review.
        """
        if not lineage_id or not run_id:
            raise ValueError("lineage_id and run_id must be nonempty")
        with self._connect() as conn:
            try:
                conn.execute("INSERT INTO holdout_claims (lineage_id,run_id,state,claimed_at) VALUES (?,?,?,?)",
                             (lineage_id, run_id, "CLAIMED", time.time()))
            except sqlite3.IntegrityError as exc:
                prior = conn.execute("SELECT run_id,state FROM holdout_claims WHERE lineage_id=?", (lineage_id,)).fetchone()
                raise PermissionError(f"Lineage '{lineage_id}' was already unsealed by run '{prior['run_id']}' (state={prior['state']}).") from exc

    def mark_holdout_read_started(self, lineage_id: str, run_id: str) -> None:
        """Commit a READ_STARTED event before opening holdout bytes."""
        with self._connect() as conn:
            updated = conn.execute("""UPDATE holdout_claims SET state='READ_STARTED',read_started_at=?
                                      WHERE lineage_id=? AND run_id=? AND state='CLAIMED'""",
                                   (time.time(), lineage_id, run_id)).rowcount
            if updated != 1:
                raise PermissionError("Holdout lineage has no fresh, matching exclusive claim")

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
        receipt = self.reconcile_attempt(run_id)
        checks = [
            (not blocking, f"blocking disposition(s): {', '.join(sorted(set(blocking)))}" if blocking else ""),
            (provenance == MARKET, f"provenance '{provenance}' is not verified market data"),
            (bool(trial.get("data_snapshot_sha256")), "trial has no pinned dataset digest"),
            (trial.get("verdict") == "CANDIDATE_FOR_QUALIFICATION",
             f"historical verdict {trial.get('verdict')} is not candidate-for-qualification"),
            (receipt["state"] == "FINALIZED", f"attempt receipt {receipt['state']}: {receipt['reason']}"),
        ]
        reason = next((r for ok, r in checks if not ok), "verified market candidate evidence")
        return {"run_id": run_id, "eligible": all(ok for ok, _ in checks), "reason": reason,
                "historical_verdict": trial.get("verdict"),
                "historical_audit_verdict": audit[0] if audit else None,
                "attempt_evidence_state": receipt["state"],
                "data_provenance": provenance, "contract_id": trial.get("contract_id"),
                "dispositions": dispositions}

    def record_spec_causality(self, *, hypothesis_id, contract_id, strategy_module,
                              config_hash, data_sha256, module_sha256,
                              feature_identity, artifact_sha256, proof_sha256) -> str:
        """Immutable causal proof for a frozen experiment configuration."""
        module = normalise_module(strategy_module)
        evidence = [hypothesis_id, contract_id, module, config_hash, data_sha256,
                    module_sha256, feature_identity, artifact_sha256, proof_sha256]
        proof_key = hashlib.sha256(canonical_json(evidence).encode()).hexdigest()
        with self._connect() as conn:
            hyp = conn.execute("SELECT contract_id FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
            if not hyp or hyp[0] != contract_id:
                raise ValueError("Cannot bind causal proof to an unregistered/mismatched hypothesis")
            conn.execute("""INSERT OR IGNORE INTO spec_causality VALUES
                            (?,?,?,?,?,?,?,?,?,?,?)""",
                         (proof_key, *evidence, time.time()))
        return proof_key

    def causal_proof_for_run(self, run_id: str, repo_root: Path) -> dict:
        """No strategy import, no exploratory data read; verify exact retained proof bytes."""
        with self._connect() as conn:
            trial = conn.execute("""SELECT hypothesis_id,contract_id,strategy_module,config_hash,
                                           config_json,data_snapshot_sha256
                                    FROM trials WHERE run_id=?""", (run_id,)).fetchone()
            if not trial:
                return {"verified": False, "reason": "run not found", "proof_key": ""}
            h, cid, module, config_hash, config_json, data_sha = trial
            expected_hash = hashlib.sha256(canonical_json(json.loads(config_json or "{}")).encode()).hexdigest()[:12]
            if not module or not data_sha or config_hash != expected_hash:
                return {"verified": False, "reason": "run configuration or dataset identity is unbound", "proof_key": ""}
            rows = conn.execute("""SELECT proof_key,module_sha256,feature_identity,artifact_sha256,proof_sha256
                                   FROM spec_causality WHERE hypothesis_id=? AND contract_id=?
                                   AND strategy_module=? AND config_hash=? AND data_sha256=?
                                   ORDER BY verified_at DESC""",
                                (h, cid, normalise_module(module), config_hash, data_sha)).fetchall()
            if not rows:
                return {"verified": False, "reason": "no exact run-bound causality proof", "proof_key": ""}
            feature_rows = {x[0]: x for x in conn.execute(
                "SELECT feature_hash,contract_id,prefix_invariance_passed,parquet_cache_path FROM feature_sets"
            )}
        root = Path(repo_root).resolve()
        src = (root / module).resolve()
        if not src.is_relative_to(root / "strategies") or not src.is_file():
            return {"verified": False, "reason": "frozen strategy module unavailable", "proof_key": ""}
        module_sha = hashlib.sha256(src.read_bytes()).hexdigest()
        for key, recorded_module_sha, identity, artifact_sha, proof_sha in rows:
            art = root / "cache" / "features" / f"{identity}.parquet"
            proof = root / "cache" / "features" / f"{identity}.proof.json"
            frow = feature_rows.get(identity)
            if (recorded_module_sha != module_sha or not frow or frow[1] != cid
                or frow[2] != 1 or Path(frow[3]).resolve() != art.resolve()
                or not art.is_file() or not proof.is_file()):
                continue
            if (hashlib.sha256(art.read_bytes()).hexdigest() != artifact_sha or
                hashlib.sha256(proof.read_bytes()).hexdigest() != proof_sha):
                continue
            try:
                doc = json.loads(proof.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # Corrupt or raced proof is never causal evidence.
            if isinstance(doc, dict) and doc.get("causality") is True and doc.get("artifact_sha256") == artifact_sha:
                return {"verified": True, "reason": "exact run-bound immutable causal feature proof", "proof_key": key}
        return {"verified": False, "reason": "causality proof drift or input/source revision", "proof_key": ""}

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
            module = normalise_module(hyp.strategy_module)
            shared = [r[0] for r in conn.execute("SELECT hypothesis_id FROM hypotheses WHERE strategy_module=?",
                                                 (module,))] if module else []
            if shared and not hyp.parent_hypothesis_id:
                raise ValueError(f"strategy module '{module}' is already registered to {shared[0]}; "
                                 "declare parent_hypothesis_id to share the exhausted lineage budget")
            _insert(conn, "hypotheses", {**asdict(hyp), "created_at": time.time()})

    def record_hypothesis_module(self, hypothesis_id, module):
        """Normalise the module once; later drift is a governance error, not a silent rebind."""
        module = normalise_module(module)
        if not module:
            return
        with self._connect() as conn:
            row = conn.execute("SELECT strategy_module FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
            if not row:
                raise ValueError("Unknown hypothesis")
            if row[0] and normalise_module(row[0]) != module:
                raise ValueError(f"Strategy module drift for '{hypothesis_id}': {row[0]!r} -> {module!r}")
            if not row[0]:
                conn.execute("UPDATE hypotheses SET strategy_module=? WHERE hypothesis_id=?", (module, hypothesis_id))

    def _lineage(self, conn, hypothesis_id):
        root, seen = hypothesis_id, set()
        while root not in seen:
            row = conn.execute("SELECT parent_hypothesis_id FROM hypotheses WHERE hypothesis_id=?", (root,)).fetchone()
            if not row or not row[0] or row[0] == root:
                break
            seen.add(root)
            root = row[0]
        members = {r[0] for r in conn.execute(
            "WITH RECURSIVE d(hypothesis_id) AS (SELECT ? UNION "
            "SELECT h.hypothesis_id FROM hypotheses h JOIN d ON h.parent_hypothesis_id = d.hypothesis_id) "
            "SELECT hypothesis_id FROM d", (root,))}
        modules = {normalise_module(m) for (m,) in conn.execute(
            f"SELECT strategy_module FROM hypotheses WHERE hypothesis_id IN ({','.join('?' * len(members))})",
            tuple(members)) if m}
        if modules:
            members |= {r[0] for r in conn.execute(
                f"SELECT hypothesis_id FROM hypotheses WHERE strategy_module IN ({','.join('?' * len(modules))})",
                tuple(modules))}
        return root, members

    def check_hypothesis_budget(self, hypothesis_id):
        with self._connect() as conn:
            row = conn.execute("SELECT trials_used,status FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown hypothesis")
            if row[0] >= PER_HYPOTHESIS_CAP:
                conn.execute("UPDATE hypotheses SET status='REJECTED' WHERE hypothesis_id=? AND status='ACTIVE'", (hypothesis_id,))
            root, members = self._lineage(conn, hypothesis_id)
            used = sum(r[0] for r in conn.execute(
                f"SELECT trials_used FROM hypotheses WHERE hypothesis_id IN ({','.join('?' * len(members))})",
                tuple(members)))
        if row[0] >= PER_HYPOTHESIS_CAP or row[1] != "ACTIVE":
            raise HypothesisBudgetExhaustedError(hypothesis_id)
        if used >= LINEAGE_CAP:
            raise HypothesisBudgetExhaustedError(
                f"{hypothesis_id}: lineage {root} exhausted ({used}/{LINEAGE_CAP} trials across "
                f"{len(members)} hypothesis/module aliases)")

    @staticmethod
    def screen_key(hypothesis_id, contract_id, strategy_module, parameters) -> str:
        """One identity per registered question/configuration, independent of data refresh."""
        return hashlib.sha256(canonical_json([
            hypothesis_id, contract_id, normalise_module(strategy_module), parameters or {},
        ]).encode()).hexdigest()[:32]

    def reserve_screen(self, hypothesis_id, contract_id, strategy_module, parameters, data_sha256="") -> str:
        """Durably admit Stage 1 before its first validation read/compute."""
        key = self.screen_key(hypothesis_id, contract_id, strategy_module, parameters)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT contract_id FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
            if not row or row[0] != contract_id:
                raise ValueError("Screen hypothesis and contract must match frozen registry")
            old = conn.execute("SELECT state FROM screen_receipts WHERE screen_key=?", (key,)).fetchone()
            if old:
                raise ScreenAlreadyAttemptedError(f"Stage 1 screen already admitted: {key} ({old[0]}); review, never replay")
            conn.execute("""INSERT INTO screen_receipts
                            (screen_key,hypothesis_id,contract_id,strategy_module,parameters_json,state,data_sha256,reserved_at)
                            VALUES (?,?,?,?,?,'RESERVED',?,?)""",
                         (key, hypothesis_id, contract_id, normalise_module(strategy_module),
                          canonical_json(parameters or {}), data_sha256, time.time()))
        return key

    def screen_status(self, hypothesis_id, contract_id, strategy_module, parameters) -> dict:
        key = self.screen_key(hypothesis_id, contract_id, strategy_module, parameters)
        with self._connect() as conn:
            row = conn.execute("SELECT state,attempt_id,data_sha256 FROM screen_receipts WHERE screen_key=?", (key,)).fetchone()
            attempt = conn.execute("SELECT outcome FROM selection_attempts WHERE attempt_id=?", (row[1],)).fetchone() if row and row[1] else None
        state = row[0] if row else "UNSEEN"
        if state == "FINALIZED" and attempt is None:
            state = "EVIDENCE_DRIFT"
        return {"screen_key": key, "state": state, "attempt_id": row[1] if row else "",
                "outcome": attempt[0] if attempt else None, "data_sha256": row[2] if row else "",
                "retry_allowed": state == "UNSEEN"}

    def spec_status(self, hypothesis_id, contract_id, strategy_module, parameters) -> dict:
        """One read-only projection for queue restart, without running research."""
        module, config = normalise_module(strategy_module), canonical_json(parameters or {})
        config_hash = hashlib.sha256(config.encode()).hexdigest()[:12]
        screen = self.screen_status(hypothesis_id, contract_id, module, parameters)
        with self._connect() as conn:
            trial = conn.execute("""SELECT run_id,verdict FROM trials WHERE hypothesis_id=? AND
                                   contract_id=? AND strategy_module=? AND config_hash=?
                                   ORDER BY created_at DESC LIMIT 1""",
                                 (hypothesis_id, contract_id, module, config_hash)).fetchone()
            pending = conn.execute("""SELECT run_id,state FROM attempt_receipts WHERE
                                      hypothesis_id=? AND contract_id=? AND state!='FINALIZED'
                                      ORDER BY reserved_at DESC LIMIT 1""",
                                   (hypothesis_id, contract_id)).fetchone()
        if pending:
            return {"state": "REQUIRES_RECONCILIATION", "reason": f"trial {pending[0]} is {pending[1]}",
                    "run_id": pending[0], "screen": screen}
        if trial:
            receipt = self.reconcile_attempt(trial[0])
            if receipt["state"] != "FINALIZED" or trial[1] == "FAILED":
                return {"state": "REQUIRES_RECONCILIATION", "reason": receipt["reason"] or f"trial {trial[1]}",
                        "run_id": trial[0], "screen": screen}
            return {"state": "STAGE3_FINAL", "reason": "immutable Warden trial recorded",
                    "run_id": trial[0], "verdict": trial[1], "screen": screen}
        if screen["state"] == "FINALIZED":
            if screen["outcome"] == "PASS":
                state = "STAGE1_PASS"
            elif screen["outcome"] == "REJECT":
                state = "STAGE1_REJECT"
            else:
                state = "REQUIRES_RECONCILIATION"
        elif screen["state"] == "UNSEEN":
            state = "NEW"
        else:
            state = "REQUIRES_RECONCILIATION"
        return {"state": state, "reason": screen["state"], "run_id": None, "screen": screen}

    def register_trial_start(self, hypothesis_id, run_id):
        """Atomically reserve unique run identity and monotonic hypothesis/lineage budget."""
        valid_id(run_id)
        self.check_hypothesis_budget(hypothesis_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT trials_used,status,attempts_json,contract_id FROM hypotheses WHERE hypothesis_id=?",
                               (hypothesis_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown hypothesis")
            if row[0] >= PER_HYPOTHESIS_CAP or row[1] != "ACTIVE":
                raise HypothesisBudgetExhaustedError(hypothesis_id)
            root, members = self._lineage(conn, hypothesis_id)
            used = sum(r[0] for r in conn.execute(
                f"SELECT trials_used FROM hypotheses WHERE hypothesis_id IN ({','.join('?' * len(members))})",
                tuple(members)))
            if used >= LINEAGE_CAP:
                raise HypothesisBudgetExhaustedError(f"{hypothesis_id}: lineage {root} exhausted ({used}/{LINEAGE_CAP})")
            attempts = json.loads(row[2])
            if (run_id in attempts or
                conn.execute("SELECT 1 FROM trials WHERE run_id=?", (run_id,)).fetchone() or
                conn.execute("SELECT 1 FROM attempt_receipts WHERE run_id=?", (run_id,)).fetchone()):
                raise ValueError("Duplicate run_id")
            number = int(row[0]) + 1
            conn.execute("""INSERT INTO attempt_receipts
                            (run_id,hypothesis_id,contract_id,trial_number,state,reserved_at)
                            VALUES (?,?,?,?,?,?)""",
                         (run_id, hypothesis_id, row[3], number, "RESERVED", time.time()))
            attempts[run_id] = number
            conn.execute("UPDATE hypotheses SET trials_used=trials_used+1,attempts_json=? WHERE hypothesis_id=?",
                         (canonical_json(attempts), hypothesis_id))
            return number

    def reconcile_attempt(self, run_id: str, *, confirm_interrupted: bool = False) -> dict:
        """Never rerun science: project receipt validity and optional durable review state."""
        with self._connect() as conn:
            receipt = conn.execute("SELECT * FROM attempt_receipts WHERE run_id=?", (run_id,)).fetchone()
            if receipt is None:
                return {"run_id": run_id, "state": "UNKNOWN", "reason": "no reservation", "eligible": False}
            if confirm_interrupted and receipt["state"] == "RESERVED":
                conn.execute("UPDATE attempt_receipts SET state='INTERRUPTED_REVIEW',reason=? WHERE run_id=? AND state='RESERVED'",
                             ("interrupted before committed final result", run_id))
                receipt = conn.execute("SELECT * FROM attempt_receipts WHERE run_id=?", (run_id,)).fetchone()
            trial = conn.execute("SELECT returns_parquet_path,returns_sha256,verdict FROM trials WHERE run_id=?", (run_id,)).fetchone()
        state, reason = receipt["state"], receipt["reason"] or ""
        if state == "FINALIZED" and trial is None:
            state, reason = "EVIDENCE_DRIFT", "finalized receipt has no immutable trial"
        elif state == "FINALIZED" and trial and trial[0]:
            path = Path(trial[0])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != trial[1]:
                state, reason = "EVIDENCE_DRIFT", "Parquet receipt missing or digest mismatched"
        elif state == "RESERVED":
            reason = "result uncommitted; reconcile after writer exits, never auto retry"
        return {"run_id": run_id, "state": state, "reason": reason, "eligible": state == "FINALIZED",
                "verdict": trial[2] if trial else None,
                "artifact_sha256": receipt["artifact_sha256"] or ""}

    def record_selection_attempt(self, *, hypothesis_id, contract_id, kind, stage, outcome,
                                 strategy_family="", strategy_module="", config_hash="", parameters=None,
                                 data_provenance="unknown", returns=None, diagnostics=None, run_id="", screen_key=None):
        """Persist one validation-consuming configuration. Repeats are counted, never merged."""
        base = hashlib.sha256(canonical_json([hypothesis_id, contract_id, kind, stage, outcome, config_hash,
                                              normalise_module(strategy_module)]).encode()).hexdigest()[:16]
        vector = None if returns is None else np.asarray(returns, dtype=float)
        if vector is not None and (vector.ndim != 1 or len(vector) == 0 or not np.isfinite(vector).all()):
            vector = None
        payload = canonical_json(vector.tolist()) if vector is not None else ""
        with self._connect() as conn:
            seq = conn.execute("SELECT COUNT(*) FROM selection_attempts WHERE attempt_base=?", (base,)).fetchone()[0] + 1
            attempt_id = f"ATT-{base}-{seq:03d}"
            conn.execute(
                "INSERT INTO selection_attempts (attempt_id,attempt_base,run_id,hypothesis_id,contract_id,"
                "strategy_family,strategy_module,kind,stage,outcome,config_hash,parameters_json,data_provenance,"
                "returns_observed,returns_json,returns_sha256,diagnostics_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (attempt_id, base, run_id, hypothesis_id, contract_id, strategy_family,
                 normalise_module(strategy_module), kind, stage, outcome, config_hash,
                 canonical_json(parameters or {}), data_provenance, int(vector is not None), payload,
                 hashlib.sha256(payload.encode()).hexdigest() if vector is not None else "",
                 canonical_json(diagnostics or {}), time.time()))
            if screen_key is not None:
                updated = conn.execute("""UPDATE screen_receipts SET state='FINALIZED',attempt_id=?,finalized_at=?
                                          WHERE screen_key=? AND hypothesis_id=? AND contract_id=? AND state='RESERVED'""",
                                       (attempt_id, time.time(), screen_key, hypothesis_id, contract_id)).rowcount
                if updated != 1:
                    raise ScreenAlreadyAttemptedError("No matching exclusive Stage 1 reservation to finalize")
        return attempt_id

    def validation_attempt_count(self, contract_id):
        with self._connect() as conn:
            trials = conn.execute("SELECT COUNT(*) FROM trials WHERE contract_id=?", (contract_id,)).fetchone()[0]
            screens = conn.execute("SELECT COUNT(*) FROM selection_attempts WHERE contract_id=?",
                                   (contract_id,)).fetchone()[0]
        return int(trials + screens)

    def selection_history(self, contract_id, expected_len):
        """Observed return streams plus explicit accounting of unvectorised attempts."""
        with self._connect() as conn:
            rows = conn.execute("SELECT run_id,returns_json,stage_reached FROM trials WHERE contract_id=? "
                                "ORDER BY created_at,run_id", (contract_id,)).fetchall()
        streams, excluded = [], []
        for run_id, r_json, stage in rows:
            if stage == "STAGE_1_SIGNAL" or not r_json or r_json == "[]":
                excluded.append({"run_id": run_id, "reason": "no observed return stream"})
                continue
            arr = np.array(json.loads(r_json), dtype=float)
            if float(np.std(arr)) < 1e-12:
                excluded.append({"run_id": run_id, "reason": "zero-variance stream is not observed returns"})
                continue
            if len(arr) != expected_len:
                raise SelectionHistoryError(
                    f"Unaligned selection history: {run_id} has {len(arr)} bars but the candidate has "
                    f"{expected_len}; refusing to pad, slice or correlate incompatible windows")
            streams.append(arr)
        raw = self.validation_attempt_count(contract_id)
        return {"streams": streams, "excluded": excluded, "raw_attempt_count": raw,
                "unvectorized_attempts": max(0, raw - len(streams))}

    def prior_search_floor(self, contract: EvaluationContract):
        """Historical floor, inherited by later contract versions of the same market/frequency."""
        family = f"{contract.asset_class}:{contract.frequency}"
        with self._connect() as conn:
            state = conn.execute(
                "SELECT MAX(s.max_keff_gate),MAX(s.max_noise) FROM contract_search_state s "
                "JOIN contracts c ON c.contract_id=s.contract_id "
                "WHERE s.contract_id=? OR (c.asset_class=? AND c.frequency=?)",
                (contract.contract_id, contract.asset_class, contract.frequency)).fetchone()
            trial_max = conn.execute("SELECT MAX(effective_trials_keff) FROM trials WHERE contract_id=?",
                                     (contract.contract_id,)).fetchone()[0]
        return max(float(state[0] or 1.0), float(trial_max or 1.0)), float(state[1] or 0.0), family

    def update_search_floor(self, contract_id, k_eff_gate, noise):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO contract_search_state VALUES (?,?,?,?) ON CONFLICT(contract_id) DO UPDATE SET "
                "max_keff_gate=MAX(max_keff_gate,excluded.max_keff_gate),"
                "max_noise=MAX(max_noise,excluded.max_noise),updated_at=excluded.updated_at",
                (contract_id, float(k_eff_gate), float(noise), time.time()))

    def load_prior_trial_returns(self, contract_id, expected_len):
        try:
            return self.selection_history(contract_id, expected_len)["streams"]
        except SelectionHistoryError as exc:
            raise ValueError(str(exc).replace("Unaligned selection history",
                                              "Contract-wide trial histories are not aligned")) from exc
    def record_trial(self, result, config_dict, net_returns, trial_number=None, diagnostics=None):
        valid_id(result.run_id)
        valid_id(result.contract_id)
        returns = np.asarray(net_returns, dtype=float)
        if returns.ndim != 1 or not np.isfinite(returns).all():
            raise ValueError("Returns must be a finite vector")
        if trial_number is None:
            trial_number = self.register_trial_start(result.hypothesis_id, result.run_id)
        path = self.db_path.parent / "returns" / result.contract_id / f"{result.run_id}.parquet"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT contract_id,attempts_json FROM hypotheses WHERE hypothesis_id=?", (result.hypothesis_id,)).fetchone()
            if row is None or row[0] != result.contract_id or json.loads(row[1]).get(result.run_id) != trial_number:
                raise ValueError("Missing or mismatched trial reservation")
            observed = len(returns) > 0
            returns_sha256 = ""
            if observed:
                path.parent.mkdir(parents=True, exist_ok=True)
                # Write, fsync, then atomically publish without replacing any
                # previous artifact. A crash before commit remains reviewable.
                with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".pending") as handle:
                    pl.DataFrame({"net_return": returns}).write_parquet(handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                    os.link(handle.name, path)
                dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)  # File name must be durable before SQLite FINALIZED commit.
                finally:
                    os.close(dir_fd)
            if observed:
                returns_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            trial_dict = {**result.to_dict(), "config_json": canonical_json(config_dict),
                          "returns_json": canonical_json(returns.tolist()),
                          "returns_parquet_path": str(path) if observed else "",
                          "returns_sha256": returns_sha256,
                          "diagnostics_json": canonical_json(diagnostics or result.diagnostics_json),
                          "trial_number_in_hyp": trial_number, "git_commit_sha": config_dict.get("git_commit_sha", ""), "created_at": time.time()}
            _insert(conn, "trials", trial_dict)
            updated = conn.execute("""UPDATE attempt_receipts SET state='FINALIZED',artifact_path=?,
                                      artifact_sha256=?,finalized_at=? WHERE run_id=? AND
                                      hypothesis_id=? AND trial_number=? AND state='RESERVED'""",
                                   (str(path) if observed else "", returns_sha256, time.time(),
                                    result.run_id, result.hypothesis_id, trial_number)).rowcount
            if updated != 1:
                raise ValueError("Missing or mismatched durable attempt receipt")
