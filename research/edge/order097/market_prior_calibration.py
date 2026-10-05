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

import numpy as np


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


class LogisticModel(NamedTuple):
    intercept: float
    coefficients: tuple[float, ...]

    def predict(self, *features: float) -> float:
        if len(features) != len(self.coefficients):
            raise ValueError("feature width does not match fitted model")
        z = self.intercept
        for coefficient, feature in zip(self.coefficients, features):
            z += coefficient * feature
        return _sigmoid(z)


def _epoch_second(value: object, field: str) -> int:
    """Parse an exact integral epoch second without truncating fractions."""
    if isinstance(value, bool):
        raise ValueError(f"{field} is not an exact epoch second")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"{field} is not an exact epoch second")
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw or not raw.isdigit():
            raise ValueError(f"{field} is not an exact epoch second")
        return int(raw)
    raise ValueError(f"{field} is not an exact epoch second")


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
        start_ts = _epoch_second(poly.get("start_ts"), "market_start_ts")
        end_ts = _epoch_second(poly.get("end_ts"), "market_end_ts")
    except ValueError:
        return None
    if start_ts % TARGET_HORIZON_SECONDS != 0:
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
        start_ts = _epoch_second(raw.get("start_ts"), "resolution_start_ts")
        end_ts = _epoch_second(raw.get("end_ts"), "resolution_end_ts")
    except ValueError as exc:
        raise ResolutionContractError("resolution market grid is invalid") from exc
    if (
        start_ts % TARGET_HORIZON_SECONDS != 0
        or end_ts - start_ts != TARGET_HORIZON_SECONDS
        or _slug_start(slug) != start_ts
    ):
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
    """Chronological market split with a label-availability purge."""
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
    train_candidates = [
        item for item in rows
        if (item.pair.market_slug, item.pair.condition_id) in train_keys
    ]
    test = [
        item for item in rows
        if (item.pair.market_slug, item.pair.condition_id) not in train_keys
    ]
    if not test:
        raise ValueError("chronological holdout is empty")

    earliest_holdout_decision = min(item.pair.decision_ts for item in test)
    train = [
        item for item in train_candidates
        if item.resolved_at < earliest_holdout_decision
    ]
    return train, test


def _newton_refine_logistic(
    feature_rows: list[tuple[float, ...]],
    labels: list[int],
    params: list[float],
    *,
    l2: float,
    penalize_intercept: bool,
    tolerance: float = 1e-8,
    max_iter: int = 50,
) -> list[float]:
    """Refine a fixed-step GD solution only when it has not converged.

    ORDER097 historically used bounded fixed-step gradient descent. Keep that
    path as the primary optimizer so already-converged historical fits remain
    unchanged, then use penalized Newton/IRLS only when the final gradient is
    materially non-zero (for example, narrow low-variance feature ranges).
    """
    if not feature_rows:
        raise ValueError("feature rows are required")

    x = np.asarray(feature_rows, dtype=float)
    y = np.asarray(labels, dtype=float)
    design = np.column_stack([np.ones(len(x), dtype=float), x])
    beta = np.asarray(params, dtype=float)

    penalty = np.full(beta.shape, float(l2), dtype=float)
    if not penalize_intercept:
        penalty[0] = 0.0

    def state(current: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        logits = design @ current
        probabilities = np.empty_like(logits, dtype=float)
        positive = logits >= 0.0
        probabilities[positive] = 1.0 / (1.0 + np.exp(-logits[positive]))
        exp_logits = np.exp(logits[~positive])
        probabilities[~positive] = exp_logits / (1.0 + exp_logits)

        losses = np.logaddexp(0.0, logits) - y * logits
        objective = float(np.mean(losses))
        objective += 0.5 * float(np.sum(penalty * current * current))

        gradient = (design.T @ (probabilities - y)) / len(y)
        gradient = gradient + penalty * current

        weights = np.clip(
            probabilities * (1.0 - probabilities),
            1e-12,
            None,
        )
        hessian = (design.T @ (weights[:, None] * design)) / len(y)
        hessian = hessian + np.diag(penalty)
        return objective, gradient, hessian

    objective, gradient, hessian = state(beta)
    if float(np.max(np.abs(gradient))) <= tolerance:
        return [float(value) for value in beta]

    for _ in range(max_iter):
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hessian, gradient, rcond=None)[0]

        alpha = 1.0
        accepted = False
        while alpha >= 1e-8:
            candidate = beta - alpha * step
            candidate_objective, candidate_gradient, candidate_hessian = state(
                candidate
            )
            if candidate_objective <= objective:
                beta = candidate
                objective = candidate_objective
                gradient = candidate_gradient
                hessian = candidate_hessian
                accepted = True
                break
            alpha *= 0.5

        if not accepted:
            raise RuntimeError("logistic optimizer refinement failed line search")
        if float(np.max(np.abs(gradient))) <= tolerance:
            return [float(value) for value in beta]

    raise RuntimeError("logistic optimizer failed to converge")


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
    feature_rows = [
        (_logit(_probability(item.pair.senex_raw_up, "senex_raw_up")),)
        for item in rows
    ]
    label_rows = [int(item.label_up) for item in rows]
    n = float(len(rows))
    for _ in range(max_iter):
        grad_intercept = 0.0
        grad_slope = 0.0
        for item, feature_row in zip(rows, feature_rows):
            x = feature_row[0]
            pred = _sigmoid(intercept + slope * x)
            error = pred - int(item.label_up)
            grad_intercept += error
            grad_slope += error * x
        grad_intercept = grad_intercept / n + l2 * intercept
        grad_slope = grad_slope / n + l2 * slope
        intercept -= learning_rate * grad_intercept
        slope -= learning_rate * grad_slope

    intercept, slope = _newton_refine_logistic(
        feature_rows,
        label_rows,
        [intercept, slope],
        l2=l2,
        penalize_intercept=True,
    )
    return PlattCalibrator(intercept=intercept, slope=slope)


