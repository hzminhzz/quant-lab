"""Durable trial ledger and reference effective-rank DSR; no harness dependencies."""

import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, fields
from pathlib import Path

import numpy as np
import polars as pl
from ml4t.diagnostic.evaluation.stats import (
    deflated_sharpe_ratio_from_statistics,
    effective_number_of_trials,
)
from scipy import stats

from warden import trust_root
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
LINEAGE_CAP = 3  # one shared budget per root/descendant/module-alias lineage; no auto-replenishment
CORR_DUPLICATE = 0.9995  # |corr| at or above this is the same trial, not a new one
CORR_NEAR_DUPLICATE = 0.98  # near-collinear streams are the same search trial for effective rank
CORR_METHOD = "effective_rank"


class HypothesisBudgetExhaustedError(ValueError):
    pass


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
        if any(abs(float(np.corrcoef(matrix[:, j], matrix[:, k])[0, 1])) >= tol for k in keep):
            continue
        keep.append(j)
    return keep


def _effective_rank(matrix: np.ndarray) -> float:
    """ML4T reference effective rank when comparable, local eigen-entropy otherwise."""
    if matrix.ndim != 2 or matrix.shape[1] < 2:
        return 1.0
    try:
        return float(effective_number_of_trials(matrix, method=CORR_METHOD).k_eff)
    except (ValueError, FloatingPointError):
        return _eigen_rank(matrix)


