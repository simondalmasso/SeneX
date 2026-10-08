from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

import numpy as np

from .common import (
    ChallengerContractError,
    Observation,
    PurgedSplit,
    canonical_numeric,
    canonical_ts,
    logit,
    proper_score_report,
    sha256_json,
    sigmoid,
    validate_observation,
    validate_purged_splits,
)
from .evaluation import fit_market_residual_stack
from .recency_challenger_v1 import (
    CHALLENGER_ID as RECENCY_ID,
    FEATURE_ORDER,
    feature_vector,
)
from .wolfram_recal_v1 import (
    CHALLENGER_ID as WOLFRAM_ID,
    fit_from_observations,
    predict as predict_recalibrated,
)


DEFAULT_OFFSET_L2 = 1e-4
DEFAULT_MAX_ITER = 100
DEFAULT_TOLERANCE = 1e-9
CLIP_EPSILON = 1e-6


@dataclass(frozen=True)
class MarketOffsetModel:
    challenger_id: str
    feature_names: tuple[str, ...]
    intercept: float
    coefficients: tuple[float, ...]
    l2: float
    iterations: int
    converged: bool

    def digest(self) -> str:
        # Hash the predictive artifact, not optimizer-path diagnostics.
        # LAPACK/NumPy builds may converge in a different iteration count or
        # differ below economically meaningful precision while producing the
        # same model. Freeze the predictive parameters/hyperparameters only.
        payload = asdict(self)
        payload.pop("iterations", None)
        return sha256_json(canonical_numeric(payload, digits=8))


def _clip(value: float) -> float:
    p = float(value)
    if not math.isfinite(p) or not 0.0 <= p <= 1.0:
        raise ChallengerContractError("market probability must be finite in [0,1]")
    return min(1.0 - CLIP_EPSILON, max(CLIP_EPSILON, p))


def _logit(value: float) -> float:
    p = _clip(value)
    return math.log(p / (1.0 - p))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    out = np.empty_like(values, dtype=float)
    positive = values >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    exp_values = np.exp(values[~positive])
    out[~positive] = exp_values / (1.0 + exp_values)
    return out


def fit_market_offset_model(
    p_market: Sequence[float],
    features: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    l2: float = DEFAULT_OFFSET_L2,
    max_iter: int = DEFAULT_MAX_ITER,
    tolerance: float = DEFAULT_TOLERANCE,
) -> MarketOffsetModel:
    """Fit logit(p)=logit(p_market)+a+beta*x on training data only."""
    if not (len(p_market) == len(features) == len(labels)) or len(labels) < 4:
        raise ChallengerContractError("market-offset fit requires >=4 aligned rows")
    if set(labels) - {0, 1} or len(set(labels)) < 2:
        raise ChallengerContractError("market-offset fit requires both binary classes")
    if not math.isfinite(l2) or l2 <= 0:
        raise ChallengerContractError("market-offset l2 must be finite and positive")

    width = len(FEATURE_ORDER)
    if any(len(row) != width for row in features):
        raise ChallengerContractError("recency feature vector width mismatch")

    x = np.asarray(features, dtype=float)
    if not np.all(np.isfinite(x)):
        raise ChallengerContractError("recency training features must be finite")
    design = np.column_stack([np.ones(len(x), dtype=float), x])
    offset = np.asarray([_logit(value) for value in p_market], dtype=float)
    y = np.asarray(labels, dtype=float)
    beta = np.zeros(design.shape[1], dtype=float)
    converged = False
    iterations = 0

    penalty = np.eye(design.shape[1], dtype=float) * l2
    penalty[0, 0] = l2 * 0.1

    for iterations in range(1, max_iter + 1):
        eta = offset + design @ beta
        p = _sigmoid(eta)
        gradient = design.T @ (p - y) + penalty @ beta
        weights = np.maximum(p * (1.0 - p), 1e-12)
        hessian = design.T @ (design * weights[:, None]) + penalty
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError as exc:
            raise ChallengerContractError("market-offset Hessian is singular") from exc
        if not np.all(np.isfinite(step)):
            raise ChallengerContractError("market-offset Newton step is non-finite")
        beta = beta - step
        if float(np.max(np.abs(step))) <= tolerance:
            converged = True
            break

    if not converged or not np.all(np.isfinite(beta)):
        raise ChallengerContractError("market-offset fit did not converge")

    return MarketOffsetModel(
        challenger_id=RECENCY_ID,
        feature_names=tuple(FEATURE_ORDER),
        intercept=float(beta[0]),
        coefficients=tuple(float(value) for value in beta[1:]),
        l2=float(l2),
        iterations=iterations,
        converged=True,
    )


