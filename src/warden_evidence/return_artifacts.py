"""Reader for Warden-written return artifacts — never market data.

``warden audit-check`` needs the simulated net-return series a trial stored under
``registry/returns/<contract>/<run>.parquet``.  Those bytes are Warden's own simulation
output, not market bars, so they are not market data and must not be routed through
:class:`warden.market_data.MarketDataGateway`.

They are still read in one place.  ``pl.read_parquet`` outside the gateway is an integrity
violation (``warden_ops.integrity.check_ast_boundaries``), and this module is the single
permitted exception next to the feature cache.  The exemption is narrowed at runtime: a
path outside the registry's own ``returns`` tree is refused, so this reader cannot be used
to reach a dataset, a contract file or a holdout.

Two artifact generations exist and both must load:

* **timestamped** — ``timestamp`` (``Datetime("us", "UTC")``) plus ``net_return``
  (``Float64``), written since #100;
* **legacy** — ``net_return`` alone.  Such an artifact proves no clock, so this reader
  reports ``timestamps_ns is None`` instead of deriving a synthetic index.

``return_frame`` is the single writer-side schema definition, so a producer cannot drift
from what the reader accepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import numpy as np
import polars as pl

#: Column names; one definition shared by the writer and the reader.
RETURN_COLUMN = "net_return"
TIMESTAMP_COLUMN = "timestamp"
#: The only time zones an artifact may declare.  Both mean UTC.
UTC_ZONES = frozenset({"UTC", "Etc/UTC"})


class ReturnArtifactError(ValueError):
    """A path or artifact that is not an acceptable Warden return artifact."""


@dataclass(frozen=True)
class ReturnStreamArtifact:
    """One artifact's returns plus, when the artifact proves it, its UTC index.

    ``timestamps_ns`` is ``None`` exactly when the file has no ``timestamp`` column.
    ``legacy`` records that absence so callers can account for it as debt instead of
    inferring a clock.
    """

    path: Path
    net_returns: np.ndarray
    timestamps_ns: np.ndarray | None
    legacy: bool

    def __len__(self) -> int:
        return int(self.net_returns.size)


def _resolved_under(path: Path | str, returns_root: Path | str) -> Path:
    resolved = Path(path).resolve()
    root = Path(returns_root).resolve()
    if not resolved.is_relative_to(root):
        raise ReturnArtifactError(
            f"'{path}' is not under the registry returns tree '{root}'; "
            "market data is only readable through MarketDataGateway"
        )
    return resolved


def return_frame(net_returns, timestamps_ns: np.ndarray | None = None) -> pl.DataFrame:
    """Build the artifact frame a writer stores, in one schema.

    ``timestamps_ns`` must be int64 UTC epoch nanoseconds, strictly increasing and the
    same length as ``net_returns``; the reader refuses anything else, so the writer
    refuses it first.  ``timestamps_ns=None`` produces the legacy single-column frame.
    """
    values = np.asarray(net_returns, dtype=float)
    if values.ndim != 1:
        raise ReturnArtifactError("net returns must be a one-dimensional vector")
    frame = pl.DataFrame({RETURN_COLUMN: pl.Series(values, dtype=pl.Float64)})
    if timestamps_ns is None:
        return frame
    stamps = np.asarray(timestamps_ns, dtype=np.int64)
    if stamps.ndim != 1 or stamps.size != values.size:
        raise ReturnArtifactError(
            f"{stamps.size} timestamps for {values.size} returns; refusing to write a "
            "misaligned artifact")
    if stamps.size and bool(np.any(np.diff(stamps) <= 0)):
        raise ReturnArtifactError("timestamps must be strictly increasing (UTC, no duplicates)")
    column = (
        pl.Series(TIMESTAMP_COLUMN, stamps, dtype=pl.Int64)
        .cast(pl.Datetime("ns"))
        .dt.replace_time_zone("UTC")
        .dt.cast_time_unit("us")
    )
    return frame.with_columns(column)


def read_return_stream(
    path: Path | str, returns_root: Path | str, *, expected_sha256: str | None = None,
) -> ReturnStreamArtifact:
    """Read returns and their UTC index; optionally verify the exact bytes parsed."""
    resolved = _resolved_under(path, returns_root)
    if expected_sha256 is None:
        frame = pl.read_parquet(resolved)
    else:
        payload = resolved.read_bytes()
        if sha256(payload).hexdigest() != expected_sha256:
            raise ReturnArtifactError("return artifact digest mismatch")
        frame = pl.read_parquet(BytesIO(payload))
    if RETURN_COLUMN not in frame.columns:
        raise ReturnArtifactError(f"'{path}' has no '{RETURN_COLUMN}' column")
    try:
        values = frame[RETURN_COLUMN].cast(pl.Float64).to_numpy()
    except (pl.exceptions.InvalidOperationError, pl.exceptions.SchemaError) as exc:
        raise ReturnArtifactError(f"'{path}' has a non-numeric '{RETURN_COLUMN}' column") from exc
    if values.ndim != 1:
        raise ReturnArtifactError(f"'{path}' has a multi-column '{RETURN_COLUMN}' column")
    if TIMESTAMP_COLUMN not in frame.columns:
        return ReturnStreamArtifact(path=resolved, net_returns=values, timestamps_ns=None,
                                    legacy=True)
    dtype = frame.schema[TIMESTAMP_COLUMN]
    if not isinstance(dtype, pl.Datetime):
        raise ReturnArtifactError(
            f"'{path}' declares '{TIMESTAMP_COLUMN}' as {dtype}, not a UTC Datetime")
    if dtype.time_zone not in UTC_ZONES:
        raise ReturnArtifactError(
            f"'{path}' declares '{TIMESTAMP_COLUMN}' in time zone {dtype.time_zone!r}; "
            "only UTC is accepted, an unknown clock is never assumed")
    stamps = frame[TIMESTAMP_COLUMN].dt.epoch("ns").to_numpy().astype(np.int64, copy=False)
    if stamps.size and bool(np.any(np.diff(stamps) <= 0)):
        raise ReturnArtifactError(f"'{path}' has non-increasing '{TIMESTAMP_COLUMN}' values")
    if stamps.size != values.size:
        raise ReturnArtifactError(
            f"'{path}' has {values.size} returns but {stamps.size} timestamps")
    return ReturnStreamArtifact(path=resolved, net_returns=values, timestamps_ns=stamps,
                                legacy=False)


def read_return_artifact(
    path: Path | str, returns_root: Path | str, *, column: str = RETURN_COLUMN
) -> pl.Series:
    """Read one return column from ``registry/returns/...``; refuse anything else.

    ``returns_root`` is the registry's own ``returns`` directory
    (``registry.db_path.parent / "returns"``).  Both sides are resolved, so a symlink that
    escapes the tree is rejected too.  Legacy single-column artifacts keep loading.
    """
    resolved = _resolved_under(path, returns_root)
    frame = pl.read_parquet(resolved)
    if column not in frame.columns:
        raise ReturnArtifactError(f"'{path}' has no '{column}' column")
    return frame[column]
