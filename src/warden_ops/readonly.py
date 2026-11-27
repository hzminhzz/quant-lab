"""Read-only registry view: status must never acquire a SQLite writer lock.

Normal ResearchRegistry initialization performs additive migrations. Operators
must be able to inspect the last committed WAL snapshot during a mutation,
without blocking, modifying a table or changing scientific eligibility.

``ResearchRegistry(path, readonly=True)`` is the real implementation (``mode=ro``
URI, ``PRAGMA query_only=ON``, zero DDL, version-gated migrations skipped). This
adapter stays as the status-only entrypoint so existing callers keep working.

Reporting, preflight, campaign-projection, closure-ledger, calibration and
outcome-memory consumers read the ledger through the narrow domain projections
below instead of opening their own ``sqlite3`` connection. Each method answers one
reporting question with a fixed column projection: there is deliberately no
``query(sql)``/``fetch(table)`` escape hatch, because a generic SQL tunnel in a
read-only wrapper would re-expose every table the read-only view exists to keep
out of report code. Direct connections remain only in the WAL backup snapshot
(``scripts/registry_evidence_snapshot.py``) and the migration-rehearsal utility.

Projections tolerate older snapshots: a field that a legacy registry does not have
yet is reported as missing (``None``) instead of crashing the reader, which is what
the reporting scripts previously had to do by hand with ``PRAGMA table_info``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from warden.registry import ResearchRegistry

# Fixed, read-only projections. A column added to the ledger later must be added
# here consciously; nothing is selected with ``*``.
HYPOTHESIS_FIELDS = (
    "hypothesis_id", "contract_id", "title", "strategy_family", "trials_used",
    "status", "expected_holding_bars", "strategy_module", "falsification_criteria",
    "parent_hypothesis_id",
)
TRIAL_FIELDS = (
    "run_id", "hypothesis_id", "contract_id", "strategy_name", "strategy_family",
    "strategy_module", "config_hash", "config_json", "stage_reached", "verdict",
    "trial_number_in_hyp", "gross_sharpe", "net_sharpe", "stress_1p5_cost_sharpe",
    "stress_2x_cost_sharpe", "delay_plus1_sharpe", "deflated_sharpe_prob",
    "expected_max_sharpe_noise", "total_trials_n", "effective_trials_keff",
    "annualized_turnover", "breakeven_cost_bps", "max_drawdown_pct",
    "param_neighbor_stability", "diagnostics_json", "returns_parquet_path",
    "returns_json", "created_at",
)
ATTEMPT_FIELDS = (
    "attempt_id", "run_id", "hypothesis_id", "contract_id", "strategy_family",
    "strategy_module", "kind", "stage", "outcome", "parameters_json",
    "returns_observed", "returns_json", "diagnostics_json", "created_at",
)
CONTRACT_FIELDS = ("contract_id", "asset_class", "frequency", "bars_per_year",
                   "label_horizon_bars")
SEARCH_STATE_FIELDS = ("contract_id", "max_keff_gate", "max_noise", "updated_at")


def open_readonly(db_path: Path) -> ReadOnlyResearchRegistry | None:
    """Open an existing registry read-only, or ``None`` when it does not exist.

    Never creates the file: a missing ledger must be reported by the caller, never
    silently materialised as an empty database by a status command.
    """
    path = Path(db_path)
    return ReadOnlyResearchRegistry(path) if path.is_file() else None


class ReadOnlyResearchRegistry(ResearchRegistry):
    """Status-only adapter: no migrations, no writer lock, no DDL."""

    def __init__(self, db_path: Path):
        super().__init__(db_path, readonly=True)

    def present(self) -> bool:
        """True when the ledger file exists (a readonly view never creates it)."""
        return self.db_path.is_file()

    # --- internals -------------------------------------------------------

    def _columns(self, table: str, allowed: tuple[str, ...]) -> list[str]:
        """The allowlisted columns this snapshot actually has, in review order."""
        with self._connect() as conn:
            present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        return [name for name in allowed if name in present]

    def _project(self, table: str, allowed: tuple[str, ...], tail: str,
                 params: tuple = ()) -> list[dict]:
        """One fixed projection of one ledger table; ``tail`` is a literal per call site.

        Only columns this snapshot actually has are returned, in allowlist order, so a legacy
        registry yields exactly the fields it stores instead of a row padded with ``None``.
        """
        columns = self._columns(table, allowed)
        if not columns:
            raise sqlite3.OperationalError(f"no {table} table in this registry snapshot")
        rows = []
        with self._connect() as conn:
            for row in conn.execute(f"SELECT {','.join(columns)} FROM {table} {tail}", params):
                projected = dict(row)
                rows.append({name: projected[name] for name in columns})
        return rows

    # --- contracts and contract-wide search state ------------------------

    def read_contracts(self) -> list[dict]:
        """Contract identity/frequency facts used to annualise reporting metrics."""
        return self._project("contracts", CONTRACT_FIELDS, "ORDER BY contract_id")

    def read_contract_spec_hash(self, contract_id: str) -> str | None:
        """Recorded immutable contract identity, without registration or migration."""
        rows = self._project("contracts", ("spec_hash",),
                             "WHERE contract_id=?", (contract_id,))
        return rows[0]["spec_hash"] if rows else None

    def read_search_state(self, contract_id: str) -> dict | None:
        """The stored multiple-testing floor for one contract, or ``None``."""
        rows = self._project("contract_search_state", SEARCH_STATE_FIELDS,
                             "WHERE contract_id=?", (contract_id,))
        return rows[0] if rows else None

    # --- hypotheses ------------------------------------------------------

    def read_hypothesis(self, hypothesis_id: str) -> dict | None:
        rows = self._project("hypotheses", HYPOTHESIS_FIELDS,
                             "WHERE hypothesis_id=?", (hypothesis_id,))
        return rows[0] if rows else None

    def read_hypothesis_inventory(self) -> list[dict]:
        return self._project("hypotheses", HYPOTHESIS_FIELDS,
                             "ORDER BY hypothesis_id" if "hypothesis_id" in
                             self._columns("hypotheses", HYPOTHESIS_FIELDS) else "")

    def read_module_owner(self, strategy_module: str,
                          exclude_hypothesis_id: str = "") -> str | None:
        """The other hypothesis, if any, already registered to this strategy module."""
        rows = self._project("hypotheses", ("hypothesis_id", "strategy_module"),
                             "WHERE strategy_module=? AND hypothesis_id!=? LIMIT 1",
                             (strategy_module, exclude_hypothesis_id))
        return rows[0]["hypothesis_id"] if rows else None

    # --- trials ----------------------------------------------------------

    def read_trial_evidence(self, run_id: str) -> dict | None:
        """Immutable trial evidence for one run, without re-hashing its artifact.

        Named apart from ``ResearchRegistry.read_trial`` on purpose: that returns the
        fixed identity/verdict ``TrialSummary``, while reporting needs the metric columns
        as well, and widening that method's contract would blur the two read tiers.
        """
        rows = self._project("trials", TRIAL_FIELDS, "WHERE run_id=?", (run_id,))
        return rows[0] if rows else None

    def read_latest_trial(self, hypothesis_id: str, *,
                          by_created_at: bool = False) -> dict | None:
        """Newest trial of one hypothesis: by trial number, or by creation time."""
        order = "created_at DESC" if by_created_at else "trial_number_in_hyp DESC"
        rows = self._project("trials", TRIAL_FIELDS,
                             f"WHERE hypothesis_id=? ORDER BY {order} LIMIT 1",
                             (hypothesis_id,))
        return rows[0] if rows else None

    def read_trial_by_number(self, hypothesis_id: str, trial_number: int) -> dict | None:
        rows = self._project("trials", TRIAL_FIELDS,
                             "WHERE hypothesis_id=? AND trial_number_in_hyp=?",
                             (hypothesis_id, int(trial_number)))
        return rows[0] if rows else None

    def read_trials(self) -> list[dict]:
        """Every trial in ledger order: contract, then creation time, then run id."""
        columns = self._columns("trials", TRIAL_FIELDS)
        order = ",".join(name for name in ("contract_id", "created_at", "run_id")
                         if name in columns)
        return self._project("trials", TRIAL_FIELDS, f"ORDER BY {order}" if order else "")

    def read_trials_for(self, hypothesis_id: str, contract_id: str) -> list[dict]:
        """One hypothesis/contract evidence trail, newest first."""
        return self._project("trials", TRIAL_FIELDS,
                             "WHERE hypothesis_id=? AND contract_id=? ORDER BY created_at DESC",
                             (hypothesis_id, contract_id))

    def read_trial_returns_path(self, run_id: str) -> str | None:
        rows = self._project("trials", ("run_id", "returns_parquet_path"),
                             "WHERE run_id=?", (run_id,))
        return rows[0]["returns_parquet_path"] if rows else None

    def read_trial_audit_verdict(self, run_id: str) -> str | None:
        """The recorded six-check audit verdict for a run, or ``None`` (never runs one)."""
        try:
            rows = self._project("audit_evidence", ("run_id", "verdict"),
                                 "WHERE run_id=? LIMIT 1", (run_id,))
        except sqlite3.Error:
            return None  # legacy snapshot without the audit table
        return rows[0]["verdict"] if rows else None

    def read_recorded_return_vectors(self, contract_id: str,
                                     exclude_run_id: str = "") -> list[str]:
        """Every recorded return vector for a contract's trials and parameter grids."""
        trials = self._project("trials", ("run_id", "contract_id", "returns_json"),
                               "WHERE contract_id=? AND run_id!=? AND returns_json NOT IN ('', '[]')",
                               (contract_id, exclude_run_id))
        grids = self._project("selection_attempts", ("contract_id", "kind", "returns_json"),
                              "WHERE contract_id=? AND kind='param_grid' "
                              "AND returns_json NOT IN ('', '[]')", (contract_id,))
        return [row["returns_json"] for row in (*trials, *grids)]

    def read_recorded_trials(self, hypothesis_id: str) -> dict:
        """Recorded trials of one hypothesis, with legacy-schema availability flags."""
        columns = self._columns("trials", ("run_id", "verdict", "config_hash"))
        return {
            "rows": self._project("trials", ("run_id", "verdict", "config_hash"),
                                  "WHERE hypothesis_id=?", (hypothesis_id,)),
            "records_config_hash": "config_hash" in columns,
        }

    # --- selection attempts ---------------------------------------------

    def read_standalone_screen(self, hypothesis_id: str) -> dict | None:
        """The recorded Stage-1 screen for a hypothesis, if any."""
        rows = self._project("selection_attempts", ATTEMPT_FIELDS,
                             "WHERE hypothesis_id=? AND kind='standalone_screen' LIMIT 1",
                             (hypothesis_id,))
        return rows[0] if rows else None

    def read_selection_attempts(self, hypothesis_id: str, contract_id: str) -> list[dict]:
        """Every recorded attempt for one hypothesis/contract, newest first."""
        return self._project("selection_attempts", ATTEMPT_FIELDS,
                             "WHERE hypothesis_id=? AND contract_id=? ORDER BY created_at DESC",
                             (hypothesis_id, contract_id))

    def read_attempts(self) -> list[dict]:
        """Every selection attempt in ledger order: creation time, then attempt id."""
        columns = self._columns("selection_attempts", ATTEMPT_FIELDS)
        order = ",".join(name for name in ("created_at", "attempt_id") if name in columns)
        return self._project("selection_attempts", ATTEMPT_FIELDS,
                             f"ORDER BY {order}" if order else "")

    def read_param_grid_diagnostics(self, hypothesis_id: str, *,
                                    after: float | None = None,
                                    until: float | None = None) -> list[str]:
        """Raw ``diagnostics_json`` of a hypothesis's parameter-grid attempts in order."""
        tail = "WHERE hypothesis_id=? AND kind='param_grid'"
        params: list[object] = [hypothesis_id]
        if after is not None:
            tail += " AND created_at > ?"
            params.append(float(after))
        if until is not None:
            tail += " AND created_at <= ?"
            params.append(float(until))
        rows = self._project("selection_attempts", ("diagnostics_json",),
                             tail + " ORDER BY created_at", tuple(params))
        return [row["diagnostics_json"] for row in rows]

    def read_recorded_screens(self, hypothesis_id: str) -> dict:
        """Recorded screens of one hypothesis, with legacy-schema availability flags."""
        columns = self._columns("selection_attempts", ("strategy_module", "parameters_json"))
        return {
            "rows": self._project("selection_attempts", ATTEMPT_FIELDS,
                                  "WHERE hypothesis_id=?", (hypothesis_id,)),
            "records_module": "strategy_module" in columns,
            "records_parameters": "parameters_json" in columns,
        }

    def read_pending_attempts(self, hypothesis_id: str, contract_id: str) -> list[dict]:
        """Reserved-but-unfinalised attempt receipts; empty on a legacy snapshot."""
        try:
            return self._project("attempt_receipts", ("run_id", "state"),
                                 "WHERE hypothesis_id=? AND contract_id=? AND state!='FINALIZED'",
                                 (hypothesis_id, contract_id))
        except sqlite3.Error:
            return []

    # --- counts ----------------------------------------------------------

    def _count(self, sql: str, params: tuple) -> int | None:
        try:
            with self._connect() as conn:
                row = conn.execute(sql, params).fetchone()
        except sqlite3.Error:
            return None  # fresh or older registry: table/column absent, report unknown
        return int(row[0]) if row else 0

    def read_contract_evidence_counts(self, contract_id: str) -> dict:
        """Per-table evidence counts for one contract; ``None`` when a table is absent."""
        return {
            "hypotheses": self._count("SELECT COUNT(*) FROM hypotheses WHERE contract_id=?",
                                      (contract_id,)),
            "trials": self._count("SELECT COUNT(*) FROM trials WHERE contract_id=?",
                                  (contract_id,)),
            "selection_attempts": self._count(
                "SELECT COUNT(*) FROM selection_attempts WHERE contract_id=?", (contract_id,)),
            "standalone_screens": self._count(
                "SELECT COUNT(*) FROM selection_attempts WHERE contract_id=? "
                "AND kind='standalone_screen'", (contract_id,)),
        }

    def read_hypothesis_evidence_counts(self, hypothesis_ids: tuple[str, ...], *,
                                        attempt_kind: str) -> dict:
        """Trial and attempt counts owned by a set of hypothesis ids."""
        if not hypothesis_ids:
            return {"trials": 0, "selection_attempts": 0}
        marks = ",".join("?" * len(hypothesis_ids))
        return {
            "trials": self._count(
                f"SELECT COUNT(*) FROM trials WHERE hypothesis_id IN ({marks})",
                tuple(hypothesis_ids)),
            "selection_attempts": self._count(
                f"SELECT COUNT(*) FROM selection_attempts WHERE hypothesis_id IN ({marks}) "
                "AND kind=?", (*hypothesis_ids, attempt_kind)),
        }

    def read_evidence_counts(self) -> dict:
        """Row counts of the three ledger tables an inventory reports."""
        return {table: self._count(f"SELECT COUNT(*) FROM {table}", ())
                for table in ("hypotheses", "trials", "selection_attempts")}
