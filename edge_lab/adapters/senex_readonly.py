from __future__ import annotations

from typing import Any

from edge_lab.baseline import ProbabilityObservation


def _infer_up_outcome(row: dict[str, Any]) -> int:
    direction = str(row.get("prediction") or "").upper()
    outcome = str(row.get("outcome") or "").upper()
    if direction == "LONG" and outcome == "WIN":
        return 1
    if direction == "LONG" and outcome == "LOSS":
        return 0
    if direction == "SHORT" and outcome == "WIN":
        return 0
    if direction == "SHORT" and outcome == "LOSS":
        return 1
    raise ValueError("resolved directional outcome is required")


def _market_horizon(poly: dict[str, Any]) -> str:
    version = str(poly.get("version") or "").lower()
    if "5m" in version:
        return "5m"
    horizon = poly.get("horizon")
    if horizon:
        return str(horizon)
    raise ValueError("Polymarket decision-time horizon is unavailable")


def extract_probability_observation(
    row: dict[str, Any],
    *,
    senex_horizon: str = "1h",
) -> ProbabilityObservation:
    audit = row.get("audit") if isinstance(row.get("audit"), dict) else {}
    pipeline = audit.get("pipeline") if isinstance(audit.get("pipeline"), dict) else {}
    step2 = pipeline.get("step2_features") if isinstance(pipeline.get("step2_features"), dict) else {}
    external = audit.get("external_markets_v1") if isinstance(audit.get("external_markets_v1"), dict) else {}
    poly = external.get("polymarket") if isinstance(external.get("polymarket"), dict) else {}

    p_market = poly.get("up_probability")
    if not isinstance(p_market, (int, float)):
        raise ValueError("decision-time Polymarket prior is required")
    p_senex = step2.get("up_prob")
    if not isinstance(p_senex, (int, float)):
        raise ValueError("SENEX p_senex/up_prob is required")

    return ProbabilityObservation(
        p_market=float(p_market),
        p_senex=float(p_senex),
        outcome=_infer_up_outcome(row),
        market_horizon=_market_horizon(poly),
        senex_horizon=str(senex_horizon),
        p_senex_semantics="UNVALIDATED_MODEL_UP_PROB",
        resolved=True,
    )