def _fit_logistic_features(
    observations: Iterable[JoinedObservation],
    feature_rows: Iterable[tuple[float, ...]],
    *,
    max_iter: int = 4000,
    learning_rate: float = 0.02,
    l2: float = 1e-4,
) -> LogisticModel:
    rows = list(observations)
    features = list(feature_rows)
    if len(rows) < 8:
        raise ValueError("at least 8 training observations are required")
    if len(features) != len(rows):
        raise ValueError("feature rows must match training observations")
    labels = {int(item.label_up) for item in rows}
    if labels != {0, 1}:
        raise ValueError("training observations require both UP and DOWN labels")
    if max_iter <= 0 or learning_rate <= 0:
        raise ValueError("optimizer parameters must be positive")
    width = len(features[0]) if features else 0
    if width <= 0 or any(len(row) != width for row in features):
        raise ValueError("feature matrix is invalid")

    intercept = 0.0
    coefficients = [0.0] * width
    label_rows = [int(item.label_up) for item in rows]
    n = float(len(rows))
    for _ in range(max_iter):
        grad_intercept = 0.0
        grad_coefficients = [0.0] * width
        for item, feature_row in zip(rows, features):
            z = intercept
            for coefficient, feature in zip(coefficients, feature_row):
                z += coefficient * feature
            pred = _sigmoid(z)
            error = pred - int(item.label_up)
            grad_intercept += error
            for index, feature in enumerate(feature_row):
                grad_coefficients[index] += error * feature

        intercept -= learning_rate * (grad_intercept / n)
        for index in range(width):
            gradient = grad_coefficients[index] / n + l2 * coefficients[index]
            coefficients[index] -= learning_rate * gradient

    refined = _newton_refine_logistic(
        features,
        label_rows,
        [intercept, *coefficients],
        l2=l2,
        penalize_intercept=False,
    )
    return LogisticModel(
        intercept=refined[0],
        coefficients=tuple(refined[1:]),
    )


def fit_incremental_models(
    observations: Iterable[JoinedObservation],
) -> tuple[LogisticModel, LogisticModel]:
    """Fit market-only and market+SENEX nested models on identical TRAIN rows."""
    rows = list(observations)
    market_features = [
        (_logit(_probability(item.pair.p_market, "p_market")),)
        for item in rows
    ]
    senex_features = [
        _logit(_probability(item.pair.senex_raw_up, "senex_raw_up"))
        for item in rows
    ]
    augmented_features = [
        (market_feature[0], senex_feature)
        for market_feature, senex_feature in zip(
            market_features,
            senex_features,
        )
    ]
    market_only = _fit_logistic_features(rows, market_features)

    # A zero-variance SENEX feature contains no incremental information and is
    # collinear with the intercept. Force the nested model to be exactly the
    # market-only fit rather than letting finite optimizer steps assign a
    # spurious coefficient to a constant score.
    if senex_features and max(senex_features) == min(senex_features):
        return (
            market_only,
            LogisticModel(
                intercept=market_only.intercept,
                coefficients=(market_only.coefficients[0], 0.0),
            ),
        )

    market_plus_senex = _fit_logistic_features(rows, augmented_features)
    return market_only, market_plus_senex