def compute_selection_report(candidate_returns, historical_return_streams, bars_per_year: int,
                            raw_attempt_count: int | None = None, prior_max_keff: float = 1.0,
                            prior_max_noise: float = 0.0) -> dict:
    """Raw N, reference effective rank, and the applied gate. No invented per-attempt penalty."""
    streams = [np.asarray(v, dtype=float) for v in [*historical_return_streams, candidate_returns]]
    n = len(streams[-1])
    if any(v.ndim != 1 or len(v) != n or not np.isfinite(v).all() for v in streams):
        raise ValueError("Trial returns must be finite and temporally aligned")
    candidate = streams[-1]
    raw = int(raw_attempt_count if raw_attempt_count is not None else len(streams))
    unvectorized, floor = max(0, raw - len(streams)), max(1.0, float(prior_max_keff))
    report = {"raw_attempt_count": raw, "streams_used": len(streams), "unvectorized_attempts": unvectorized,
              "prior_max_keff": float(prior_max_keff), "prior_max_noise": float(prior_max_noise),
              "k_eff_dedup": 1.0, "k_eff_gate": floor, "probability": 0.0,
              "expected_max_sharpe_noise": 0.0, "noise_floor_applied": False,
              "correction_policy": f"ML4T {CORR_METHOD} dedup@{CORR_NEAR_DUPLICATE} + immutable prior floor; "
                                   "no arbitrary per-attempt penalty",
              "non_promotable_reason": ""}
    if unvectorized:
        report["non_promotable_reason"] = (
            f"{unvectorized} validation-consuming attempt(s) have no comparable return stream; statistical "
            "promotion requires complete comparable evidence")
    if n < 10 or float(np.std(candidate)) < 1e-12:
        report["non_promotable_reason"] = "candidate return stream is empty or degenerate"
        return report
    matrix = np.column_stack(streams)
    hist, cand = matrix[:, :-1], matrix[:, -1]
    hist_kept = distinct_stream_indices(hist, CORR_NEAR_DUPLICATE) if hist.shape[1] else []
    # The candidate represents its own direction: a near-collinear copy of it in the history must
    # not change the hurdle, and max(k_all, k_hist) stops a new losing attempt lowering it.
    usable = [j for j in hist_kept if abs(float(np.corrcoef(hist[:, j], cand)[0, 1])) < CORR_NEAR_DUPLICATE]
    kept = [*usable, matrix.shape[1] - 1]
    k_gate = max(_effective_rank(matrix[:, kept]), 1.0 if len(usable) < 2 else _effective_rank(hist[:, usable]), floor)
    sharpes = [float(np.mean(matrix[:, j]) / (np.std(matrix[:, j], ddof=1) + 1e-12)) for j in kept]
    sr_std = max(float(np.std(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0, 1.0 / math.sqrt(n))
    sr = float(np.mean(candidate) / (np.std(candidate, ddof=1) + 1e-12))
    rho = 0.0
    if n > 2 and float(np.std(candidate[:-1])) > 1e-12 and float(np.std(candidate[1:])) > 1e-12:
        rho = float(np.corrcoef(candidate[:-1], candidate[1:])[0, 1])
        rho = rho if np.isfinite(rho) else 0.0
    skew, excess_kurt = float(stats.skew(candidate)), float(stats.kurtosis(candidate, fisher=True))
    reference = deflated_sharpe_ratio_from_statistics(
        observed_sharpe=sr, n_samples=n, n_trials=max(raw, 2), variance_trials=sr_std ** 2,
        skewness=skew, excess_kurtosis=excess_kurt, autocorrelation=rho, periods_per_year=bars_per_year,
        effective_trials=k_gate, correlation_method=CORR_METHOD, min_k_eff=floor)
    probability, noise_ann = float(reference.probability), float(reference.expected_max_sharpe) * math.sqrt(bars_per_year)
    if prior_max_noise > noise_ann:  # re-apply the recorded annualised floor as the DSR threshold
        floored = deflated_sharpe_ratio_from_statistics(
            observed_sharpe=sr, n_samples=n, n_trials=1, variance_trials=0.0, skewness=skew,
            excess_kurtosis=excess_kurt, autocorrelation=rho, periods_per_year=bars_per_year,
            benchmark_sharpe=float(prior_max_noise) / math.sqrt(bars_per_year))
        probability, noise_ann = min(probability, float(floored.probability)), float(prior_max_noise)
        report["noise_floor_applied"] = True
    report.update(probability=probability, expected_max_sharpe_noise=noise_ann, k_eff_gate=k_gate,
                  k_eff_dedup=k_gate, sr_std=sr_std)
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


def _placeholders(n):
    return ",".join("?" * n)


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
                CREATE TABLE IF NOT EXISTS selection_attempts (attempt_id TEXT PRIMARY KEY,attempt_base TEXT NOT NULL,run_id TEXT DEFAULT '',hypothesis_id TEXT NOT NULL,contract_id TEXT NOT NULL,strategy_family TEXT DEFAULT '',strategy_module TEXT DEFAULT '',kind TEXT NOT NULL,stage TEXT NOT NULL,outcome TEXT NOT NULL,config_hash TEXT DEFAULT '',parameters_json TEXT DEFAULT '{{}}',data_provenance TEXT DEFAULT 'unknown',returns_observed INTEGER NOT NULL DEFAULT 0,returns_json TEXT DEFAULT '',returns_sha256 TEXT DEFAULT '',diagnostics_json TEXT DEFAULT '{{}}',created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS contract_search_state (contract_id TEXT PRIMARY KEY,max_keff_gate REAL NOT NULL,max_noise REAL NOT NULL,updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS scope_authorizations (authorization_id TEXT PRIMARY KEY,root_hypothesis_id TEXT NOT NULL,hypothesis_id TEXT NOT NULL,contract_id TEXT NOT NULL,strategy_module TEXT NOT NULL DEFAULT '',rationale TEXT NOT NULL,approver TEXT NOT NULL,evidence_sha256 TEXT NOT NULL,signature TEXT NOT NULL,created_at REAL NOT NULL);
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
                CREATE TRIGGER IF NOT EXISTS scope_auth_no_update BEFORE UPDATE ON scope_authorizations BEGIN SELECT RAISE(ABORT,'Append-only violation: scope authorizations are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS scope_auth_no_delete BEFORE DELETE ON scope_authorizations BEGIN SELECT RAISE(ABORT,'Append-only violation: scope authorizations are immutable'); END;
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
                "SELECT disposition,reason,attributed_to,created_at FROM trial_dispositions WHERE run_id=? ORDER BY created_at", (run_id,))]
            quarantine = [dict(r) for r in conn.execute(
                "SELECT reason,quarantined_at FROM trial_quarantine WHERE run_id=?", (run_id,))]
            audit = conn.execute("SELECT verdict FROM audit_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
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
                "historical_verdict": trial.get("verdict"), "historical_audit_verdict": audit[0] if audit else None,
                "data_provenance": provenance, "contract_id": trial.get("contract_id"), "dispositions": dispositions}

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
            if hyp.parent_hypothesis_id and not conn.execute(
                    "SELECT 1 FROM hypotheses WHERE hypothesis_id=?", (hyp.parent_hypothesis_id,)).fetchone():
                raise ValueError("Register parent hypothesis first")
            module = normalise_module(hyp.strategy_module)
            shared = [r[0] for r in conn.execute("SELECT hypothesis_id FROM hypotheses WHERE strategy_module=?",
                                                 (module,))] if module else []
            if shared and not hyp.parent_hypothesis_id:
                raise ValueError(f"strategy module '{module}' is already registered to {shared[0]}; "
                                 "declare parent_hypothesis_id to share the exhausted lineage budget")
            _insert(conn, "hypotheses", {**asdict(hyp), "created_at": time.time()})

    def record_hypothesis_module(self, hypothesis_id, module):
        """Normalise the module once; later drift or a shared binding is a governance error."""
        module = normalise_module(module)
        if not module:
            return
        with self._connect() as conn:
            row = conn.execute("SELECT strategy_module,parent_hypothesis_id FROM hypotheses WHERE hypothesis_id=?",
                               (hypothesis_id,)).fetchone()
            if not row:
                raise ValueError("Unknown hypothesis")
            if row[0] and normalise_module(row[0]) != module:
                raise ValueError(f"Strategy module drift for '{hypothesis_id}': {row[0]!r} -> {module!r}")
            owner = conn.execute("SELECT hypothesis_id FROM hypotheses WHERE strategy_module=? AND hypothesis_id<>?",
                                 (module, hypothesis_id)).fetchone()
            if owner and not row[1]:
                raise ValueError(f"strategy module '{module}' is already registered to {owner[0]}; "
                                 "declare parent_hypothesis_id to share the exhausted lineage budget")
            if not row[0]:
                conn.execute("UPDATE hypotheses SET strategy_module=? WHERE hypothesis_id=?", (module, hypothesis_id))

    # --- shared three-attempt lineage and signed scope escalation ----------
    def _authorized_roots(self, conn) -> set[str]:
        authorized = set()
        for hid, contract_id, module, signature, rationale, approver, evidence, root in conn.execute(
                "SELECT hypothesis_id,contract_id,strategy_module,signature,rationale,approver,"
                "evidence_sha256,root_hypothesis_id FROM scope_authorizations"):
            row = conn.execute("SELECT contract_id,strategy_module FROM hypotheses WHERE hypothesis_id=?", (hid,)).fetchone()
            if not row or row[0] != contract_id or normalise_module(row[1]) != normalise_module(module):
                continue
            payload = {"root_hypothesis_id": root, "hypothesis_id": hid, "contract_id": contract_id,
                       "strategy_module": normalise_module(module), "rationale": rationale,
                       "approver": approver, "evidence_sha256": evidence}
            if trust_root.verify_approval(canonical_json(payload).encode(), signature):
                authorized.add(hid)
        return authorized

    def _lineage(self, conn, hypothesis_id):
        """Fixpoint over parents, descendants and module aliases, stopping at signed scopes."""
        has_scope = conn.execute("SELECT 1 FROM scope_authorizations LIMIT 1").fetchone()
        authorized = self._authorized_roots(conn) if has_scope else set()
        if hypothesis_id in authorized:  # a signed human decision starts a distinct budget
            return hypothesis_id, {hypothesis_id}
        root, seen = hypothesis_id, set()
        while root not in seen and root not in authorized:
            row = conn.execute("SELECT parent_hypothesis_id FROM hypotheses WHERE hypothesis_id=?", (root,)).fetchone()
            if not row or not row[0] or row[0] == root:
                break
            seen.add(root)
            root = row[0]
        members: set[str] = set()
        while True:
            before = len(members)
            members.add(root)
            frontier = [h for h in members if h not in authorized]
            while frontier:
                for (child,) in conn.execute("SELECT hypothesis_id FROM hypotheses WHERE parent_hypothesis_id=?",
                                             (frontier.pop(),)):
                    if child not in members and child not in authorized:
                        members.add(child)
                        frontier.append(child)
            modules = {normalise_module(m) for (m,) in conn.execute(
                f"SELECT strategy_module FROM hypotheses WHERE hypothesis_id IN ({_placeholders(len(members))})",
                tuple(members)) if m}
            if modules:
                members |= {a for (a,) in conn.execute(
                    f"SELECT hypothesis_id FROM hypotheses WHERE strategy_module IN ({_placeholders(len(modules))})",
                    tuple(modules)) if a not in authorized}
            if len(members) == before:
                return root, members

    def _usage(self, conn, hypothesis_id):
        root, members = self._lineage(conn, hypothesis_id)
        marks = _placeholders(len(members))
        reserved = {hid: set(json.loads(attempts or "{}")) for hid, attempts in conn.execute(
            f"SELECT hypothesis_id,attempts_json FROM hypotheses WHERE hypothesis_id IN ({marks})", tuple(members))}
        attempts = list(conn.execute(
            f"SELECT hypothesis_id,run_id FROM selection_attempts WHERE hypothesis_id IN ({marks})", tuple(members)))

        def used(ids):
            return sum(len(reserved.get(hid, set())) + sum(
                1 for a_hid, a_run in attempts if a_hid == hid and not (a_run and a_run in reserved.get(hid, set())))
                for hid in ids)

        return used({hypothesis_id}), used(members), root, members

    def check_hypothesis_budget(self, hypothesis_id):
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown hypothesis")
            own, lineage_used, root, members = self._usage(conn, hypothesis_id)
            if own >= PER_HYPOTHESIS_CAP:
                conn.execute("UPDATE hypotheses SET status='REJECTED' WHERE hypothesis_id=? AND status='ACTIVE'",
                             (hypothesis_id,))
            status = conn.execute("SELECT status FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()[0]
        if own >= PER_HYPOTHESIS_CAP or status != "ACTIVE":
            raise HypothesisBudgetExhaustedError(hypothesis_id)
        if lineage_used >= LINEAGE_CAP:
            raise HypothesisBudgetExhaustedError(
                f"{hypothesis_id}: lineage {root} exhausted ({lineage_used}/{LINEAGE_CAP} attempts across "
                f"{len(members)} hypothesis/module aliases); new scope needs a signed human authorization")

    def record_scope_authorization(self, *, root_hypothesis_id, hypothesis_id, contract_id,
                                   strategy_module="", rationale, approver, evidence_sha256, signature):
        """Append-only, signature-verified human decision that starts a distinct three-attempt scope."""
        if len(rationale.strip()) < 40 or not approver.strip():
            raise ValueError("a scope authorization needs a substantive rationale and an approver")
        if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256 or ""):
            raise ValueError("a scope authorization needs a 64-hexdigest of externally trusted evidence")
        payload = {"root_hypothesis_id": root_hypothesis_id, "hypothesis_id": hypothesis_id,
                   "contract_id": contract_id, "strategy_module": normalise_module(strategy_module),
                   "rationale": rationale.strip(), "approver": approver.strip(), "evidence_sha256": evidence_sha256}
        if not trust_root.verify_approval(canonical_json(payload).encode(), signature):
            raise PermissionError("scope authorization signature does not verify against the pinned human key")
        authorization_id = "AUTH-" + hashlib.sha256(canonical_json(payload).encode()).hexdigest()[:24]
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM hypotheses WHERE hypothesis_id=?", (root_hypothesis_id,)).fetchone():
                raise ValueError("authorize the scope only after the root hypothesis exists")
            conn.execute("INSERT OR IGNORE INTO scope_authorizations VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (authorization_id, root_hypothesis_id, hypothesis_id, contract_id, payload["strategy_module"],
                          payload["rationale"], payload["approver"], evidence_sha256, signature, time.time()))
        return authorization_id

    def scope_authorized(self, hypothesis_id: str) -> bool:
        with self._connect() as conn:
            return hypothesis_id in self._authorized_roots(conn)

    def register_trial_start(self, hypothesis_id, run_id):
        valid_id(run_id)
        self.check_hypothesis_budget(hypothesis_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT trials_used,status,attempts_json FROM hypotheses WHERE hypothesis_id=?",
                               (hypothesis_id,)).fetchone()
            if self._usage(conn, hypothesis_id)[0] >= PER_HYPOTHESIS_CAP or row[1] != "ACTIVE":
                raise HypothesisBudgetExhaustedError(hypothesis_id)
            attempts = json.loads(row[2])
            if run_id in attempts or conn.execute("SELECT 1 FROM trials WHERE run_id=?", (run_id,)).fetchone():
                raise ValueError("Duplicate run_id")
            attempts[run_id] = row[0] + 1
            conn.execute("UPDATE hypotheses SET trials_used=trials_used+1,attempts_json=? WHERE hypothesis_id=?",
                         (canonical_json(attempts), hypothesis_id))
            return row[0] + 1

    def record_selection_attempt(self, *, hypothesis_id, contract_id, kind, stage, outcome,
                                 strategy_family="", strategy_module="", config_hash="", parameters=None,
                                 data_provenance="unknown", returns=None, diagnostics=None, run_id=""):
        """Persist one validation-consuming configuration. Repeats are counted, never merged."""
        if run_id:
            valid_id(run_id)
        vector = None
        if returns is not None:
            vector = np.asarray(returns, dtype=float)
            if vector.ndim != 1 or len(vector) == 0 or not np.isfinite(vector).all():
                raise ValueError("Observed return vectors must be a non-empty finite 1-D array; "
                                 "use returns=None only for an unobserved attempt")
        self.check_hypothesis_budget(hypothesis_id)
        base = hashlib.sha256(canonical_json([hypothesis_id, contract_id, kind, stage, outcome, config_hash,
                                              normalise_module(strategy_module)]).encode()).hexdigest()[:16]
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
        return attempt_id

    def validation_attempt_count(self, contract_id):
        """Every consumed attempt: reservations, trials, and unshadowed selection attempts."""
        with self._connect() as conn:
            trials = {r[0] for r in conn.execute("SELECT run_id FROM trials WHERE contract_id=?", (contract_id,))}
            reservations = set()
            for (attempts_json,) in conn.execute("SELECT attempts_json FROM hypotheses WHERE contract_id=?",
                                                 (contract_id,)):
                reservations |= set(json.loads(attempts_json or "{}"))
            attempts = conn.execute("SELECT run_id FROM selection_attempts WHERE contract_id=?",
                                    (contract_id,)).fetchall()
        counted = trials | reservations
        return int(len(counted) + sum(1 for (run_id,) in attempts if not run_id or run_id not in counted))

    def selection_history(self, contract_id, expected_len, exclude_run_id=""):
        """Observed return streams from trials and screens/grids, plus explicit exclusions."""
        with self._connect() as conn:
            trial_rows = conn.execute("SELECT run_id,returns_json FROM trials WHERE contract_id=? ORDER BY created_at,run_id",
                                      (contract_id,)).fetchall()
            attempt_rows = conn.execute(
                "SELECT attempt_id,run_id,returns_json,kind,strategy_family FROM selection_attempts "
                "WHERE contract_id=? AND returns_observed=1 ORDER BY created_at,attempt_id", (contract_id,)).fetchall()
        trial_ids = {row[0] for row in trial_rows}
        streams, excluded = [], []

        def observe(label, raw_json):
            if not raw_json or raw_json == "[]":
                excluded.append({"run_id": label, "reason": "no observed return stream"})
                return
            arr = np.asarray(json.loads(raw_json), dtype=float)
            if float(np.std(arr)) < 1e-12:
                excluded.append({"run_id": label, "reason": "zero-variance stream is not observed returns"})
            elif len(arr) != expected_len:
                raise SelectionHistoryError(
                    f"Unaligned selection history: {label} has {len(arr)} bars but the candidate has "
                    f"{expected_len}; refusing to pad, slice or correlate incompatible windows")
            else:
                streams.append(arr)

        for run_id, raw_json in trial_rows:
            if run_id != exclude_run_id:
                observe(run_id, raw_json)
        for attempt_id, run_id, raw_json, kind, family in attempt_rows:
            if run_id and (run_id in trial_ids or run_id == exclude_run_id):
                continue  # the trial row (or pending candidate) is the authoritative record
            if kind == "standalone_screen" and family == "cross_sectional_ml":
                excluded.append({"run_id": attempt_id, "reason": "stage-1 IC series is not a portfolio return"})
                continue
            observe(attempt_id, raw_json)
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
            row = conn.execute("SELECT contract_id,attempts_json FROM hypotheses WHERE hypothesis_id=?",
                               (result.hypothesis_id,)).fetchone()
            if row is None or row[0] != result.contract_id or json.loads(row[1]).get(result.run_id) != trial_number:
                raise ValueError("Missing or mismatched trial reservation")
            observed = len(returns) > 0
            returns_sha256 = ""
            if observed:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as handle:
                    pl.DataFrame({"net_return": returns}).write_parquet(handle)
            try:
                if observed:
                    returns_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
                trial_dict = {**result.to_dict(), "config_json": canonical_json(config_dict),
                              "returns_json": canonical_json(returns.tolist()),
                              "returns_parquet_path": str(path) if observed else "",
                              "returns_sha256": returns_sha256,
                              "diagnostics_json": canonical_json(diagnostics or result.diagnostics_json),
                              "trial_number_in_hyp": trial_number,
                              "git_commit_sha": config_dict.get("git_commit_sha", ""), "created_at": time.time()}
                _insert(conn, "trials", trial_dict)
            except Exception:
                if observed:
                    path.unlink(missing_ok=True)
                raise
