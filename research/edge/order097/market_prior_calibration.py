"""ORDER097 target-aligned SENEX-vs-Polymarket incremental EDGE research.

Pure offline functions only. This module deliberately refuses to use the
canonical SENEX 15m/1h outcome as the label for a Polymarket BTC 5m contract.
The only admissible label is an independently supplied resolution for the
exact same Polymarket market identity, observed after market close.

No network, runtime, predictor, GPTrader, execution, or order surface exists.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, NamedTuple


POLYMARKET_VERSION = "polymarket-btc-5m-v1"
POLYMARKET_SOURCE = "POLYMARKET_PUBLIC"
TARGET_HORIZON_SECONDS = 300


class ResolutionContractError(ValueError):
    """Resolution evidence violates the target-alignment contract."""


class T0Pair(NamedTuple):
    prediction_id: object
    decision_ts: float
    market_slug: str
    condition_id: str
    market_start_ts: int
    market_end_ts: int
    market_horizon_seconds: int
    p_market: float
    senex_raw_up: float


class JoinedObservation(NamedTuple):
    pair: T0Pair
    label_up: int
    resolved_at: float


class PlattCalibrator(NamedTuple):
    intercept: float
    slope: float

    def predict(self, raw_up: float) -> float:
        x = _logit(_probability(raw_up, "senex_raw_up"))
        return _sigmoid(self.intercept + self.slope * x)


def _parse_time(value: object, field: str) -> float:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is missing")
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} is not ISO8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc).timestamp()


def _probability(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        raise ValueError(f"{field} is outside [0,1]")
    return result


def _logit(p: float) -> float:
    clipped = min(1.0 - 1e-6, max(1e-6, p))
    return math.log(clipped / (1.0 - clipped))


def _sigmoid(z: float) -> float:
    if z >= 0:
        exp_neg = math.exp(-z)
        return 1.0 / (1.0 + exp_neg)
    exp_pos = math.exp(z)
    return exp_pos / (1.0 + exp_pos)


def _slug_start(slug: str) -> int | None:
    prefix = "btc-updown-5m-"
    if not slug.startswith(prefix):
        return None
    suffix = slug[len(prefix):]
    if not suffix.isdigit():
        return None
    try:
        return int(suffix)
    except ValueError:
        return None


def extract_t0_pair(row: dict) -> T0Pair | None:
    """Extract one causal model-vs-market pair from a local prediction row.

    The row outcome field is deliberately ignored because it belongs to
    SENEX canonical settlement horizons, not to the Polymarket 5m contract.
    """
    if not isinstance(row, dict):
        return None
    symbol = str(row.get("symbol") or "").replace("/", "").upper()
    if symbol != "BTCUSDT":
        return None

    audit = row.get("_audit")
    if not isinstance(audit, dict):
        audit = row.get("audit")
    if not isinstance(audit, dict):
        return None

    pipeline = audit.get("pipeline")
    step2 = pipeline.get("step2_features") if isinstance(pipeline, dict) else None
    if not isinstance(step2, dict):
        return None

    # Incremental-value testing must not compare the market prior against a
    # SENEX score that already consumed that same prior.  Require explicit T0
    # evidence that Polymarket directional fusion was disabled.
    poly_influence = step2.get("polymarket_context_v1")
    if not isinstance(poly_influence, dict):
        return None
    if poly_influence.get("directional_use") is not False:
        return None
    if poly_influence.get("experiment_enabled") is not False:
        return None
    try:
        effective_weight = float(poly_influence.get("effective_weight"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(effective_weight) or abs(effective_weight) > 1e-12:
        return None

    external = audit.get("external_markets_v1")
    poly = external.get("polymarket") if isinstance(external, dict) else None
    if not isinstance(poly, dict):
        return None
    if poly.get("source") != POLYMARKET_SOURCE:
        return None
    if poly.get("version") != POLYMARKET_VERSION:
        return None
    if poly.get("eligible_for_prediction") is not True:
        return None

    slug = str(poly.get("slug") or "")
    condition_id = str(poly.get("condition_id") or "")
    if not slug or not condition_id:
        return None

    try:
        start_ts = int(poly.get("start_ts"))
        end_ts = int(poly.get("end_ts"))
    except (TypeError, ValueError):
        return None
    if end_ts - start_ts != TARGET_HORIZON_SECONDS:
        return None
    if _slug_start(slug) != start_ts:
        return None

    try:
        decision_ts = _parse_time(row.get("ts") or row.get("timestamp"), "decision_ts")
        p_market = _probability(poly.get("up_probability"), "p_market")
        senex_raw_up = _probability(step2.get("up_prob"), "senex_raw_up")
    except ValueError:
        return None

    if decision_ts < start_ts or decision_ts >= end_ts:
        return None

    return T0Pair(
        prediction_id=row.get("id"),
        decision_ts=decision_ts,
        market_slug=slug,
        condition_id=condition_id,
        market_start_ts=start_ts,
        market_end_ts=end_ts,
        market_horizon_seconds=TARGET_HORIZON_SECONDS,
        p_market=p_market,
        senex_raw_up=senex_raw_up,
    )


def extract_t0_pairs(rows: Iterable[dict]) -> list[T0Pair]:
    result: list[T0Pair] = []
    for row in rows:
        pair = extract_t0_pair(row)
        if pair is not None:
            result.append(pair)
    return result


def _resolution_record(raw: dict) -> tuple[tuple[str, str], dict]:
    if not isinstance(raw, dict):
        raise ResolutionContractError("resolution must be an object")
    slug = str(raw.get("slug") or "")
    condition_id = str(raw.get("condition_id") or "")
    if not slug or not condition_id:
        raise ResolutionContractError("resolution market identity is missing")

    try:
        start_ts = int(raw.get("start_ts"))
        end_ts = int(raw.get("end_ts"))
    except (TypeError, ValueError) as exc:
        raise ResolutionContractError("resolution market grid is invalid") from exc
    if end_ts - start_ts != TARGET_HORIZON_SECONDS or _slug_start(slug) != start_ts:
        raise ResolutionContractError("resolution market identity/grid is not BTC 5m")

    outcome = str(raw.get("outcome") or "").upper()
    if outcome not in {"UP", "DOWN"}:
        raise ResolutionContractError("resolution outcome must be UP or DOWN")
    source_raw = raw.get("source")
    if not isinstance(source_raw, str) or not source_raw.strip():
        raise ResolutionContractError("resolution source is missing or invalid")
    source = source_raw.strip()
    try:
        resolved_at = _parse_time(raw.get("resolved_at"), "resolved_at")
    except ValueError as exc:
        raise ResolutionContractError(str(exc)) from exc
    if resolved_at < end_ts:
        raise ResolutionContractError("resolution observed before market close")

    normalized = {
        "slug": slug,
        "condition_id": condition_id,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "outcome": outcome,
        "resolved_at": resolved_at,
        "source": source,
    }
    return (slug, condition_id), normalized


def join_resolutions(
    pairs: Iterable[T0Pair],
    resolutions: Iterable[dict],
) -> list[JoinedObservation]:
    """Join labels to exact market identities without using SENEX outcomes."""
    by_market: dict[tuple[str, str], dict] = {}
    for raw in resolutions:
        key, normalized = _resolution_record(raw)
        existing = by_market.get(key)
        if existing is not None and existing != normalized:
            raise ResolutionContractError(
                f"conflicting resolution for market identity {key!r}"
            )
        by_market[key] = normalized

    joined: list[JoinedObservation] = []
    for pair in pairs:
        key = (pair.market_slug, pair.condition_id)
        resolution = by_market.get(key)
        if resolution is None:
            continue
        if (
            resolution["start_ts"] != pair.market_start_ts
            or resolution["end_ts"] != pair.market_end_ts
        ):
            raise ResolutionContractError(
                f"resolution market identity/grid mismatch for {key!r}"
            )
        resolved_at = float(resolution["resolved_at"])
        if resolved_at < pair.market_end_ts:
            raise ResolutionContractError("resolution observed before market close")
        joined.append(
            JoinedObservation(
                pair=pair,
                label_up=1 if resolution["outcome"] == "UP" else 0,
                resolved_at=resolved_at,
            )
        )
    return joined


def chronological_market_split(
    observations: Iterable[JoinedObservation],
    *,
    train_fraction: float = 0.67,
) -> tuple[list[JoinedObservation], list[JoinedObservation]]:
    """Chronological split by complete market groups, never by individual row."""
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be in (0,1)")
    rows = sorted(
        observations,
        key=lambda item: (
            item.pair.market_end_ts,
            item.pair.market_slug,
            item.pair.decision_ts,
        ),
    )
    market_keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in rows:
        key = (item.pair.market_slug, item.pair.condition_id)
        if key not in seen:
            seen.add(key)
            market_keys.append(key)
    if len(market_keys) < 2:
        raise ValueError("at least two resolved markets are required")

    cut = int(len(market_keys) * train_fraction)
    cut = min(len(market_keys) - 1, max(1, cut))
    train_keys = set(market_keys[:cut])
    train = [
        item for item in rows
        if (item.pair.market_slug, item.pair.condition_id) in train_keys
    ]
    test = [
        item for item in rows
        if (item.pair.market_slug, item.pair.condition_id) not in train_keys
    ]
    return train, test


def fit_platt(
    observations: Iterable[JoinedObservation],
    *,
    max_iter: int = 4000,
    learning_rate: float = 0.02,
    l2: float = 1e-4,
) -> PlattCalibrator:
    """Fit a train-only logistic mapping from raw SENEX score to P(UP)."""
    rows = list(observations)
    if len(rows) < 8:
        raise ValueError("at least 8 training observations are required")
    labels = {int(item.label_up) for item in rows}
    if labels != {0, 1}:
        raise ValueError("training observations require both UP and DOWN labels")
    if max_iter <= 0 or learning_rate <= 0:
        raise ValueError("optimizer parameters must be positive")

    intercept = 0.0
    slope = 1.0
    n = float(len(rows))
    for _ in range(max_iter):
        grad_intercept = 0.0
        grad_slope = 0.0
        for item in rows:
            x = _logit(_probability(item.pair.senex_raw_up, "senex_raw_up"))
            pred = _sigmoid(intercept + slope * x)
            error = pred - int(item.label_up)
            grad_intercept += error
            grad_slope += error * x
        grad_intercept = grad_intercept / n + l2 * intercept
        grad_slope = grad_slope / n + l2 * slope
        intercept -= learning_rate * grad_intercept
        slope -= learning_rate * grad_slope

    return PlattCalibrator(intercept=intercept, slope=slope)


def _brier(probabilities: list[float], labels: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probabilities, labels)) / len(labels)


def _log_loss(probabilities: list[float], labels: list[int]) -> float:
    total = 0.0
    for p, y in zip(probabilities, labels):
        clipped = min(1.0 - 1e-12, max(1e-12, p))
        total -= y * math.log(clipped) + (1 - y) * math.log(1.0 - clipped)
    return total / len(labels)


def evaluate_paired(
    observations: Iterable[JoinedObservation],
    calibrator: PlattCalibrator,
) -> dict[str, float | int]:
    """Compare calibrated SENEX and Polymarket on identical resolved rows."""
    rows = list(observations)
    if not rows:
        raise ValueError("paired evaluation requires resolved observations")

    labels = [int(item.label_up) for item in rows]
    market = [_probability(item.pair.p_market, "p_market") for item in rows]
    senex = [calibrator.predict(item.pair.senex_raw_up) for item in rows]

    market_brier = _brier(market, labels)
    senex_brier = _brier(senex, labels)
    market_log = _log_loss(market, labels)
    senex_log = _log_loss(senex, labels)
    market_acc = sum(
        (p >= 0.5) == bool(y) for p, y in zip(market, labels)
    ) / len(labels)
    senex_acc = sum(
        (p >= 0.5) == bool(y) for p, y in zip(senex, labels)
    ) / len(labels)

    markets = {
        (item.pair.market_slug, item.pair.condition_id)
        for item in rows
    }
    return {
        "n": len(rows),
        "n_markets": len(markets),
        "market_brier": market_brier,
        "senex_brier": senex_brier,
        "brier_delta_senex_minus_market": senex_brier - market_brier,
        "market_log_loss": market_log,
        "senex_log_loss": senex_log,
        "log_loss_delta_senex_minus_market": senex_log - market_log,
        "market_directional_accuracy": market_acc,
        "senex_directional_accuracy": senex_acc,
        "accuracy_delta_senex_minus_market": senex_acc - market_acc,
    }


def experiment_status(
    pairs: Iterable[T0Pair],
    resolutions: Iterable[dict],
) -> dict[str, object]:
    pairs_list = list(pairs)
    resolutions_list = list(resolutions)
    if not pairs_list:
        return {
            "status": "BLOCKED_NO_DECISION_TIME_PAIRS",
            "edge": "UNPROVEN",
            "pairs": 0,
            "resolved_pairs": 0,
        }
    if not resolutions_list:
        return {
            "status": "BLOCKED_TARGET_LABEL_5M_NOT_PERSISTED",
            "edge": "UNPROVEN",
            "pairs": len(pairs_list),
            "resolved_pairs": 0,
        }
    joined = join_resolutions(pairs_list, resolutions_list)
    if not joined:
        return {
            "status": "BLOCKED_NO_TARGET_ALIGNED_RESOLUTIONS",
            "edge": "UNPROVEN",
            "pairs": len(pairs_list),
            "resolved_pairs": 0,
        }
    return {
        "status": "READY_FOR_CHRONOLOGICAL_CALIBRATION",
        "edge": "UNPROVEN",
        "pairs": len(pairs_list),
        "resolved_pairs": len(joined),
        "resolved_markets": len({
            (item.pair.market_slug, item.pair.condition_id)
            for item in joined
        }),
    }


def read_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSONL at {path}:{line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(
                    f"non-object JSONL row at {path}:{line_number}"
                )
            rows.append(value)
    return rows


def run_offline(
    predictions_path: str | Path,
    resolutions_path: str | Path | None = None,
    *,
    train_fraction: float = 0.67,
) -> dict[str, object]:
    rows = read_jsonl(predictions_path)
    pairs = extract_t0_pairs(rows)
    resolutions = read_jsonl(resolutions_path) if resolutions_path else []
    status = experiment_status(pairs, resolutions)
    if status["status"] != "READY_FOR_CHRONOLOGICAL_CALIBRATION":
        return status

    joined = join_resolutions(pairs, resolutions)
    train, test = chronological_market_split(
        joined,
        train_fraction=train_fraction,
    )
    calibrator = fit_platt(train)
    metrics = evaluate_paired(test, calibrator)
    return {
        "status": "EVALUATED_HOLDOUT",
        "edge": "UNPROVEN",
        "pairs": len(pairs),
        "resolved_pairs": len(joined),
        "train_rows": len(train),
        "test_rows": len(test),
        "train_markets": len({
            (item.pair.market_slug, item.pair.condition_id)
            for item in train
        }),
        "test_markets": len({
            (item.pair.market_slug, item.pair.condition_id)
            for item in test
        }),
        "calibrator": {
            "type": "PLATT_LOGISTIC_ON_RAW_SENEX_UP_SCORE",
            "intercept": calibrator.intercept,
            "slope": calibrator.slope,
            "fit_scope": "TRAIN_ONLY",
        },
        "holdout": metrics,
        "interpretation": (
            "DIAGNOSTIC_ONLY; EDGE remains UNPROVEN until uncertainty, "
            "dependence, cost, and prospective replication gates pass"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ORDER097 offline target-aligned market-prior calibration"
    )
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--resolutions")
    parser.add_argument("--train-fraction", type=float, default=0.67)
    args = parser.parse_args()
    result = run_offline(
        args.predictions,
        args.resolutions,
        train_fraction=args.train_fraction,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