def evaluate_incremental_models(
    observations: Iterable[JoinedObservation],
    market_only: LogisticModel,
    market_plus_senex: LogisticModel,
) -> dict[str, float | int]:
    """Evaluate nested models on one identical chronological holdout."""
    rows = list(observations)
    if not rows:
        raise ValueError("paired evaluation requires resolved observations")

    labels = [int(item.label_up) for item in rows]
    market_probabilities = [
        market_only.predict(_logit(_probability(item.pair.p_market, "p_market")))
        for item in rows
    ]
    augmented_probabilities = [
        market_plus_senex.predict(
            _logit(_probability(item.pair.p_market, "p_market")),
            _logit(_probability(item.pair.senex_raw_up, "senex_raw_up")),
        )
        for item in rows
    ]

    market_brier = _brier(market_probabilities, labels)
    augmented_brier = _brier(augmented_probabilities, labels)
    market_log = _log_loss(market_probabilities, labels)
    augmented_log = _log_loss(augmented_probabilities, labels)
    market_acc = sum(
        (p >= 0.5) == bool(y)
        for p, y in zip(market_probabilities, labels)
    ) / len(labels)
    augmented_acc = sum(
        (p >= 0.5) == bool(y)
        for p, y in zip(augmented_probabilities, labels)
    ) / len(labels)
    markets = {
        (item.pair.market_slug, item.pair.condition_id)
        for item in rows
    }
    return {
        "n": len(rows),
        "n_markets": len(markets),
        "market_only_brier": market_brier,
        "market_plus_senex_brier": augmented_brier,
        "brier_delta_augmented_minus_market_only": augmented_brier - market_brier,
        "market_only_log_loss": market_log,
        "market_plus_senex_log_loss": augmented_log,
        "log_loss_delta_augmented_minus_market_only": augmented_log - market_log,
        "market_only_directional_accuracy": market_acc,
        "market_plus_senex_directional_accuracy": augmented_acc,
        "accuracy_delta_augmented_minus_market_only": augmented_acc - market_acc,
    }


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
    *,
    train_fraction: float = 0.67,
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
    resolved_markets = len({
        (item.pair.market_slug, item.pair.condition_id)
        for item in joined
    })
    if not joined:
        return {
            "status": "BLOCKED_NO_TARGET_ALIGNED_RESOLUTIONS",
            "edge": "UNPROVEN",
            "pairs": len(pairs_list),
            "resolved_pairs": 0,
            "resolved_markets": 0,
        }

    try:
        train, test = chronological_market_split(
            joined,
            train_fraction=train_fraction,
        )
    except ValueError as exc:
        return {
            "status": "BLOCKED_INSUFFICIENT_TARGET_ALIGNED_DATA",
            "edge": "UNPROVEN",
            "pairs": len(pairs_list),
            "resolved_pairs": len(joined),
            "resolved_markets": resolved_markets,
            "blocker_reason": str(exc),
        }

    if len(train) < 8:
        return {
            "status": "BLOCKED_INSUFFICIENT_TARGET_ALIGNED_DATA",
            "edge": "UNPROVEN",
            "pairs": len(pairs_list),
            "resolved_pairs": len(joined),
            "resolved_markets": resolved_markets,
            "train_rows": len(train),
            "test_rows": len(test),
            "blocker_reason": "at least 8 causally available training observations are required",
        }

    if {int(item.label_up) for item in train} != {0, 1}:
        return {
            "status": "BLOCKED_INSUFFICIENT_TARGET_ALIGNED_DATA",
            "edge": "UNPROVEN",
            "pairs": len(pairs_list),
            "resolved_pairs": len(joined),
            "resolved_markets": resolved_markets,
            "train_rows": len(train),
            "test_rows": len(test),
            "blocker_reason": "training observations require both UP and DOWN labels",
        }

    return {
        "status": "READY_FOR_CHRONOLOGICAL_CALIBRATION",
        "edge": "UNPROVEN",
        "pairs": len(pairs_list),
        "resolved_pairs": len(joined),
        "resolved_markets": resolved_markets,
        "train_rows": len(train),
        "test_rows": len(test),
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
    inventory = {
        "total_rows": len(rows),
        "valid_pairs": len(pairs),
        "rejected_pairs": len(rows) - len(pairs),
        "unique_markets": len({
            (pair.market_slug, pair.condition_id)
            for pair in pairs
        }),
    }

    resolutions = read_jsonl(resolutions_path) if resolutions_path else []
    status = experiment_status(
        pairs,
        resolutions,
        train_fraction=train_fraction,
    )
    if status["status"] != "READY_FOR_CHRONOLOGICAL_CALIBRATION":
        return {**status, **inventory}

    joined = join_resolutions(pairs, resolutions)
    train, test = chronological_market_split(
        joined,
        train_fraction=train_fraction,
    )
    market_only, market_plus_senex = fit_incremental_models(train)
    metrics = evaluate_incremental_models(
        test,
        market_only,
        market_plus_senex,
    )
    return {
        "status": "EVALUATED_HOLDOUT",
        "edge": "UNPROVEN",
        "pairs": len(pairs),
        "resolved_pairs": len(joined),
        **inventory,
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
        "models": {
            "market_only": {
                "type": "LOGISTIC_ON_MARKET_PRIOR_LOGIT",
                "intercept": market_only.intercept,
                "coefficients": list(market_only.coefficients),
                "fit_scope": "TRAIN_ONLY",
            },
            "market_plus_senex": {
                "type": "LOGISTIC_ON_MARKET_PRIOR_PLUS_SENEX_LOGITS",
                "intercept": market_plus_senex.intercept,
                "coefficients": list(market_plus_senex.coefficients),
                "fit_scope": "TRAIN_ONLY",
            },
        },
        "holdout": metrics,
        "interpretation": (
            "DIAGNOSTIC_ONLY; incremental comparison is market-only versus "
            "market+SENEX on identical causally valid rows; EDGE remains "
            "UNPROVEN until uncertainty, dependence, cost, and prospective "
            "replication gates pass"
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
