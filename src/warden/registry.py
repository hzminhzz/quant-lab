"""Durable trial ledger and reference effective-rank DSR; no harness dependencies."""

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import TypedDict

import numpy as np

from warden.codeid import code_closure
from warden.contracts import (
    CURRENT_SPEC_VERSION,
    DEFAULT_SIMULATION_EPOCH,
    EvaluationContract,
    ExperimentResult,
    HypothesisSpec,
    canonical_json,
    contract_row,
    utc,
    valid_id,
)

BLOCKING = frozenset({
    "QUARANTINED_SYNTHETIC",
    "QUARANTINED",
    "REVOKED",
    "SUPERSEDED",
    "RETROSPECTIVE_REPLICATION_ONLY",
})
RUNNABLE_HYPOTHESIS_STATUSES = frozenset({"ACTIVE", "RETROSPECTIVE_REPLICATION_ONLY"})
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


class ContractSelectionGroupError(ValueError):
    """A contract cannot be registered with a policy-resolvable selection group.

    Raised by :meth:`ResearchRegistry.register_contract` /
    :meth:`ResearchRegistry.register_new_contract` when the signed venue policy
    cannot map the contract's ``(asset_class, cadence)`` domain, when a contract's
    declared group contradicts the policy, or when a new contract is registered
    without a policy at all.  The message always names the domain, so the refusal
    is actionable and cannot be mistaken for a per-contract DSR reset.
    """


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
    from ml4t.diagnostic.evaluation.stats import deflated_sharpe_ratio
    from scipy import stats

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
              "k_eff_dedup": 1.0, "k_eff_effective": 1.0 + UNVECTORIZED_PENALTY * unvectorized,
              "k_eff_gate": max(1.0 + UNVECTORIZED_PENALTY * unvectorized, floor),
              "probability": 0.0, "expected_max_sharpe_noise": 0.0, "noise_floor_applied": False,
              "correction_policy": "max(dedup@0.9995 + 0.50/unvectorized, prior_floor)",
              "non_promotable_reason": ""}
    if n < 10 or float(np.std(candidate)) < 1e-12:
        report["non_promotable_reason"] = "candidate return stream is empty or degenerate"
        return report
    matrix = np.column_stack(streams)
    k_dedup = _eigen_rank(matrix[:, distinct_stream_indices(matrix)])
    k_effective = k_dedup + UNVECTORIZED_PENALTY * unvectorized
    k_gate = max(k_effective, floor)
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
                  k_eff_dedup=k_dedup, k_eff_effective=k_effective, k_eff_gate=k_gate, sr_std=sr_std)
    return report


def compute_effective_trials_and_dsr(candidate_returns, historical_return_streams, bars_per_year,
                                     total_trials_n=None, prior_max_keff=1.0, prior_max_noise=0.0):
    """Backward-compatible 4-tuple: (probability, annualised noise SR, raw N, applied k_eff gate)."""
    r = compute_selection_report(candidate_returns, historical_return_streams, bars_per_year,
                                 total_trials_n, prior_max_keff, prior_max_noise)
    return r["probability"], r["expected_max_sharpe_noise"], r["raw_attempt_count"], r["k_eff_gate"]


def normalise_module(module: str | None) -> str:
    return (module or "").strip().removeprefix("./")


def _normalize_epoch(simulation_epoch: str | None) -> str:
    return (simulation_epoch or DEFAULT_SIMULATION_EPOCH).strip() or DEFAULT_SIMULATION_EPOCH


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _columns(cls):
    return ", ".join(
        f"{f.name} {f.metadata.get('sqlite_type') or ('TEXT' if 'str' in str(f.type) or 'datetime' in str(f.type) else 'REAL')}"
        for f in fields(cls))


def _ensure_columns(conn, table, cls):
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for f in fields(cls):
        if f.name not in existing:
            kind = (f.metadata.get("sqlite_type")
                    or ("TEXT" if ("str" in str(f.type) or "datetime" in str(f.type)) else "REAL"))
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {f.name} {kind}")


def _insert(conn, table, values):
    conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                 tuple(values.values()))


SCHEMA_VERSION = 3
"""Current ``PRAGMA user_version`` of the registry schema.

``0`` means "unversioned legacy": the 15 tables and 14 triggers already existed before
the gate, so a v0 database is *adopted* (idempotent ``CREATE ... IF NOT EXISTS`` plus
the additive legacy backfills) rather than rebuilt. ``1`` is the P2 baseline. ``2``
adds ``contract_governance`` (the declared ``selection_group_id`` /
``inherits_selection_from`` per contract) so cross-contract selection scoping is
resolvable without reading contract YAMLs at query time. The addition is a table only:
no existing table, column or trigger changes, and the table has no rows for a legacy
contract, so its scope stays exactly as it was. ``3`` (#113) unifies the contract
schema: it adds the explicit ``contracts.policy`` TEXT column (#94's ``InitVar``
carrier, now a real field) and ``contract_governance.group_source`` (``declared`` vs
``policy``), both by ``ALTER TABLE ... ADD COLUMN`` with a non-destructive default.
Nothing newer than this constant is refused instead of guessed at.

Down-path: v3 is data-only to undo. A v2 build refuses to open a v3 registry (it is
newer than the build), so rolling back means ``PRAGMA user_version=2``; both added
columns are nullable/defaulted and are ignored by v2 readers, and no row is rewritten,
so the rollback is a version flip, not a data migration.
"""


def _split_sql(script: str) -> list[str]:
    """Split a DDL script into complete statements without executing anything.

    ``sqlite3.complete_statement`` understands ``BEGIN ... END`` trigger bodies, so a
    migration can run statement-by-statement inside one explicit transaction instead of
    ``executescript`` (which commits any open transaction first and would leave the DDL
    and the ``user_version`` bump non-atomic).
    """
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        statements.append(buffer.strip())
    return statements


_DDL_SCRIPT = f"""
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
                CREATE TABLE IF NOT EXISTS selection_attempts (attempt_id TEXT PRIMARY KEY,attempt_base TEXT NOT NULL,run_id TEXT DEFAULT '',hypothesis_id TEXT NOT NULL,contract_id TEXT NOT NULL,strategy_family TEXT DEFAULT '',strategy_module TEXT DEFAULT '',kind TEXT NOT NULL,stage TEXT NOT NULL,outcome TEXT NOT NULL,config_hash TEXT DEFAULT '',parameters_json TEXT DEFAULT '{{}}',data_provenance TEXT DEFAULT 'unknown',returns_observed INTEGER NOT NULL DEFAULT 0,returns_json TEXT DEFAULT '',returns_sha256 TEXT DEFAULT '',diagnostics_json TEXT DEFAULT '{{}}',simulation_epoch TEXT DEFAULT '{DEFAULT_SIMULATION_EPOCH}',created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS contract_search_state (contract_id TEXT PRIMARY KEY,max_keff_gate REAL NOT NULL,max_noise REAL NOT NULL,updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS contract_governance (contract_id TEXT PRIMARY KEY,selection_group_id TEXT NOT NULL DEFAULT '',inherits_selection_from TEXT NOT NULL DEFAULT '',spec_version INTEGER NOT NULL DEFAULT 1,group_source TEXT NOT NULL DEFAULT 'declared',recorded_at REAL NOT NULL);
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
"""


class ScreenStatusView(TypedDict):
    """Write-free projection of one Stage 1 receipt."""

    screen_key: str
    state: str
    attempt_id: str
    outcome: str | None
    data_sha256: str
    retry_allowed: bool


class SpecStatusView(TypedDict, total=False):
    """Write-free projection of one spec; ``evidence_verified`` is always ``False``.

    Consumers that must prove artifact bytes still belong on ``spec_status()``, which
    reconciles the Parquet receipt. This view never opens an artifact and never writes.
    """

    state: str
    reason: str
    hypothesis_registered: bool
    hypothesis_status: str | None
    hypothesis_trials_used: int
    audit_verdict: str | None
    receipt_state: str | None
    run_id: str | None
    verdict: str | None
    screen: ScreenStatusView
    evidence_verified: bool


