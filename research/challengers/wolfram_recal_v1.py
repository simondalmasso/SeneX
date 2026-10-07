from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Sequence

import numpy as np

from .common import ChallengerContractError, Observation, canonical_numeric, sha256_json, validate_observation


CHALLENGER_ID = "WOLFRAM_RECAL_V1"
METHOD = "LOGIT_RECALIBRATION_NEWTON_V1"
DEFAULT_CLIP_EPSILON = 1e-6
DEFAULT_L2_TO_IDENTITY = 1e-6
DEFAULT_MAX_ITER = 100
DEFAULT_TOLERANCE = 1e-10


@dataclass(frozen=True)
class RecalibrationModel:
    challenger_id: str
    method: str
    intercept: float
    slope: float
    clip_epsilon: float
    l2_to_identity: float
    iterations: int
    converged: bool

    def digest(self) -> str:
        return sha256_json(canonical_numeric(asdict(self)))


def _clip_probability(value: float, epsilon: float) -> float:
    p = float(value)
    if not math.isfinite(p) or not 0.0 <= p <= 1.0:
        raise ChallengerContractError("raw probability must be finite in [0,1]")
    return min(1.0 - epsilon, max(epsilon, p))


def _logit(value: float, epsilon: float) -> float:
    p = _clip_probability(value, epsilon)
    return math.log(p / (1.0 - p))


def _sigmoid_array(values: np.ndarray) -> np.ndarray:
    out = np.empty_like(values, dtype=float)
    positive = values >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    exp_values = np.exp(values[~positive])
    out[~positive] = exp_values / (1.0 + exp_values)
    return out


def fit_recalibration(
    raw_probabilities: Sequence[float],
    labels: Sequence[int],
    *,
    clip_epsilon: float = DEFAULT_CLIP_EPSILON,
    l2_to_identity: float = DEFAULT_L2_TO_IDENTITY,
    max_iter: int = DEFAULT_MAX_ITER,
    tolerance: float = DEFAULT_TOLERANCE,
) -> RecalibrationModel:
    """Fit logit(p_cal)=a+b*logit(p_raw) with deterministic Newton steps.

    Wolfram independently verified the Bernoulli log-loss gradient as
    (p-y)[1,x] and Hessian as p(1-p)[[1,x],[x,x^2]].
    The tiny L2 penalty is centered on identity calibration (a=0,b=1).
    """
    if len(raw_probabilities) != len(labels) or len(labels) < 4:
        raise ChallengerContractError("recalibration requires at least four aligned rows")
    if set(labels) - {0, 1}:
        raise ChallengerContractError("labels must be binary")
    if len(set(labels)) < 2:
        raise ChallengerContractError("recalibration requires both label classes")
    if not 0.0 < clip_epsilon < 0.5:
        raise ChallengerContractError("clip_epsilon must be in (0,0.5)")
    if l2_to_identity < 0 or not math.isfinite(l2_to_identity):
        raise ChallengerContractError("l2_to_identity must be finite and non-negative")
    if max_iter < 1 or tolerance <= 0:
        raise ChallengerContractError("invalid optimizer controls")

    x = np.array(
        [_logit(value, clip_epsilon) for value in raw_probabilities],
        dtype=float,
    )
    y = np.array(labels, dtype=float)
    design = np.column_stack([np.ones_like(x), x])
    beta = np.array([0.0, 1.0], dtype=float)
    target = np.array([0.0, 1.0], dtype=float)
    converged = False
    iterations = 0

    for iterations in range(1, max_iter + 1):
        eta = design @ beta
        p = _sigmoid_array(eta)
        gradient = design.T @ (p - y) + l2_to_identity * (beta - target)
        weights = np.maximum(p * (1.0 - p), 1e-12)
        hessian = design.T @ (design * weights[:, None])
        hessian += np.eye(2, dtype=float) * l2_to_identity
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError as exc:
            raise ChallengerContractError("recalibration Hessian is singular") from exc
        if not np.all(np.isfinite(step)):
            raise ChallengerContractError("recalibration Newton step is non-finite")
        beta = beta - step
        if float(np.max(np.abs(step))) <= tolerance:
            converged = True
            break

    if not converged:
        raise ChallengerContractError("recalibration did not converge")

    # V1 is a recalibrator, not an anti-signal flipper. Because the penalized
    # Bernoulli objective is convex, if the unconstrained optimum has b < 0,
    # the optimum under the frozen monotonic constraint b >= 0 lies at b = 0.
    if beta[1] < 0.0:
        intercept = float(beta[0])
        boundary_converged = False
        boundary_iterations = 0
        for boundary_iterations in range(1, max_iter + 1):
            eta = np.full_like(y, intercept, dtype=float)
            p = _sigmoid_array(eta)
            gradient = float(np.sum(p - y) + l2_to_identity * intercept)
            hessian = float(
                np.sum(np.maximum(p * (1.0 - p), 1e-12))
                + l2_to_identity
            )
            step = gradient / hessian
            if not math.isfinite(step):
                raise ChallengerContractError(
                    "monotonic-boundary Newton step is non-finite"
                )
            intercept -= step
            if abs(step) <= tolerance:
                boundary_converged = True
                break
        if not boundary_converged:
            raise ChallengerContractError(
                "monotonic-boundary recalibration did not converge"
            )
        beta = np.array([intercept, 0.0], dtype=float)
        iterations = boundary_iterations

    if not np.all(np.isfinite(beta)):
        raise ChallengerContractError("recalibration coefficients are non-finite")

    return RecalibrationModel(
        challenger_id=CHALLENGER_ID,
        method=METHOD,
        intercept=float(beta[0]),
        slope=float(beta[1]),
        clip_epsilon=float(clip_epsilon),
        l2_to_identity=float(l2_to_identity),
        iterations=iterations,
        converged=True,
    )


def predict(model: RecalibrationModel, raw_probabilities: Sequence[float]) -> list[float]:
    if model.challenger_id != CHALLENGER_ID or model.method != METHOD:
        raise ChallengerContractError("unexpected recalibration model contract")
    values = np.array(
        [_logit(value, model.clip_epsilon) for value in raw_probabilities],
        dtype=float,
    )
    eta = model.intercept + model.slope * values
    probabilities = _sigmoid_array(eta)
    clipped = np.clip(
        probabilities,
        model.clip_epsilon,
        1.0 - model.clip_epsilon,
    )
    return [float(value) for value in clipped]


def fit_from_observations(
    rows: Sequence[Observation],
    train_indices: Sequence[int],
) -> RecalibrationModel:
    probabilities: list[float] = []
    labels: list[int] = []
    for idx in train_indices:
        row = rows[idx]
        validate_observation(row)
        if row.senex_raw_up is None:
            raise ChallengerContractError("WOLFRAM_RECAL_V1 requires senex_raw_up")
        probabilities.append(row.senex_raw_up)
        labels.append(row.label)
    return fit_recalibration(probabilities, labels)
