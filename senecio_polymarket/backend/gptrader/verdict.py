from __future__ import annotations

import json
import math
import os
import random
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from .paths import GPTraderPaths
from .science import MIN_CALENDAR_DAYS, MIN_INDEPENDENT_1H


class Verdict(str, Enum):
    SENEX_SIGNAL_USEFUL_EVIDENCE = "SENEX_SIGNAL_USEFUL_EVIDENCE"
    SENEX_SIGNAL_NOT_USEFUL = "SENEX_SIGNAL_NOT_USEFUL"
    SENEX_DIRECTION_ANTI_INFORMATIVE = "SENEX_DIRECTION_ANTI_INFORMATIVE"
    GPTRADER_POLICY_BAD_OR_UNPROVEN = "GPTRADER_POLICY_BAD_OR_UNPROVEN"
    EXECUTION_ASSUMPTIONS_DOMINATE = "EXECUTION_ASSUMPTIONS_DOMINATE"
    INCREMENTAL_EDGE_NOT_ESTABLISHED = "INCREMENTAL_EDGE_NOT_ESTABLISHED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    reasons: tuple[str, ...]
    independent_1h: int
    calendar_days: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
            "independent_1h": self.independent_1h,
            "calendar_days": self.calendar_days,
        }


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if number < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return number


def _flag(metrics: dict[str, Any], key: str) -> bool:
    return metrics.get(key) is True


def evaluate_verdict(metrics: dict[str, Any]) -> VerdictResult:
    """Apply the preregistered ORDER086 verdict hierarchy.

    Profitability and Wilson intervals are intentionally not sufficient for a
    positive signal verdict. Dependence-aware evidence and sample gates are
    load-bearing.
    """

    if not isinstance(metrics, dict):
        raise TypeError("metrics must be a dict")

    independent_1h = _nonnegative_int(
        metrics.get("independent_1h", metrics.get("n_independent_1h", 0)),
        "independent_1h",
    )
    calendar_days = _nonnegative_int(metrics.get("calendar_days", 0), "calendar_days")

    if independent_1h < MIN_INDEPENDENT_1H or calendar_days < MIN_CALENDAR_DAYS:
        return VerdictResult(
            Verdict.INSUFFICIENT_DATA,
            ("PREREGISTERED_SAMPLE_GATE_NOT_MET",),
            independent_1h,
            calendar_days,
        )

    cost_stable = metrics.get(
        "cost_stress_2x_sign_stable",
        metrics.get("cost_stress_sign_stable"),
    )
    if cost_stable is False:
        return VerdictResult(
            Verdict.EXECUTION_ASSUMPTIONS_DOMINATE,
            ("TWO_X_COST_STRESS_CHANGES_CONCLUSION",),
            independent_1h,
            calendar_days,
        )

    if _flag(metrics, "direction_effect_below_neutral") and (
        _flag(metrics, "dependence_aware_direction_robust")
        or _flag(metrics, "direction_effect_robust")
    ):
        return VerdictResult(
            Verdict.SENEX_DIRECTION_ANTI_INFORMATIVE,
            ("DIRECTION_EFFECT_ROBUSTLY_BELOW_NEUTRAL", "NO_DIRECTION_INVERSION_ACTION"),
            independent_1h,
            calendar_days,
        )

    signal_evidence = metrics.get("signal_evidence")
    if (
        _flag(metrics, "direction_neutral")
        and signal_evidence is False
        and metrics.get("fixed_risk_beats_abstain") is False
    ):
        return VerdictResult(
            Verdict.SENEX_SIGNAL_NOT_USEFUL,
            ("NO_DIRECTIONAL_EVIDENCE", "FIXED_RISK_DOES_NOT_BEAT_ABSTAIN"),
            independent_1h,
            calendar_days,
        )

    if (
        signal_evidence is True
        and _flag(metrics, "policy_evaluated")
        and metrics.get("policy_improves_fixed_risk") is False
    ):
        return VerdictResult(
            Verdict.GPTRADER_POLICY_BAD_OR_UNPROVEN,
            ("SENEX_SIGNAL_EVIDENCE_PRESENT", "GPTRADER_POLICY_NOT_BETTER_THAN_FIXED_RISK"),
            independent_1h,
            calendar_days,
        )

    if signal_evidence is True and metrics.get("incremental_established") is False:
        return VerdictResult(
            Verdict.INCREMENTAL_EDGE_NOT_ESTABLISHED,
            ("INCREMENTAL_COMPARISON_NOT_ESTABLISHED",),
            independent_1h,
            calendar_days,
        )

    if (
        signal_evidence is True
        and _flag(metrics, "incremental_established")
        and _flag(metrics, "fixed_risk_beats_abstain")
        and _flag(metrics, "dependence_aware_ci_excludes_zero")
        and cost_stable is not False
    ):
        return VerdictResult(
            Verdict.SENEX_SIGNAL_USEFUL_EVIDENCE,
            (
                "SAMPLE_GATE_MET",
                "DEPENDENCE_AWARE_EFFECT_EXCLUDES_ZERO",
                "FIXED_RISK_BEATS_ABSTAIN",
                "INCREMENTAL_EFFECT_ESTABLISHED",
            ),
            independent_1h,
            calendar_days,
        )

    return VerdictResult(
        Verdict.INDETERMINATE,
        ("EVIDENCE_PATTERN_DOES_NOT_MEET_PREREGISTERED_DECISION_RULE",),
        independent_1h,
        calendar_days,
    )