def predict_market_offset(
    model: MarketOffsetModel,
    p_market: Sequence[float],
    features: Sequence[Sequence[float]],
) -> list[float]:
    if model.challenger_id != RECENCY_ID:
        raise ChallengerContractError("unexpected market-offset challenger id")
    if tuple(model.feature_names) != tuple(FEATURE_ORDER):
        raise ChallengerContractError("market-offset feature contract mismatch")
    if len(p_market) != len(features):
        raise ChallengerContractError("market-offset prediction inputs are misaligned")
    x = np.asarray(features, dtype=float)
    if x.ndim != 2 or x.shape[1] != len(FEATURE_ORDER):
        raise ChallengerContractError("market-offset prediction feature width mismatch")
    if not np.all(np.isfinite(x)):
        raise ChallengerContractError("market-offset prediction features must be finite")
    offset = np.asarray([_logit(value) for value in p_market], dtype=float)
    beta = np.asarray([model.intercept, *model.coefficients], dtype=float)
    design = np.column_stack([np.ones(len(x), dtype=float), x])
    return [float(value) for value in _sigmoid(offset + design @ beta)]


def _recency_vector_for_observation(
    row: Observation,
    record: Mapping[str, object],
) -> list[float]:
    if set(record) != {"cutoff_ts", "features"}:
        raise ChallengerContractError("recency feature record must contain cutoff_ts and features")
    if canonical_ts(str(record["cutoff_ts"])) != canonical_ts(row.decision_ts):
        raise ChallengerContractError(
            f"recency feature cutoff must match observation decision_ts for {row.market_id}"
        )
    features = record["features"]
    if not isinstance(features, dict):
        raise ChallengerContractError("recency feature payload must be an object")
    return feature_vector(features)


def evaluate_historical_folds(
    rows: Sequence[Observation],
    recency_features: Mapping[str, Mapping[str, object]],
    splits: Sequence[PurgedSplit],
) -> dict[str, dict]:
    if len({row.market_id for row in rows}) != len(rows):
        raise ChallengerContractError("historical challenger rows must be unique by market_id")
    validate_purged_splits(rows, splits)

    collected: dict[str, dict[str, list]] = {
        WOLFRAM_ID: {"labels": [], "market": [], "candidate": [], "folds": []},
        RECENCY_ID: {"labels": [], "market": [], "candidate": [], "folds": []},
    }

    for fold_index, split in enumerate(splits):
        test_rows = [rows[idx] for idx in split.test_indices]
        labels = [row.label for row in test_rows]
        market = [row.p_market for row in test_rows]

        recal_model = fit_from_observations(rows, split.train_indices)
        raw_test = []
        for row in test_rows:
            if row.senex_raw_up is None:
                raise ChallengerContractError("WOLFRAM_RECAL_V1 requires senex_raw_up")
            raw_test.append(row.senex_raw_up)
        recal_probs = predict_recalibrated(recal_model, raw_test)

        train_rows = [rows[idx] for idx in split.train_indices]
        train_features = []
        for row in train_rows:
            if row.market_id not in recency_features:
                raise ChallengerContractError(
                    f"missing recency features for training market {row.market_id}"
                )
            train_features.append(
                _recency_vector_for_observation(row, recency_features[row.market_id])
            )
        offset_model = fit_market_offset_model(
            [row.p_market for row in train_rows],
            train_features,
            [row.label for row in train_rows],
        )
        test_features = []
        for row in test_rows:
            if row.market_id not in recency_features:
                raise ChallengerContractError(
                    f"missing recency features for test market {row.market_id}"
                )
            test_features.append(
                _recency_vector_for_observation(row, recency_features[row.market_id])
            )
        recency_probs = predict_market_offset(offset_model, market, test_features)

        for challenger_id, probs, model_digest in (
            (WOLFRAM_ID, recal_probs, recal_model.digest()),
            (RECENCY_ID, recency_probs, offset_model.digest()),
        ):
            report = proper_score_report(labels, market, probs)
            report["fold_index"] = fold_index
            report["model_digest"] = model_digest
            collected[challenger_id]["folds"].append(report)
            collected[challenger_id]["labels"].extend(labels)
            collected[challenger_id]["market"].extend(market)
            collected[challenger_id]["candidate"].extend(probs)

    output: dict[str, dict] = {}
    for challenger_id, payload in collected.items():
        overall = proper_score_report(
            payload["labels"], payload["market"], payload["candidate"]
        )
        output[challenger_id] = {
            "challenger_id": challenger_id,
            "overall": overall,
            "folds": payload["folds"],
            "oos_market_count": len(payload["labels"]),
            "valid": True,
        }
    return output


