"""Depth-2 path signature: the two conventions the source conflates, plus its seven signals.

Source: delphicalpha, "Path Signatures - Does the Shape of Price Paths Predict Returns".
Lines 70-84 define level 2 as a strict `t1 < t2` double sum of increments. Lines 88 and
184 say the reported numbers came from `iisignature`, which returns the standard truncated
signature, where intra-segment terms carry 1/2. The two differ on the cross terms by
`0.5*sum(dx*dy)` and on the diagonal by `0.5*sum(dx**2)`, so a result computed one way may
not be reported as the other. Both are implemented; callers must name the convention.

Line 52 constructs the path from cumulative log return and cumulative (high-low)/close, then
standardises it to unit variance per dimension. `standardize_path` does that on the cumulative
path. Standardising *increments* instead would centre them, forcing the total displacement to
zero and destroying the endpoint term that carries much of the level-2 signal.
"""

from __future__ import annotations

import numpy as np

SIGNAL_NAMES = (
    "Sig-Reversion",
    "Sig-VolDrift",
    "Sig-Accel",
    "Sig-LevyArea",
    "Sig-FadeRetVol",
    "Sig-FadeVolRet",
    "Sig-VolAccel",
)


def _as_path(values, name: str) -> np.ndarray:
    pts = np.asarray(values, dtype=float)
    if pts.ndim != 1 or pts.size < 2:
        raise ValueError(f"{name} must be a 1D array with at least two points")
    if not np.isfinite(pts).all():
        raise ValueError(f"{name} coordinates must be finite")
    return pts


def depth2_signature(x, y) -> dict[str, float]:
    """Standard truncated signature of the piecewise-linear path, anchored at its first point.

    Matches what `iisignature` returns (source lines 88, 184). Here S11 and S22 reduce to
    half the squared displacement, so this convention carries no ordering information on the
    diagonal at all; the cross terms carry the Lévy area.
    """
    pts_x = _as_path(x, "x")
    pts_y = _as_path(y, "y")
    if pts_x.shape != pts_y.shape:
        raise ValueError("x and y must be equal-length 1D arrays")
    pts_x, pts_y = pts_x - pts_x[0], pts_y - pts_y[0]
    dx, dy = np.diff(pts_x), np.diff(pts_y)
    x_prev, y_prev = pts_x[:-1], pts_y[:-1]
    # Iterated integrals over a linear segment: int X dY = X_prev*dY + dX*dY/2.
    s12 = float(np.sum(x_prev * dy + 0.5 * dx * dy))
    s21 = float(np.sum(y_prev * dx + 0.5 * dx * dy))
    return {
        "S11": 0.5 * float(pts_x[-1]) ** 2,
        "S12": s12,
        "S21": s21,
        "S22": 0.5 * float(pts_y[-1]) ** 2,
    }


def strict_pair_signature(x, y) -> dict[str, float]:
    """Strict i<j double sum of increments, exactly as the source writes level 2 (lines 70-84).

    Here S11 = (dX^2 - sum(dx^2)) / 2, so the diagonal responds to how dispersed the
    increments are, but still not to their time order.
    """
    dx, dy = np.diff(_as_path(x, "x")), np.diff(_as_path(y, "y"))
    if dx.shape != dy.shape:
        raise ValueError("x and y must be equal-length 1D arrays")
    prefix_dx = np.concatenate(([0.0], np.cumsum(dx)[:-1]))
    prefix_dy = np.concatenate(([0.0], np.cumsum(dy)[:-1]))
    return {
        "S11": float(np.sum(prefix_dx * dx)),
        "S12": float(np.sum(prefix_dx * dy)),
        "S21": float(np.sum(prefix_dy * dx)),
        "S22": float(np.sum(prefix_dy * dy)),
    }


def levy_area(x, y, convention: str = "standard") -> float:
    """Signed order-sensitive area A = S(1,2) - S(2,1) (source line 86)."""
    comps = _components(x, y, convention)
    return comps["S12"] - comps["S21"]


def source_signals(x, y, convention: str = "strict") -> dict[str, float]:
    """The seven signals of Appendix C (source lines 222-240), signs exactly as stated.

    Defaults to the strict convention because that is the one the source fully specifies.
    """
    comps = _components(x, y, convention)
    pts_x, pts_y = _as_path(x, "x"), _as_path(y, "y")
    level1_return = float(pts_x[-1] - pts_x[0])
    level1_range = float(pts_y[-1] - pts_y[0])
    return {
        "Sig-Reversion": -level1_return,
        "Sig-VolDrift": level1_range,
        "Sig-Accel": comps["S11"],
        "Sig-LevyArea": comps["S12"] - comps["S21"],
        "Sig-FadeRetVol": -comps["S12"],
        "Sig-FadeVolRet": -comps["S21"],
        "Sig-VolAccel": comps["S22"],
    }


def _components(x, y, convention: str) -> dict[str, float]:
    if convention == "standard":
        return depth2_signature(x, y)
    if convention == "strict":
        return strict_pair_signature(x, y)
    raise ValueError(f"unknown convention {convention!r}; use 'standard' or 'strict'")


def standardize_path(points) -> np.ndarray:
    """Z-score the cumulative path per dimension within one window (source line 52).

    Only the scale matters: both conventions anchor at the first point or use increments
    alone, so translating the whole path leaves every component unchanged. Scaling is not
    neutral -- level 2 is homogeneous of degree 2 -- which is what "unit variance per
    dimension" buys. Standardising increments instead would zero the displacement and destroy
    the endpoint term.
    """
    arr = np.asarray(points, dtype=float)
    if arr.ndim != 2:
        raise ValueError("points must be a 2D array of shape (n_bars, n_dims)")
    if not np.isfinite(arr).all():
        raise ValueError("path coordinates must be finite")
    std = arr.std(axis=0)
    std[std == 0.0] = 1.0
    return (arr - arr.mean(axis=0)) / std


def bruteforce_iterated(x, y, substeps: int = 2000) -> dict[str, float]:
    """Independent check: trapezoid Riemann sums of int X dY and int Y dX."""
    pts_x = _as_path(x, "x")
    pts_y = _as_path(y, "y")
    pts_x, pts_y = pts_x - pts_x[0], pts_y - pts_y[0]
    # Refining a piecewise-linear path leaves it geometrically unchanged.
    xs, ys = [], []
    for i in range(len(pts_x) - 1):
        xs.extend(np.linspace(pts_x[i], pts_x[i + 1], substeps, endpoint=False))
        ys.extend(np.linspace(pts_y[i], pts_y[i + 1], substeps, endpoint=False))
    xs.append(pts_x[-1])
    ys.append(pts_y[-1])
    xs, ys = np.asarray(xs), np.asarray(ys)
    x_mid, y_mid = 0.5 * (xs[1:] + xs[:-1]), 0.5 * (ys[1:] + ys[:-1])
    return {
        "S12": float(np.sum(x_mid * np.diff(ys))),
        "S21": float(np.sum(y_mid * np.diff(xs))),
        "S11": float(np.sum(x_mid * np.diff(xs))),
        "S22": float(np.sum(y_mid * np.diff(ys))),
    }
