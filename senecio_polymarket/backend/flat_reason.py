"""Minimal runtime classification for why a prediction stayed FLAT."""
from __future__ import annotations

from typing import Any


def _audit(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("audit", row.get("_audit", {}))
    return value if isinstance(value, dict) else {}


def _pipeline(row: dict[str, Any]) -> dict[str, Any]:
    value = _audit(row).get("pipeline", {})
    return value if isinstance(value, dict) else {}


def classify_flat_reason(row: dict[str, Any]) -> str:
    if str(row.get("prediction") or "").upper() != "FLAT":
        return "DIRECTIONAL_EXECUTE"
    pipeline = _pipeline(row)
    step1 = pipeline.get("step1_market") or {}
    step2 = pipeline.get("step2_features") or {}
    action = _audit(row).get("action_vector") or {}
    reason = str(action.get("reason") or "").lower()
    if step2.get("long_suppressed_by_regime"):
        return "LONG_BEAR_SUPPRESSION"
    if "no_direction" in reason or step2.get("direction") == "NEUTRAL":
        return "NO_DIRECTION/NEUTRAL"
    if "low_conviction" in reason:
        return "LOW_CONVICTION_GATE"
    if "volatile_shield" in reason or "regime_guard" in reason:
        return "REGIME/HIGH_VOL_SHIELD"
    if "high_noise" in reason:
        return "HIGH_NOISE"
    if "negative_ev" in reason:
        return "NEGATIVE_OR_INSUFFICIENT_EV"
    if "not_feasible" in reason:
        return "EXECUTION_INFEASIBLE"
    if "cooldown" in reason:
        return "LATENCY_COOLDOWN"
    if "signal_density" in reason:
        return "SIGNAL_DENSITY"
    if "size_too_small" in reason:
        return "SIZE_TOO_SMALL"
    availability = step1.get("feature_availability_v1") or {}
    if any(
        (item or {}).get("status") in {"MISSING", "SOURCE_ERROR"}
        for item in availability.values()
    ):
        return "MISSING_INPUT/DEGRADED_SOURCE"
    return "OTHER_EXPLICIT_REASON"