class HoldoutClaimView(TypedDict):
    """Durable one-shot holdout claim, read without acquiring any lock."""

    lineage_id: str
    run_id: str
    state: str
    claimed_at: float
    read_started_at: float | None
    finalized_at: float | None


class TrialSummary(TypedDict):
    """Immutable trial identity and verdict, without artifact re-hashing."""

    run_id: str
    hypothesis_id: str
    contract_id: str
    strategy_module: str
    config_hash: str
    verdict: str | None
    stage_reached: str | None
    created_at: float | None


@dataclass(frozen=True)
class SelectionScope:
    """Which contracts' search evidence counts for one contract.

    ``members`` always contains the contract itself.  ``group_id`` is empty for a legacy
    contract, which keeps the pre-#100 family-wide floor and its own history only.
    ``unresolved`` lists inheritance targets that could not be followed (a cycle or an
    unregistered contract); they are reported, never guessed at.
    """

    contract_id: str
    group_id: str
    members: tuple[str, ...]
    inherited: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    @property
    def scoped_by_group(self) -> bool:
        return bool(self.group_id)


class ResearchRegistry:
    def __init__(self, db_path=Path("registry/registry.db"), *, readonly: bool = False):
        self.db_path = Path(db_path)
        self.readonly = bool(readonly)
        if not self.readonly:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.readonly:
            # Status reads keep the old adapter's contract: a missing registry is reported
            # by the caller's ``is_file()`` guard, not by an exception at construction.
            # Zero DDL, zero migrations, zero writes -- and never create the file.
            self.schema_version = 0
            if self.db_path.is_file():
                with self._connect() as conn:
                    self.schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                    self._refuse_newer_schema()
            return
        with self._connect() as conn:
            self.schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            self._refuse_newer_schema()
            if self.schema_version < SCHEMA_VERSION:
                self._migrate(conn)
                self.schema_version = SCHEMA_VERSION
            self._reconcile_legacy(conn)

    def _refuse_newer_schema(self) -> None:
        if self.schema_version > SCHEMA_VERSION:
            raise ValueError(
                f"Registry schema v{self.schema_version} is newer than this warden build "
                f"(v{SCHEMA_VERSION}); refusing to open it"
            )

    def _migrate(self, conn) -> None:
        """Additive-only schema migration, atomic with the ``user_version`` bump.

        Only additive DDL runs here: ``CREATE ... IF NOT EXISTS`` for every table and
        trigger, plus the ``ALTER TABLE`` column adoption. A crash rolls the DDL *and* the
        version back, so the retry starts from the same v0 snapshot. Existing tables,
        triggers and rows are never rewritten.
        """
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _split_sql(_DDL_SCRIPT):
                conn.execute(statement)
            for table, cls in (("contracts", EvaluationContract), ("hypotheses", HypothesisSpec),
                               ("trials", ExperimentResult)):
                _ensure_columns(conn, table, cls)
            if not _has_column(conn, "selection_attempts", "simulation_epoch"):
                conn.execute("ALTER TABLE selection_attempts ADD COLUMN simulation_epoch TEXT")
            if not _has_column(conn, "contract_governance", "group_source"):
                # #113: distinguish a contract's own declaration from a policy-derived
                # mapping of legacy debt.  Additive, non-null with a default, no row rewrite.
                conn.execute("ALTER TABLE contract_governance ADD COLUMN group_source TEXT NOT NULL DEFAULT 'declared'")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def _reconcile_legacy(self, conn) -> None:
        """Idempotent repair of pre-versioning residue, guarded so a stable registry is quiet.

        These are *data* reconciliations, not schema: a v1 registry cannot legitimately
        acquire v0-era rows, but a legacy snapshot can, so each step probes for the exact
        gap first and writes only when something is genuinely missing. On a registry that
        is already consistent this method performs zero writes (and so never dirties WAL).
        """
        # A prior qualification is irrevocably spent even if the claim table was absent.
        if conn.execute(
            "SELECT 1 FROM qualifications q LEFT JOIN holdout_claims h ON h.lineage_id=q.lineage_id "
            "WHERE q.lineage_id IS NOT NULL AND h.lineage_id IS NULL LIMIT 1"
        ).fetchone():
            conn.execute("""INSERT OR IGNORE INTO holdout_claims (lineage_id,run_id,state,claimed_at,read_started_at,finalized_at)
                            SELECT lineage_id,run_id,'LEGACY_SPENT',COALESCE(qualified_at,0),
                                   COALESCE(qualified_at,0),COALESCE(qualified_at,0)
                              FROM qualifications WHERE lineage_id IS NOT NULL""")
        # Never reset counters or overwrite old verdicts; unfinalized consumed attempts
        # remain review-required.
        if conn.execute(
            "SELECT 1 FROM trials t LEFT JOIN attempt_receipts r ON r.run_id=t.run_id "
            "WHERE r.run_id IS NULL LIMIT 1"
        ).fetchone():
            conn.execute("""INSERT OR IGNORE INTO attempt_receipts
                            (run_id,hypothesis_id,contract_id,trial_number,state,artifact_path,artifact_sha256,reserved_at,finalized_at)
                            SELECT run_id,hypothesis_id,contract_id,COALESCE(trial_number_in_hyp,0),'FINALIZED',
                                   COALESCE(returns_parquet_path,''),COALESCE(returns_sha256,''),
                                   COALESCE(created_at,0),COALESCE(created_at,0) FROM trials""")
        for hyp_id, contract_id, attempts_json in conn.execute(
                "SELECT hypothesis_id,contract_id,attempts_json FROM hypotheses "
                "WHERE attempts_json IS NOT NULL AND attempts_json NOT IN ('', '{}')"):
            for run_id, number in json.loads(attempts_json or "{}").items():
                if conn.execute("SELECT 1 FROM attempt_receipts WHERE run_id=?", (run_id,)).fetchone():
                    continue
                conn.execute("""INSERT OR IGNORE INTO attempt_receipts
                                (run_id,hypothesis_id,contract_id,trial_number,state,reason,reserved_at)
                                VALUES (?,?,?,?,'INTERRUPTED_REVIEW','legacy consumed attempt without trial',0)""",
                             (run_id, hyp_id, contract_id, int(number)))
        # Backfill old screens without replaying them or modifying search debt.
        for sr in conn.execute(
                "SELECT attempt_id,hypothesis_id,contract_id,strategy_module,parameters_json,created_at "
                "FROM selection_attempts WHERE kind='standalone_screen'"):
            sk = self.screen_key(sr[1], sr[2], sr[3], json.loads(sr[4] or '{}'))
            if conn.execute("SELECT 1 FROM screen_receipts WHERE screen_key=?", (sk,)).fetchone():
                continue
            conn.execute("""INSERT OR IGNORE INTO screen_receipts
                            (screen_key,hypothesis_id,contract_id,strategy_module,parameters_json,state,attempt_id,reserved_at,finalized_at)
                            VALUES (?,?,?,?,?,'FINALIZED',?,?,?)""",
                         (sk, sr[1], sr[2], normalise_module(sr[3]), sr[4] or '{}', sr[0],
                          sr[5] or 0, sr[5] or 0))
        for r_id in LEGACY_RUNS:
            if conn.execute("SELECT 1 FROM trial_quarantine WHERE run_id=?", (r_id,)).fetchone():
                continue
            conn.execute("INSERT OR IGNORE INTO trial_quarantine (run_id, reason, quarantined_at) VALUES (?, ?, ?)",
                         (r_id, LEGACY_REASON, time.time()))
        # Ticket 03: attributed, append-only superseding decision for the required regression case.
        legacy = conn.execute("SELECT contract_id, created_at FROM trials WHERE run_id='RUN-ML-001'").fetchone()
        if legacy and not conn.execute(
            "SELECT 1 FROM trial_dispositions WHERE run_id='RUN-ML-001' "
            "AND disposition='QUARANTINED_SYNTHETIC' AND attributed_to='warden:migration:issue-4-03'"
        ).fetchone():
            q = conn.execute("SELECT quarantined_at FROM trial_quarantine WHERE run_id='RUN-ML-001'").fetchone()
            self._append_disposition(
                conn, "RUN-ML-001", "QUARANTINED_SYNTHETIC",
                "Legacy trial recorded synthetic=0 with an empty data_snapshot_sha256, but the registry "
                "quarantines it as synthetic benchmark evidence; a matching digest cannot prove market origin.",
                "warden:migration:issue-4-03", legacy[0], (q[0] if q else legacy[1]) or time.time())

    @contextmanager
    def _connect(self):
        """One connection per unit of work; read-only views never take a writer lock.

        ``readonly=True`` uses a ``mode=ro`` URI plus ``PRAGMA query_only=ON``, so the
        connection can neither create the file, run DDL, nor block on a writer that holds
        ``BEGIN IMMEDIATE`` (WAL readers stay unblocked). The bounded 0.2 s timeout keeps
        a status read from ever turning into a wait for a checkpoint.
        """
        if getattr(self, "readonly", False):
            conn = sqlite3.connect(
                f"file:{self.db_path.resolve()}?mode=ro", uri=True, timeout=0.2
            )
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA query_only=ON")
                yield conn
            finally:
                conn.close()
            return
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
            h_pub = None
            if _has_column(conn, "hypotheses", "source_published_at"):
                h_row = conn.execute("SELECT source_published_at FROM hypotheses WHERE hypothesis_id=?",
                                     (trial.get("hypothesis_id"),)).fetchone()
                h_pub = h_row[0] if h_row else None
            c_row = conn.execute("SELECT val_start FROM contracts WHERE contract_id=?",
                                 (trial.get("contract_id"),)).fetchone()
            c_val_start = c_row[0] if c_row else None
        blocking = [d["disposition"] for d in dispositions if d["disposition"] in BLOCKING]
        if quarantine:
            blocking.append("QUARANTINED")
        is_retrospective = (trial.get("verdict") == "RETROSPECTIVE_REPLICATION_ONLY") or (
            h_pub is not None and c_val_start is not None and utc(h_pub) >= utc(c_val_start)
        )
        provenance = trial.get("data_provenance") or "unknown"
        if provenance == "unknown" and trial.get("synthetic") in (1, 1.0, True):
            provenance = "synthetic"
        receipt = self.reconcile_attempt(run_id)
        checks = [
            (not is_retrospective, "RETROSPECTIVE_REPLICATION_ONLY: source_published_at >= contract.val_start"),
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
                                           config_json,data_snapshot_sha256,strategy_code_sha256
                                    FROM trials WHERE run_id=?""", (run_id,)).fetchone()
            if not trial:
                return {"verified": False, "reason": "run not found", "proof_key": ""}
            h, cid, module, config_hash, config_json, data_sha, trial_code_sha = trial
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
        module_sha, _ = code_closure(root, normalise_module(module))
        if trial_code_sha and trial_code_sha != module_sha:
            return {"verified": False, "reason": "causality proof drift: strategy code closure changed since trial", "proof_key": ""}
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

    # -- registration -------------------------------------------------------

    @staticmethod
    def _venue_policy(venue_policy=None, repo_root=None):
        """Resolve the policy object a registration must obey (``None`` = pre-#113 path)."""
        if venue_policy is not None and repo_root is not None:
            raise ValueError("pass venue_policy or repo_root, not both")
        if venue_policy is not None:
            return venue_policy
        if repo_root is not None:
            from warden_ops.venue_policy import load_venue_policy

            return load_venue_policy(Path(repo_root))
        return None

    @staticmethod
    def _domain(contract) -> str:
        from warden_ops.venue_policy import cadence_bucket

        try:
            cadence = f" at cadence {cadence_bucket(contract.frequency)!r}"
        except ValueError:
            cadence = ""
        return f"asset class {contract.asset_class!r}{cadence} (frequency {contract.frequency!r})"

    def _selection_group_for(self, contract, policy, *, require_new: bool) -> tuple[str, str]:
        """``(group, source)`` a registration must record; fails closed (#113).

        ``source`` is ``"declared"`` (the contract's own v2 governance block, or an
        unpolicy-mapped legacy contract's absence of one) or ``"policy"`` (the signed
        venue policy's mandatory mapping).  When a policy is supplied the policy is
        authoritative: an unmapped domain is refused with the domain named, and a
        declaration that contradicts it is refused rather than re-homed.
        """
        from warden_ops.venue_policy import require_selection_group

        declared = contract.governance.selection_group_id
        declares_governance = contract.governance.declares_governance
        if require_new and not declares_governance:
            raise ContractSelectionGroupError(
                f"cannot register {contract.contract_id} as a new contract: it declares no "
                f"spec_version {CURRENT_SPEC_VERSION} governance block for {self._domain(contract)}")
        if require_new and policy is None:
            raise ContractSelectionGroupError(
                f"cannot register new contract {contract.contract_id} without the signed venue "
                f"policy: no policy-resolvable selection_group_id for {self._domain(contract)}")
        if policy is not None:
            resolved = require_selection_group(policy, contract.asset_class, contract.frequency)
            if declared and declared != resolved:
                raise ContractSelectionGroupError(
                    f"{contract.contract_id} declares selection_group_id {declared!r} but the signed "
                    f"venue policy maps {self._domain(contract)} to {resolved!r}; the policy is "
                    "authoritative and a contract may not re-home itself")
            return resolved, "policy"
        if declares_governance and not declared:
            # Unreachable through ContractPolicy, but a registration must never be the
            # first place a v2 contract's missing group is discovered.
            raise ContractSelectionGroupError(
                f"{contract.contract_id} declares no selection_group_id for {self._domain(contract)} "
                "and no venue policy was supplied to derive one")
        return declared, ("declared" if declared else "")

    def register_contract(self, contract, *, venue_policy=None, repo_root=None) -> dict:
        """Register a contract idempotently, recording its selection group (#113).

        Pass ``venue_policy`` (a :class:`warden_ops.venue_policy.VenuePolicy`) or
        ``repo_root`` to make the signed venue policy authoritative for the
        contract's ``selection_group_id``: an unmapped domain fails closed naming
        the asset class and cadence, and a contradicting declaration is refused.
        Without either, a legacy v1 contract keeps its historical registration and a
        v2 contract keeps its own mandatory declaration.  New contracts belong on
        :meth:`register_new_contract`, which requires the policy.

        Returns the recorded registration (``contract_id``, ``spec_hash``,
        ``selection_group_id``, ``inherits_selection_from``, ``group_source``,
        ``created``).
        """
        return self._register_contract(contract, self._venue_policy(venue_policy, repo_root),
                                       require_new=False)

    def register_new_contract(self, contract, *, venue_policy=None, repo_root=None) -> dict:
        """Register a *new* contract under the signed venue policy's sole authority.

        Every compiled/production contract path must come through here: a v2
        governance block and a policy that maps ``(asset_class, cadence)`` are both
        required, so a sandbox or production contract cannot invent its own group
        and cannot restart the group's DSR debt.
        """
        return self._register_contract(contract, self._venue_policy(venue_policy, repo_root),
                                       require_new=True)

    def _register_contract(self, contract, policy, *, require_new: bool) -> dict:
        group, source = self._selection_group_for(contract, policy, require_new=require_new)
        inherit = contract.governance.inherits_selection_from
        spec_version = contract.governance.spec_version
        with self._connect() as conn:
            row = conn.execute("SELECT spec_hash FROM contracts WHERE contract_id=?", (contract.contract_id,)).fetchone()
            if row and row[0] != contract.spec_hash():
                raise ValueError("Registered contract is immutable; choose a new contract_id")
            created = row is None
            if created:
                _insert(conn, "contracts", contract_row(contract))
            # A contract registered before #113 may still lack its scope row; recording it
            # is additive and never rewrites history (write-once, INSERT OR IGNORE).
            self._record_governance(conn, contract.contract_id, group, inherit, spec_version, source)
            recorded = self._governance_rows(conn).get(str(contract.contract_id), ("", "", ""))
        return {"contract_id": str(contract.contract_id), "spec_hash": contract.spec_hash(),
                "selection_group_id": recorded[0], "inherits_selection_from": recorded[1],
                "group_source": recorded[2], "created": created}

    def register_hypothesis(self, hyp):
        with self._connect() as conn:
            c_row = conn.execute("SELECT val_start FROM contracts WHERE contract_id=?", (hyp.contract_id,)).fetchone()
            if not c_row:
                raise ValueError("Register the contract first")
            existing = conn.execute("SELECT * FROM hypotheses WHERE hypothesis_id=?", (hyp.hypothesis_id,)).fetchone()
            if existing:
                for k, v in asdict(hyp).items():
                    if k == "strategy_module" and not v and existing[k]:
                        continue
                    if existing[k] != v:
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
            pub = hyp.source_published_utc()
            retrospective = pub is not None and pub >= utc(c_row[0])
            initial_status = "RETROSPECTIVE_REPLICATION_ONLY" if retrospective else "ACTIVE"
            _insert(conn, "hypotheses", {**asdict(hyp), "status": initial_status, "created_at": time.time()})

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
                conn.execute(
                    "UPDATE hypotheses SET status='REJECTED' "
                    "WHERE hypothesis_id=? AND status IN ('ACTIVE', 'RETROSPECTIVE_REPLICATION_ONLY')",
                    (hypothesis_id,),
                )
            root, members = self._lineage(conn, hypothesis_id)
            used = sum(r[0] for r in conn.execute(
                f"SELECT trials_used FROM hypotheses WHERE hypothesis_id IN ({','.join('?' * len(members))})",
                tuple(members)))
        if row[0] >= PER_HYPOTHESIS_CAP or row[1] not in RUNNABLE_HYPOTHESIS_STATUSES:
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

    def reconcile_screen(self, hypothesis_id, contract_id, strategy_module, parameters, *,
                         confirm_interrupted: bool = False) -> dict:
        """Project a Stage 1 receipt and durably mark an orphan for review.

        Moving RESERVED -> INTERRUPTED_REVIEW keeps the screen identity consumed:
        the receipt can never be reserved or finalized again, and no trial or
        selection debt is credited back. This is a review transition, not a retry.
        """
        key = self.screen_key(hypothesis_id, contract_id, strategy_module, parameters)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state,attempt_id,data_sha256 FROM screen_receipts WHERE screen_key=?",
                (key,),
            ).fetchone()
            if row is None:
                return {"screen_key": key, "state": "UNSEEN", "reason": "no reservation",
                        "attempt_id": "", "outcome": None, "data_sha256": "",
                        "eligible": False, "retry_allowed": True}
            if confirm_interrupted and row[0] == "RESERVED":
                conn.execute(
                    "UPDATE screen_receipts SET state='INTERRUPTED_REVIEW',finalized_at=? "
                    "WHERE screen_key=? AND state='RESERVED'",
                    (time.time(), key),
                )
                row = conn.execute(
                    "SELECT state,attempt_id,data_sha256 FROM screen_receipts WHERE screen_key=?",
                    (key,),
                ).fetchone()
            attempt = (conn.execute("SELECT outcome FROM selection_attempts WHERE attempt_id=?",
                                    (row[1],)).fetchone() if row[1] else None)
        state = row[0]
        reason = {
            "RESERVED": "screen compute uncommitted; reconcile after writer exits, never auto retry",
            "INTERRUPTED_REVIEW": "interrupted before committed screen result; review, never replay",
            "FINALIZED": "screen result committed",
        }.get(state, "screen receipt requires review")
        if state == "FINALIZED" and attempt is None:
            state, reason = "EVIDENCE_DRIFT", "finalized receipt has no immutable selection attempt"
        return {"screen_key": key, "state": state, "reason": reason,
                "attempt_id": row[1] or "", "outcome": attempt[0] if attempt else None,
                "data_sha256": row[2] or "", "eligible": state == "FINALIZED",
                "retry_allowed": state == "UNSEEN"}

    def spec_status(self, hypothesis_id, contract_id, strategy_module, parameters) -> dict:
        """One read-only projection for queue restart, without running research."""
        module, config = normalise_module(strategy_module), canonical_json(parameters or {})
        config_hash = hashlib.sha256(config.encode()).hexdigest()[:12]
        screen = self.screen_status(hypothesis_id, contract_id, module, parameters)
        with self._connect() as conn:
            hyp_row = conn.execute(
                "SELECT status,trials_used FROM hypotheses WHERE hypothesis_id=?",
                (hypothesis_id,),
            ).fetchone()
            trial = conn.execute("""SELECT run_id,verdict FROM trials WHERE hypothesis_id=? AND
                                   contract_id=? AND strategy_module=? AND config_hash=?
                                   ORDER BY created_at DESC LIMIT 1""",
                                 (hypothesis_id, contract_id, module, config_hash)).fetchone()
            pending = conn.execute("""SELECT run_id,state FROM attempt_receipts WHERE
                                      hypothesis_id=? AND contract_id=? AND state!='FINALIZED'
                                      ORDER BY reserved_at DESC LIMIT 1""",
                                   (hypothesis_id, contract_id)).fetchone()
            audit_row = (
                conn.execute(
                    "SELECT verdict FROM audit_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                    (trial[0],),
                ).fetchone()
                if trial
                else None
            )
        hyp_registered = hyp_row is not None
        hyp_status = hyp_row[0] if hyp_row else None
        hyp_trials_used = int(hyp_row[1]) if hyp_row else 0
        audit_verdict = audit_row[0] if audit_row else None
        base = {
            "hypothesis_registered": hyp_registered,
            "hypothesis_status": hyp_status,
            "hypothesis_trials_used": hyp_trials_used,
            "audit_verdict": audit_verdict,
            "receipt_state": None,
            "screen": screen,
        }
        if pending:
            return {
                **base,
                "state": "REQUIRES_RECONCILIATION",
                "reason": f"trial {pending[0]} is {pending[1]}",
                "run_id": pending[0],
                "receipt_state": pending[1],
            }
        if trial:
            receipt = self.reconcile_attempt(trial[0])
            base["receipt_state"] = receipt["state"]
            if receipt["state"] != "FINALIZED" or trial[1] == "FAILED":
                return {
                    **base,
                    "state": "REQUIRES_RECONCILIATION",
                    "reason": receipt["reason"] or f"trial {trial[1]}",
                    "run_id": trial[0],
                }
            if trial[1] == "REJECTED_EARLY":
                return {
                    **base,
                    "state": "STAGE1_REJECT",
                    "reason": "immutable early-rejection trial recorded",
                    "run_id": trial[0],
                    "verdict": trial[1],
                }
            if trial[1] == "CANDIDATE_FOR_QUALIFICATION" and audit_verdict is None:
                return {
                    **base,
                    "state": "STAGE3_CANDIDATE_UNAUDITED",
                    "reason": "finalized candidate trial awaiting six-check audit",
                    "run_id": trial[0],
                    "verdict": trial[1],
                }
            return {
                **base,
                "state": "STAGE3_FINAL",
                "reason": "immutable Warden trial recorded",
                "run_id": trial[0],
                "verdict": trial[1],
            }
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
        return {**base, "state": state, "reason": screen["state"], "run_id": None}

    # --- typed, write-free read API for preflight/campaign/closure consumers ----

    def read_screen_status(self, hypothesis_id, contract_id, strategy_module,
                           parameters) -> ScreenStatusView:
        """Stage 1 receipt projection built only from SELECTs (safe on a readonly view)."""
        key = self.screen_key(hypothesis_id, contract_id, strategy_module, parameters)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state,attempt_id,data_sha256 FROM screen_receipts WHERE screen_key=?",
                (key,),
            ).fetchone()
            attempt = (
                conn.execute(
                    "SELECT outcome FROM selection_attempts WHERE attempt_id=?", (row[1],)
                ).fetchone()
                if row and row[1]
                else None
            )
        state: str = row[0] if row else "UNSEEN"
        if state == "FINALIZED" and attempt is None:
            state = "EVIDENCE_DRIFT"
        return {
            "screen_key": key,
            "state": state,
            "attempt_id": (row[1] or "") if row else "",
            "outcome": attempt[0] if attempt else None,
            "data_sha256": (row[2] or "") if row else "",
            "retry_allowed": state == "UNSEEN",
        }

    def read_spec_status(self, hypothesis_id, contract_id, strategy_module,
                         parameters) -> SpecStatusView:
        """Write-free spec projection for consumers that must not take a writer lock.

        Unlike :meth:`spec_status` this never opens a Parquet artifact and never
        reconciles it, so ``evidence_verified`` is always ``False``: a caller that needs
        byte-level evidence proof must still call :meth:`spec_status`/``reconcile_attempt``.
        """
        module = normalise_module(strategy_module)
        config_hash = hashlib.sha256(canonical_json(parameters or {}).encode()).hexdigest()[:12]
        screen = self.read_screen_status(hypothesis_id, contract_id, module, parameters)
        with self._connect() as conn:
            hyp_row = conn.execute(
                "SELECT status,trials_used FROM hypotheses WHERE hypothesis_id=?",
                (hypothesis_id,),
            ).fetchone()
            trial = conn.execute(
                """SELECT run_id,verdict FROM trials WHERE hypothesis_id=? AND
                   contract_id=? AND strategy_module=? AND config_hash=?
                   ORDER BY created_at DESC LIMIT 1""",
                (hypothesis_id, contract_id, module, config_hash),
            ).fetchone()
            pending = conn.execute(
                """SELECT run_id,state FROM attempt_receipts WHERE
                   hypothesis_id=? AND contract_id=? AND state!='FINALIZED'
                   ORDER BY reserved_at DESC LIMIT 1""",
                (hypothesis_id, contract_id),
            ).fetchone()
            audit_row = (
                conn.execute(
                    "SELECT verdict FROM audit_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                    (trial[0],),
                ).fetchone()
                if trial
                else None
            )
            receipt_state = (
                conn.execute(
                    "SELECT state FROM attempt_receipts WHERE run_id=?", (trial[0],)
                ).fetchone()
                if trial
                else None
            )
        base: SpecStatusView = {
            "hypothesis_registered": hyp_row is not None,
            "hypothesis_status": hyp_row[0] if hyp_row else None,
            "hypothesis_trials_used": int(hyp_row[1]) if hyp_row else 0,
            "audit_verdict": audit_row[0] if audit_row else None,
            "receipt_state": (receipt_state[0] if receipt_state else None),
            "screen": screen,
            "evidence_verified": False,
            "run_id": None,
            "verdict": None,
        }
        if pending:
            return {**base, "state": "REQUIRES_RECONCILIATION",
                    "reason": f"trial {pending[0]} is {pending[1]}",
                    "run_id": pending[0], "receipt_state": pending[1]}
        if trial:
            verdict = trial[1]
            if base["receipt_state"] != "FINALIZED" or verdict == "FAILED":
                return {**base, "state": "REQUIRES_RECONCILIATION",
                        "reason": f"attempt receipt {base['receipt_state']} for {trial[0]}",
                        "run_id": trial[0], "verdict": verdict}
            if verdict == "REJECTED_EARLY":
                return {**base, "state": "STAGE1_REJECT",
                        "reason": "immutable early-rejection trial recorded",
                        "run_id": trial[0], "verdict": verdict}
            if verdict == "CANDIDATE_FOR_QUALIFICATION" and base["audit_verdict"] is None:
                return {**base, "state": "STAGE3_CANDIDATE_UNAUDITED",
                        "reason": "finalized candidate trial awaiting six-check audit",
                        "run_id": trial[0], "verdict": verdict}
            return {**base, "state": "STAGE3_FINAL",
                    "reason": "immutable Warden trial recorded",
                    "run_id": trial[0], "verdict": verdict}
        if screen["state"] == "FINALIZED":
            state = {"PASS": "STAGE1_PASS", "REJECT": "STAGE1_REJECT"}.get(
                str(screen["outcome"]), "REQUIRES_RECONCILIATION")
        elif screen["state"] == "UNSEEN":
            state = "NEW"
        else:
            state = "REQUIRES_RECONCILIATION"
        return {**base, "state": state, "reason": screen["state"]}

    def read_holdout_claim(self, lineage_id: str) -> HoldoutClaimView | None:
        """The durable one-shot claim, or ``None``; never writes or releases it."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT lineage_id,run_id,state,claimed_at,read_started_at,finalized_at "
                "FROM holdout_claims WHERE lineage_id=?",
                (lineage_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "lineage_id": row[0], "run_id": row[1], "state": row[2],
            "claimed_at": float(row[3]), "read_started_at": row[4], "finalized_at": row[5],
        }

    def read_trial(self, run_id: str) -> TrialSummary | None:
        """Immutable trial identity/verdict without re-hashing its artifact."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run_id,hypothesis_id,contract_id,strategy_module,config_hash,"
                "verdict,stage_reached,created_at FROM trials WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0], "hypothesis_id": row[1], "contract_id": row[2],
            "strategy_module": normalise_module(row[3]), "config_hash": row[4] or "",
            "verdict": row[5], "stage_reached": row[6], "created_at": row[7],
        }

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
            if row[0] >= PER_HYPOTHESIS_CAP or row[1] not in RUNNABLE_HYPOTHESIS_STATUSES:
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
                                 data_provenance="unknown", returns=None, diagnostics=None, run_id="",
                                 screen_key=None, simulation_epoch: str = DEFAULT_SIMULATION_EPOCH):
        """Persist one validation-consuming configuration. Repeats are counted, never merged."""
        epoch = _normalize_epoch(simulation_epoch)
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
                "returns_observed,returns_json,returns_sha256,diagnostics_json,simulation_epoch,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (attempt_id, base, run_id, hypothesis_id, contract_id, strategy_family,
                 normalise_module(strategy_module), kind, stage, outcome, config_hash,
                 canonical_json(parameters or {}), data_provenance, int(vector is not None), payload,
                 hashlib.sha256(payload.encode()).hexdigest() if vector is not None else "",
                 canonical_json(diagnostics or {}), epoch, time.time()))
            if screen_key is not None:
                updated = conn.execute("""UPDATE screen_receipts SET state='FINALIZED',attempt_id=?,finalized_at=?
                                          WHERE screen_key=? AND hypothesis_id=? AND contract_id=? AND state='RESERVED'""",
                                       (attempt_id, time.time(), screen_key, hypothesis_id, contract_id)).rowcount
                if updated != 1:
                    raise ScreenAlreadyAttemptedError("No matching exclusive Stage 1 reservation to finalize")
        return attempt_id

    @staticmethod
    def _epoch_clause(conn: sqlite3.Connection, table: str, epoch: str) -> tuple[str, tuple[str, ...]]:
        if _has_column(conn, table, "simulation_epoch"):
            return " AND COALESCE(NULLIF(simulation_epoch,''),?)=?", (DEFAULT_SIMULATION_EPOCH, epoch)
        if epoch == DEFAULT_SIMULATION_EPOCH:
            return "", ()
        return " AND 1=0", ()

    def validation_attempt_count(self, contract_id, simulation_epoch: str | None = None):
        with self._connect() as conn:
            if simulation_epoch is None:
                trials = conn.execute("SELECT COUNT(*) FROM trials WHERE contract_id=?", (contract_id,)).fetchone()[0]
                screens = conn.execute("SELECT COUNT(*) FROM selection_attempts WHERE contract_id=?",
                                       (contract_id,)).fetchone()[0]
                return int(trials + screens)
            epoch = _normalize_epoch(simulation_epoch)
            t_clause, t_params = self._epoch_clause(conn, "trials", epoch)
            s_clause, s_params = self._epoch_clause(conn, "selection_attempts", epoch)
            trials = conn.execute(
                f"SELECT COUNT(*) FROM trials WHERE contract_id=?{t_clause}",
                (contract_id, *t_params),
            ).fetchone()[0]
            screens = conn.execute(
                f"SELECT COUNT(*) FROM selection_attempts WHERE contract_id=?{s_clause}",
                (contract_id, *s_params),
            ).fetchone()[0]
        return int(trials + screens)

    # -- selection scope ----------------------------------------------------

    @staticmethod
    def _declared_governance(contract) -> tuple[str, str]:
        """The contract's *own* declared group/inheritance; never a manifest-resolved one.

        ``evaluation`` manifests carry lifecycle metadata, and reading it here would make
        scope depend on a file the registry does not own.  A legacy contract therefore has
        no declared scope and keeps its historical family-wide floor.
        """
        policy = getattr(contract, "governance", None)
        if policy is None:
            return "", ""
        return (str(getattr(policy, "selection_group_id", "") or "").strip(),
                str(getattr(policy, "inherits_selection_from", "") or "").strip())

    def _governance_rows(self, conn) -> dict[str, tuple[str, str, str]]:
        """``contract_id -> (selection_group_id, inherits_selection_from, group_source)``."""
        try:
            rows = conn.execute(
                "SELECT contract_id,selection_group_id,inherits_selection_from,"
                "COALESCE(group_source,'declared') "
                "FROM contract_governance").fetchall()
        except sqlite3.OperationalError:
            # A pre-#100 snapshot opened read-only keeps its legacy semantics: no table,
            # no rows, no scope -- and never a write to add one.
            return {}
        return {str(row[0]): (str(row[1] or ""), str(row[2] or ""), str(row[3] or "declared"))
                for row in rows}

    def recorded_policy(self, contract_id) -> dict | None:
        """The governance block persisted with a registered contract (#113), read-only.

        ``None`` when the contract is unknown or is a legacy v1 contract (the column
        is empty).  This is the ledger's own copy, not a re-read of the contract YAML.
        """
        with self._connect() as conn:
            try:
                row = conn.execute("SELECT policy FROM contracts WHERE contract_id=?",
                                   (str(contract_id),)).fetchone()
            except sqlite3.OperationalError:
                return None
        if not row or not str(row[0] or "").strip():
            return None
        return json.loads(str(row[0]))

    def selection_group_debt(self, contract, *,
                             simulation_epoch: str = DEFAULT_SIMULATION_EPOCH) -> dict:
        """Persisted trial/search-debt count for the whole selection group (#113).

        Read-only: sums ``validation_attempt_count`` over every member of the
        contract's scope, so two contract ids in one group -- including one whose
        membership was derived from the venue policy -- report a single shared
        count instead of one each.
        """
        epoch = _normalize_epoch(simulation_epoch)
        scope = self.selection_scope(contract)
        per_contract = {member: self.validation_attempt_count(member, simulation_epoch=epoch)
                        for member in scope.members}
        return {"contract_id": str(contract.contract_id), "selection_group_id": scope.group_id,
                "scope": list(scope.members), "per_contract": per_contract,
                "shared_attempt_count": int(sum(per_contract.values())),
                "simulation_epoch": epoch}

    def selection_scope(self, contract) -> SelectionScope:
        """Contracts whose search evidence counts for ``contract``.

        A contract that declares ``selection_group_id`` is scoped to that group plus its
        ``inherits_selection_from`` chain (and those targets' groups, because inheriting a
        contract inherits the selection problem it belongs to).  Every contract the
        registry records in that group counts, including a legacy contract whose
        membership was mapped from the venue policy (#113).  A legacy contract has no
        declared group and is scoped to itself; its floors still fall back to the
        ``asset_class:frequency`` family, exactly as before #100 -- a policy-derived
        mapping joins the *group's* evidence but never narrows the legacy contract's own
        family floor, which could otherwise drop a hurdle.
        """
        own = str(contract.contract_id)
        group, inherit = self._declared_governance(contract)
        if not group and not inherit:
            return SelectionScope(own, "", (own,), (), ())
        from warden_evidence.selection_alignment import MAX_INHERITANCE_DEPTH

        with self._connect() as conn:
            recorded = self._governance_rows(conn)
        if not recorded:
            # A pre-#100 registry (or an unmigrated read-only snapshot) has no declared
            # scope at all.  Falling back to the legacy family scope is the conservative
            # reading: it can only widen the debt, never shrink it.
            return SelectionScope(own, "", (own,), (), ())
        if not group:
            # Only a *declared* row can stand in for a contract's own missing declaration
            # (a v1-shaped projection of a registered v2 contract).  A policy-mapped row
            # must not re-home a legacy contract and shrink its family floor.
            recorded_group, _, source = recorded.get(own, ("", "", ""))
            if source == "declared":
                group = recorded_group
        members: set[str] = {own}
        if group:
            members.update(cid for cid, (declared, _, _) in recorded.items() if declared == group)
        inherited: list[str] = []
        unresolved: list[str] = []
        seen = {own}
        target = inherit
        while target:
            if target in seen:
                unresolved.append(target)
                break
            if len(inherited) >= MAX_INHERITANCE_DEPTH:
                raise SelectionHistoryError(
                    f"inherits_selection_from chain from '{own}' exceeds "
                    f"{MAX_INHERITANCE_DEPTH} hops; refusing to guess a floor")
            seen.add(target)
            inherited.append(target)
            members.add(target)
            declared, onward, _ = recorded.get(target, ("", "", ""))
            if declared:
                members.update(cid for cid, (group_id, _, _) in recorded.items()
                               if group_id == declared)
            target = onward
        return SelectionScope(own, group, tuple(sorted(members)), tuple(inherited),
                              tuple(unresolved))

    def _record_governance(self, conn, contract_id, group: str, inherit: str,
                           spec_version: int, source: str) -> None:
        """Write-once record of a contract's selection-group membership (#113).

        ``group_source`` says *why* the row exists: ``declared`` (the contract's own v2
        governance block) or ``policy`` (the signed venue policy mapped this legacy
        contract into the domain's group).  ``INSERT OR IGNORE`` over a primary key, and
        the registry exposes no update or delete path: a group cannot be silently
        re-homed.  This table deliberately carries no triggers -- the migration family
        pins the pre-#100 trigger set exactly, and additive tables/columns are the only
        schema changes that leave it untouched.
        """
        if not group and not inherit:
            return
        conn.execute(
            "INSERT OR IGNORE INTO contract_governance "
            "(contract_id,selection_group_id,inherits_selection_from,spec_version,group_source,recorded_at) "
            "VALUES (?,?,?,?,?,?)",
            (str(contract_id), str(group or ""), str(inherit or ""),
             int(spec_version or 1), str(source or "declared"), time.time()))

    def _recorded_policies(self, conn) -> dict[str, dict]:
        """``contract_id -> persisted governance block`` for rows that carry one."""
        if not _has_column(conn, "contracts", "policy"):
            return {}
        out: dict[str, dict] = {}
        for contract_id, payload in conn.execute(
                "SELECT contract_id, policy FROM contracts WHERE policy IS NOT NULL AND policy != ''"):
            try:
                parsed = json.loads(str(payload))
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed:
                out[str(contract_id)] = parsed
        return out

    def map_selection_groups(self, *, venue_policy=None, repo_root=None, dry_run: bool = False) -> dict:
        """Map registered contracts without a scope row onto their policy group (#113).

        Additive by construction: it only ``INSERT OR IGNORE``s into
        ``contract_governance`` (a table added by the v2 migration), never touches
        ``contracts``, ``trials`` or ``selection_attempts``, and never rewrites an
        existing row.  A contract that declares its own group is recorded as
        ``declared``; a legacy contract is recorded as ``policy`` from the signed venue
        policy's ``(asset_class, cadence)`` mapping, which is how pre-existing trial
        debt joins its domain's group.  A domain the policy does not map is *reported*
        in ``unmapped`` with the domain named -- never guessed.

        Pass ``dry_run=True`` to compute the report without writing.
        """
        policy = self._venue_policy(venue_policy, repo_root)
        if policy is None:
            raise ContractSelectionGroupError(
                "mapping selection groups requires the signed venue policy: pass venue_policy "
                "or repo_root (there is no default mapping)")
        from warden_ops.venue_policy import SelectionGroupResolutionError, require_selection_group

        mapped: list[dict] = []
        already: list[str] = []
        unmapped: list[dict] = []
        with self._connect() as conn:
            recorded = self._governance_rows(conn)
            persisted = self._recorded_policies(conn)
            rows = conn.execute(
                "SELECT contract_id, asset_class, frequency FROM contracts ORDER BY contract_id").fetchall()
            for raw_id, asset_class, frequency in rows:
                contract_id = str(raw_id)
                if contract_id in recorded:
                    already.append(contract_id)
                    continue
                block = persisted.get(contract_id, {})
                declared = str(block.get("selection_group_id") or "").strip()
                if declared:
                    group, source, spec_version = declared, "declared", int(block.get("spec_version") or 1)
                    inherit = str(block.get("inherits_selection_from") or "").strip()
                else:
                    try:
                        group = require_selection_group(policy, str(asset_class), str(frequency))
                    except SelectionGroupResolutionError as exc:
                        unmapped.append({"contract_id": contract_id, "asset_class": str(asset_class),
                                         "frequency": str(frequency), "reason": str(exc)})
                        continue
                    source, spec_version, inherit = "policy", 1, ""
                if not dry_run:
                    self._record_governance(conn, contract_id, group, inherit, spec_version, source)
                mapped.append({"contract_id": contract_id, "selection_group_id": group,
                               "group_source": source, "spec_version": spec_version})
        return {"policy_sha256": str(getattr(policy, "sha256", "")), "dry_run": bool(dry_run),
                "mapped": mapped, "already_recorded": already, "unmapped": unmapped}

    def _artifact_timestamps(self, path) -> np.ndarray | None:
        """UTC nanoseconds from the trial's own artifact, or ``None`` when it has none.

        A missing or legacy artifact proves no clock.  That is reported as ``None`` so the
        stream is accounted as debt; it is never replaced by an inferred index.
        """
        if not path:
            return None
        from warden_evidence.return_artifacts import ReturnArtifactError, read_return_stream

        try:
            return read_return_stream(Path(path), self.db_path.parent / "returns").timestamps_ns
        except (ReturnArtifactError, OSError):
            return None

    def _selection_rows(self, members: tuple[str, ...], epoch: str):
        marks = ",".join("?" * len(members))
        with self._connect() as conn:
            t_clause, t_params = self._epoch_clause(conn, "trials", epoch)
            s_clause, s_params = self._epoch_clause(conn, "selection_attempts", epoch)
            rows = conn.execute(
                "SELECT run_id,contract_id,returns_json,stage_reached,returns_parquet_path "
                f"FROM trials WHERE contract_id IN ({marks}){t_clause} ORDER BY created_at,run_id",
                (*members, *t_params)).fetchall()
            attempts = conn.execute(
                "SELECT attempt_id,contract_id,returns_json FROM selection_attempts "
                f"WHERE contract_id IN ({marks}) AND returns_observed=1 "
                "AND (kind='param_grid' OR (kind='standalone_screen' AND strategy_family IN ('rules_ts', 'meta_overlay')))"
                f"{s_clause} ORDER BY created_at,attempt_id",
                (*members, *s_params)).fetchall()
        return rows, attempts

    def _selection_streams(self, scope: SelectionScope, epoch: str) -> tuple[list, list[dict]]:
        """Every vectorised selection stream in scope, plus the explicit debt register."""
        from warden_evidence.selection_alignment import ReturnStream

        rows, attempts = self._selection_rows(scope.members, epoch)
        streams: list[ReturnStream] = []
        excluded: list[dict] = []
        for run_id, contract_id, r_json, stage, path in rows:
            if stage == "STAGE_1_SIGNAL" or not r_json or r_json == "[]":
                excluded.append({"run_id": run_id, "reason": "no observed return stream"})
                continue
            arr = np.array(json.loads(r_json), dtype=float)
            if arr.ndim != 1 or not np.isfinite(arr).all() or float(np.std(arr)) < 1e-12:
                excluded.append({"run_id": run_id,
                                 "reason": "zero-variance stream is not observed returns"})
                continue
            stamps = self._artifact_timestamps(path)
            if stamps is not None and stamps.size != arr.size:
                excluded.append({"run_id": run_id,
                                 "reason": "artifact timestamps disagree with the recorded return vector"})
                continue
            own = str(contract_id) == scope.contract_id
            streams.append(ReturnStream(
                stream_id=str(run_id), returns=arr, timestamps_ns=stamps,
                contract_id=str(contract_id), origin="trial",
                required=own, legacy_positional=own))
        for attempt_id, contract_id, r_json in attempts:
            if not r_json or r_json == "[]":
                excluded.append({"attempt_id": attempt_id, "reason": "no observed return stream"})
                continue
            arr = np.array(json.loads(r_json), dtype=float)
            if arr.ndim != 1 or not np.isfinite(arr).all() or float(np.std(arr)) < 1e-12:
                excluded.append({"attempt_id": attempt_id,
                                 "reason": "zero-variance or non-finite selection_attempt stream"})
                continue
            # Selection attempts store a vector only: they have no artifact and therefore
            # no clock.  They stay positional evidence for their own contract and debt
            # everywhere else.
            streams.append(ReturnStream(
                stream_id=str(attempt_id), returns=arr, timestamps_ns=None,
                contract_id=str(contract_id), origin="attempt", required=False,
                legacy_positional=str(contract_id) == scope.contract_id))
        return streams, excluded

    def selection_history(
        self,
        contract_id,
        expected_len,
        simulation_epoch: str = DEFAULT_SIMULATION_EPOCH,
        *,
        timestamps=None,
        candidate_returns=None,
        contract=None,
    ):
        """Observed return streams plus explicit accounting of unvectorised attempts.

        Pass ``timestamps`` *and* ``candidate_returns`` (the candidate's own vector) to
        align the history on the candidate's clock.  The returned
        ``candidate_returns`` is the candidate restricted/compounded onto the same index
        as ``streams``; when ``alignment.frequency`` is not ``bar`` the caller must feed
        that vector, not its raw one, to ``compute_selection_report``.

        Pass ``contract`` to adopt its declared ``selection_group_id`` scope.  Without it
        only this contract's own evidence counts, which is the pre-#100 behaviour.
        """
        from warden_evidence.selection_alignment import AlignmentError, align_streams

        epoch = _normalize_epoch(simulation_epoch)
        if (timestamps is None) != (candidate_returns is None):
            raise ValueError(
                "timestamps and candidate_returns must be supplied together: an index "
                "without the vector it indexes cannot be aligned, and a vector without an "
                "index cannot be placed on one")
        if contract is not None and str(contract.contract_id) != str(contract_id):
            raise ValueError(
                f"contract '{contract.contract_id}' does not match contract_id '{contract_id}'")
        scope = (self.selection_scope(contract) if contract is not None
                 else SelectionScope(str(contract_id), "", (str(contract_id),), (), ()))
        streams, excluded = self._selection_streams(scope, epoch)

        candidate = (np.asarray(candidate_returns, dtype=float) if candidate_returns is not None
                     else np.zeros(int(expected_len), dtype=float))
        try:
            aligned = align_streams(streams, candidate, timestamps, expected_len=int(expected_len))
        except AlignmentError as exc:
            raise SelectionHistoryError(str(exc).replace(
                "Unaligned selection history", "Unaligned selection history")) from exc
        aligned_streams = [aligned.matrix[:, index] for index in range(aligned.matrix.shape[1])]
        excluded.extend(item.to_dict() for item in aligned.excluded)
        raw = sum(self.validation_attempt_count(member, simulation_epoch=epoch)
                  for member in scope.members)
        return {
            "streams": aligned_streams,
            "excluded": excluded,
            "raw_attempt_count": raw,
            # Everything counted but not vectorised, including legacy streams whose clock
            # cannot be proved: 0.5 effective attempts each, never a silent drop.
            "unvectorized_attempts": max(0, raw - len(aligned_streams)),
            "simulation_epoch": epoch,
            "candidate_returns": aligned.candidate if candidate_returns is not None else None,
            "alignment": {**aligned.to_dict(),
                          "scope": list(scope.members),
                          "selection_group_id": scope.group_id,
                          "inherited": list(scope.inherited)},
        }

    def preflight_selection_alignment(
        self,
        contract,
        timestamps,
        *,
        candidate_returns=None,
        expected_len: int | None = None,
        simulation_epoch: str = DEFAULT_SIMULATION_EPOCH,
    ) -> dict:
        """Refuse an incomparable selection history *before* a trial slot is reserved.

        Read-only: no reservation, no trial row, no attempt row, no search-floor write.
        Returns the aligned index description so the caller can log what it will compare.
        """
        from warden_evidence.selection_alignment import AlignmentError, check_alignment

        epoch = _normalize_epoch(simulation_epoch)
        scope = self.selection_scope(contract)
        streams, excluded = self._selection_streams(scope, epoch)
        vector = (np.asarray(candidate_returns, dtype=float) if candidate_returns is not None
                  else np.zeros(int(expected_len if expected_len is not None else len(timestamps)),
                                dtype=float))
        try:
            aligned = check_alignment(
                streams, vector, timestamps,
                expected_len=int(expected_len if expected_len is not None else vector.size))
        except AlignmentError as exc:
            raise SelectionHistoryError(
                f"Pre-reservation alignment refused for '{scope.contract_id}': {exc}") from exc
        return {**aligned.to_dict(), "excluded": excluded + aligned.to_dict()["excluded"],
                "scope": list(scope.members), "selection_group_id": scope.group_id,
                "inherited": list(scope.inherited), "simulation_epoch": epoch}

    def parent_validation_stream(
        self,
        parent_hypothesis_id: str | None,
        contract_id: str,
        simulation_epoch: str | None = None,
    ) -> dict | None:
        """Return the latest recorded Stage 2/3 validation stream for ``parent_hypothesis_id``."""
        if not parent_hypothesis_id:
            return None
        epoch = _normalize_epoch(simulation_epoch) if simulation_epoch is not None else None
        with self._connect() as conn:
            clause, params = (
                self._epoch_clause(conn, "trials", epoch) if epoch is not None else ("", ())
            )
            row = conn.execute(
                "SELECT run_id, returns_json, net_sharpe, annualized_turnover, "
                "max_drawdown_pct, cvar_99_pct, simulation_epoch, data_snapshot_sha256 "
                "FROM trials WHERE hypothesis_id=? AND contract_id=? "
                "AND stage_reached IN ('STAGE_2_BACKTEST', 'STAGE_3_STRESS') "
                "AND verdict NOT IN ('FAILED', 'REJECTED_EARLY')"
                f"{clause} ORDER BY created_at DESC, run_id DESC LIMIT 1",
                (parent_hypothesis_id, contract_id, *params),
            ).fetchone()
        if not row or not row[1]:
            return None
        try:
            arr = np.asarray(json.loads(row[1]), dtype=float)
        except (TypeError, ValueError):
            return None
        if arr.ndim != 1 or len(arr) == 0 or not np.isfinite(arr).all():
            return None
        return {
            "run_id": str(row[0]),
            "net_returns": arr,
            "net_sharpe": float(row[2] or 0.0),
            "annualized_turnover": float(row[3] or 0.0),
            "max_drawdown_pct": float(row[4] or 0.0),
            "cvar_99_pct": float(row[5] or 0.0),
            "simulation_epoch": str(row[6] or DEFAULT_SIMULATION_EPOCH),
            "data_snapshot_sha256": str(row[7] or ""),
        }

    def prior_search_floor(self, contract: EvaluationContract):
        """Historical floor for this contract's selection problem.

        Scoped by ``selection_group_id`` plus ``inherits_selection_from`` when the contract
        declares them, so re-homing a strategy onto a narrower contract in the same group
        inherits the higher DSR floor instead of restarting the search.  A legacy contract
        declares neither, keeps the historical ``asset_class:frequency`` family fallback,
        and its stored floor is still a ``MAX`` over every prior row: nothing here can
        lower a floor, and ``contract_search_state`` refuses a relaxing write anyway.
        """
        scope = self.selection_scope(contract)
        family = scope.group_id or f"{contract.asset_class}:{contract.frequency}"
        marks = ",".join("?" * len(scope.members))
        with self._connect() as conn:
            state = conn.execute(
                f"SELECT MAX(s.max_keff_gate),MAX(s.max_noise) FROM contract_search_state s "
                f"WHERE s.contract_id IN ({marks})", scope.members).fetchone()
            trial_max = conn.execute(
                f"SELECT MAX(effective_trials_keff) FROM trials WHERE contract_id IN ({marks})",
                scope.members).fetchone()[0]
            if not scope.scoped_by_group:
                # Pre-#100 behaviour, byte for byte: a later contract version of the same
                # market/frequency inherits the family's stored floor.
                family_state = conn.execute(
                    "SELECT MAX(s.max_keff_gate),MAX(s.max_noise) FROM contract_search_state s "
                    "JOIN contracts c ON c.contract_id=s.contract_id "
                    "WHERE s.contract_id=? OR (c.asset_class=? AND c.frequency=?)",
                    (contract.contract_id, contract.asset_class, contract.frequency)).fetchone()
                state = (max(float(state[0] or 0.0), float(family_state[0] or 0.0)),
                         max(float(state[1] or 0.0), float(family_state[1] or 0.0)))
                family_trial_max = conn.execute(
                    "SELECT MAX(t.effective_trials_keff) FROM trials t JOIN contracts c "
                    "ON c.contract_id=t.contract_id WHERE c.asset_class=? AND c.frequency=?",
                    (contract.asset_class, contract.frequency)).fetchone()[0]
                trial_max = max(float(trial_max or 0.0), float(family_trial_max or 0.0))
        return max(float(state[0] or 1.0), float(trial_max or 1.0)), float(state[1] or 0.0), family

    def update_search_floor(self, contract_id, k_eff_gate, noise):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO contract_search_state VALUES (?,?,?,?) ON CONFLICT(contract_id) DO UPDATE SET "
                "max_keff_gate=MAX(max_keff_gate,excluded.max_keff_gate),"
                "max_noise=MAX(max_noise,excluded.max_noise),updated_at=excluded.updated_at",
                (contract_id, float(k_eff_gate), float(noise), time.time()))

    def load_prior_trial_returns(self, contract_id, expected_len, simulation_epoch: str = DEFAULT_SIMULATION_EPOCH):
        try:
            return self.selection_history(contract_id, expected_len, simulation_epoch=simulation_epoch)["streams"]
        except SelectionHistoryError as exc:
            raise ValueError(str(exc).replace("Unaligned selection history",
                                              "Contract-wide trial histories are not aligned")) from exc
    def record_trial(self, result, config_dict, net_returns, trial_number=None, diagnostics=None,
                     *, timestamps=None):
        """Persist a trial and its immutable return artifact.

        ``timestamps`` (UTC-aware, one per return) writes the #100 artifact generation:
        ``timestamp`` (``Datetime("us","UTC")``) plus ``net_return`` (``Float64``).  Omitted,
        the artifact keeps the legacy single-column form so an existing reader is unaffected.
        Timestamps are validated *before* anything is written, and a naive clock is refused
        outright: a fabricated index would make two incomparable trials look comparable.
        """
        valid_id(result.run_id)
        valid_id(result.contract_id)
        returns = np.asarray(net_returns, dtype=float)
        if returns.ndim != 1 or not np.isfinite(returns).all():
            raise ValueError("Returns must be a finite vector")
        stamps_ns = None
        if timestamps is not None:
            from warden_evidence.selection_alignment import AlignmentError, utc_ns

            try:
                stamps_ns = utc_ns(timestamps)
            except AlignmentError as exc:
                raise ValueError(f"Invalid trial timestamps: {exc}") from exc
            if stamps_ns.size != returns.size:
                raise ValueError(
                    f"{returns.size} returns but {stamps_ns.size} timestamps; refusing to "
                    "write a misaligned return artifact")
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
                from warden_evidence.return_artifacts import return_frame

                path.parent.mkdir(parents=True, exist_ok=True)
                # Write, fsync, then atomically publish without replacing any
                # previous artifact. A crash before commit remains reviewable.
                with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".pending") as handle:
                    return_frame(returns, stamps_ns).write_parquet(handle)
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
