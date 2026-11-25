"""Frozen read-only replay for legacy ``generate_positions`` strategies.

Legacy modules are historical evidence: they must stay executable exactly as they
ran, but an ``ACTIVE`` hypothesis may never execute one.  Everything that can run a
legacy module lives here, in one place, behind an explicit
``allow_legacy_replay`` check performed by the caller (``warden.protocols``); no
active-strategy path imports this module's execution helpers.

Two replay shapes are kept because they are not interchangeable:

* :func:`run_generate_positions_isolated` runs the module in a child process, so
  module-level code cannot touch the evaluator's process (``screen``/``backtest``).
* :func:`run_generate_positions_in_process` keeps qualification's recorded-config
  behaviour, where the frozen module is called directly.
"""

from __future__ import annotations

import pickle
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from warden.contracts import EvaluationContract


class LegacyReplayRefusedError(RuntimeError):
    """Raised when legacy replay is requested without the explicit replay flag."""


def _require_replay_flag(allow_legacy_replay: bool) -> None:
    if not allow_legacy_replay:
        raise LegacyReplayRefusedError(
            "Legacy generate_positions() replay is read-only historical evidence and "
            "requires the explicit allow_legacy_replay flag."
        )


def run_generate_positions_isolated(
    strategy_module_path: Path,
    panel_df: Any,
    contract: EvaluationContract,
    params: dict[str, Any],
    *,
    allow_legacy_replay: bool = True,
) -> dict[str, Any]:
    """Execute a legacy module in a subprocess and return its position payload."""
    _require_replay_flag(allow_legacy_replay)
    with tempfile.NamedTemporaryFile("wb", delete=False) as handle:
        pickle.dump((str(Path(strategy_module_path).resolve()), panel_df, contract, params), handle)
        inp_path = handle.name
    out_path = inp_path + ".out"
    cmd = [
        sys.executable,
        "-c",
        (
            "import sys, pickle\n"
            "from pathlib import Path\n"
            "from warden.strategy_loader import load_module_from_path\n"
            "inp, out = sys.argv[1], sys.argv[2]\n"
            "with open(inp, 'rb') as f: mod_str, p, c, kwargs = pickle.load(f)\n"
            "mod = load_module_from_path(Path(mod_str))\n"
            "res = mod.generate_positions(p, c, **kwargs)\n"
            "with open(out, 'wb') as f: pickle.dump(res, f)\n"
        ),
        inp_path,
        out_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            err = proc.stderr.strip() or f"Strategy subprocess failed with code {proc.returncode}"
            raise RuntimeError(err)
        with open(out_path, "rb") as handle:
            return pickle.load(handle)
    finally:
        Path(inp_path).unlink(missing_ok=True)
        Path(out_path).unlink(missing_ok=True)


def run_generate_positions_in_process(
    mod: Any,
    panel_df: Any,
    contract: EvaluationContract,
    params: dict[str, Any],
    *,
    allow_legacy_replay: bool = True,
) -> dict[str, Any]:
    """Call a legacy module's ``generate_positions`` in this process (qualification)."""
    _require_replay_flag(allow_legacy_replay)
    generate = getattr(mod, "generate_positions", None)
    if not callable(generate):
        raise LegacyReplayRefusedError("Module does not implement generate_positions().")
    payload = generate(panel_df, contract, **params)
    if not isinstance(payload, dict):
        raise LegacyReplayRefusedError(
            f"generate_positions() returned {type(payload).__name__}, not a position mapping."
        )
    return payload


def simulate_positions(
    positions,
    returns_panel,
    funding_panel,
    contract,
    val_slice,
    cost_multiplier=1.0,
    delay_offset=0,
    funding_mask=None,
    quote_returns=None,
) -> dict[str, np.ndarray]:
    """Legacy target-weight PnL arithmetic, kept bit-identical for historical parity."""
    pos = np.asarray(positions, dtype=float)
    total_delay = contract.execution_delay_bars + delay_offset
    if total_delay > 0:
        pos = np.roll(pos, total_delay, axis=0)
        pos[:total_delay] = 0.0
    pos_val = pos[val_slice]
    ret_val = returns_panel[val_slice]
    turnover = np.sum(np.abs(np.diff(pos_val, axis=0, prepend=0.0)), axis=1)
    if quote_returns is not None and quote_returns.get("short") is not None:
        long_r, short_r = quote_returns["long"][val_slice], quote_returns["short"][val_slice]
        # short_ret is already the per-unit-short PnL, so exposure is |position|.
        gross = np.sum(np.where(pos_val > 0, pos_val * long_r, np.abs(pos_val) * short_r), axis=1)
    else:
        gross = np.sum(pos_val * ret_val, axis=1)
    one_way_cost = contract.total_one_way_cost_decimal() * cost_multiplier
    if funding_panel is not None:
        fund_slice = funding_panel[val_slice]
        if funding_mask is not None:
            f_mask = np.asarray(funding_mask, dtype=bool)[val_slice, None]
            funding_cost = np.sum(pos_val * fund_slice * f_mask, axis=1)
        else:
            funding_cost = np.sum(pos_val * fund_slice, axis=1)
    else:
        funding_cost = 0.0
    net = gross - turnover * one_way_cost - funding_cost
    return {"positions": pos_val, "gross_returns": gross, "net_returns": net, "turnover": turnover}