def evaluate_market_residual_folds(
    rows: Sequence[Observation],
    splits: Sequence[PurgedSplit],
) -> dict:
    """Purged OOS diagnostic for whether SENEX adds information beyond p_market.

    This diagnostic is deliberately not a third V1 prospective challenger.
    """
    if len({row.market_id for row in rows}) != len(rows):
        raise ChallengerContractError("residual diagnostic rows must be unique by market_id")
    validate_purged_splits(rows, splits)

    labels_all: list[int] = []
    market_all: list[float] = []
    candidate_all: list[float] = []
    fold_reports: list[dict] = []

    for fold_index, split in enumerate(splits):
        train_rows = [rows[idx] for idx in split.train_indices]
        test_rows = [rows[idx] for idx in split.test_indices]
        if any(row.senex_raw_up is None for row in [*train_rows, *test_rows]):
            raise ChallengerContractError(
                "market residual diagnostic requires senex_raw_up"
            )

        fitted = fit_market_residual_stack(
            [row.p_market for row in train_rows],
            [float(row.senex_raw_up) for row in train_rows],
            [row.label for row in train_rows],
        )
        beta = float(fitted["beta_senex_residual"])
        test_market = [row.p_market for row in test_rows]
        test_labels = [row.label for row in test_rows]
        test_candidate = [
            sigmoid(
                logit(row.p_market)
                + beta
                * (
                    logit(float(row.senex_raw_up))
                    - logit(row.p_market)
                )
            )
            for row in test_rows
        ]
        report = proper_score_report(test_labels, test_market, test_candidate)
        report["fold_index"] = fold_index
        report["beta_senex_residual"] = beta
        fold_reports.append(report)
        labels_all.extend(test_labels)
        market_all.extend(test_market)
        candidate_all.extend(test_candidate)

    return {
        "diagnostic_id": "MARKET_PLUS_SENEX_RESIDUAL_OOS_V1",
        "selection_eligible": False,
        "oos_market_count": len(labels_all),
        "overall": proper_score_report(labels_all, market_all, candidate_all),
        "folds": fold_reports,
    }


def select_candidates(
    reports: Mapping[str, dict],
    *,
    max_candidates: int = 2,
) -> list[str]:
    """Frozen V1 selection: non-worse on both proper scores, then rank by Brier."""
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
        raise ChallengerContractError("max_candidates must be an integer")
    if not 1 <= max_candidates <= 2:
        raise ChallengerContractError("V1 allows at most two prospective challengers")

    eligible: list[tuple[float, float, str]] = []
    for challenger_id, report in reports.items():
        if report.get("valid") is not True:
            continue
        overall = report.get("overall")
        if not isinstance(overall, dict):
            continue
        try:
            delta_brier = float(overall["delta_brier"])
            delta_log = float(overall["delta_log_loss"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(delta_brier) or not math.isfinite(delta_log):
            continue
        if delta_brier <= 0.0 and delta_log <= 0.0:
            eligible.append((delta_brier, delta_log, challenger_id))

    eligible.sort()
    return [challenger_id for _, _, challenger_id in eligible[:max_candidates]]
