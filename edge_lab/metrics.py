from __future__ import annotations

import math
from collections.abc import Iterable


_EPS = 1e-15


def brier_score(probabilities: Iterable[float], outcomes: Iterable[int]) -> float:
    pairs = list(zip(probabilities, outcomes, strict=True))
    if not pairs:
        raise ValueError("brier_score requires at least one observation")
    return sum((float(p) - int(y)) ** 2 for p, y in pairs) / len(pairs)


def log_loss(probabilities: Iterable[float], outcomes: Iterable[int]) -> float:
    pairs = list(zip(probabilities, outcomes, strict=True))
    if not pairs:
        raise ValueError("log_loss requires at least one observation")
    total = 0.0
    for p, y in pairs:
        p = min(1.0 - _EPS, max(_EPS, float(p)))
        y = int(y)
        total += -(y * math.log(p) + (1 - y) * math.log(1.0 - p))
    return total / len(pairs)
