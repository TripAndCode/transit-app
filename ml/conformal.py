"""Split conformal calibration of a quantile interval (conformalized quantile
regression). Needs the optional `ml` dependency group."""

from __future__ import annotations

import math

import numpy as np


def conformity_shift(lo: np.ndarray, hi: np.ndarray, y: np.ndarray, *, coverage: float) -> float:
    """How far to widen [lo, hi] (a negative value narrows it) so that, on these
    held-out rows, the interval holds `coverage` of them. The rank carries split
    conformal prediction's finite-sample margin, ceil((n + 1) * coverage)."""
    scores = np.sort(np.maximum(lo - y, y - hi))
    n = len(scores)
    if n == 0:
        return 0.0
    rank = min(math.ceil((n + 1) * coverage), n)
    return float(scores[rank - 1])