def wilson_interval(
    successes: int,
    n: int,
    *,
    z: float = 1.959963984540054,
) -> dict[str, Any]:
    """Wilson score interval, exposed as descriptive evidence only."""

    successes = _nonnegative_int(successes, "successes")
    n = _nonnegative_int(n, "n")
    if n <= 0 or successes > n:
        raise ValueError("require 0 <= successes <= n and n > 0")
    p = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denominator
    radius = (
        z
        * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
        / denominator
    )
    return {
        "lower": max(0.0, center - radius),
        "upper": min(1.0, center + radius),
        "descriptive_only": True,
    }


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("cannot compute percentile of empty sequence")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * q
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return float(sorted_values[low])
    weight = position - low
    return float(sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight)


def block_bootstrap_mean_ci(
    hourly_cluster_values: Sequence[float],
    *,
    block_size: int = 6,
    resamples: int = 1000,
    seed: int = 86,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Seeded circular moving-block bootstrap over one value per hour cluster."""

    values = [float(value) for value in hourly_cluster_values]
    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError("hourly_cluster_values must contain finite numbers")
    if isinstance(block_size, bool) or not isinstance(block_size, int) or block_size <= 0:
        raise ValueError("block_size must be a positive integer")
    if isinstance(resamples, bool) or not isinstance(resamples, int) or resamples < 50:
        raise ValueError("resamples must be an integer >= 50")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0,1)")

    n = len(values)
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(n)
            for offset in range(block_size):
                sample.append(values[(start + offset) % n])
                if len(sample) == n:
                    break
        means.append(sum(sample) / n)

    means.sort()
    return {
        "mean": sum(values) / n,
        "lower": _percentile(means, alpha / 2.0),
        "upper": _percentile(means, 1.0 - alpha / 2.0),
        "alpha": alpha,
        "block_size": block_size,
        "resamples": resamples,
        "seed": seed,
        "temporal_dependence_acknowledged": True,
        "unit": "non_overlapping_1h_cluster",
    }


def persist_verdict(
    root: str | Path | None,
    metrics: dict[str, Any],
    *,
    provenance: dict[str, Any] | None = None,
) -> VerdictResult:
    result = evaluate_verdict(metrics)
    paths = GPTraderPaths.from_root(root)
    paths.ensure_root()
    payload = {
        **result.as_dict(),
        "edge": "UNPROVEN",
        "score_semantics": "UNCALIBRATED_SCORE",
        "wilson_role": "DESCRIPTIVE_ONLY",
        "provenance": dict(provenance or {}),
    }
    temp = paths.verdict.with_name(f".{paths.verdict.name}.tmp")
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, paths.verdict)
    return result


def verdict_from_metrics(metrics: dict[str, Any]) -> str:
    return evaluate_verdict(metrics).verdict.value
