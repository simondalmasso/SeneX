from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.edge.order097 import market_prior_calibration as order097
from research.edge.order099 import tabular_falsification as order099
from senecio_polymarket.backend.research._calibration_primitives import (
    expected_calibration_error,
    maximum_calibration_error,
    reliability_curve,
)


PRIMARY_KEYS = (
    "market_only_brier",
    "market_plus_senex_brier",
    "market_only_log_loss",
    "market_plus_senex_log_loss",
)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    z = np.asarray(values, dtype=float)
    out = np.empty_like(z)
    positive = z >= 0.0
    out[positive] = 1.0 / (1.0 + np.exp(-z[positive]))
    exp_z = np.exp(z[~positive])
    out[~positive] = exp_z / (1.0 + exp_z)
    return out


def _logit(values: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(values, dtype=float), 1e-6, 1.0 - 1e-6)
    return np.log(p / (1.0 - p))


def _metrics(labels: np.ndarray, probabilities: np.ndarray) -> dict[str, float]:
    y = np.asarray(labels, dtype=float)
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-12, 1.0 - 1e-12)
    return {
        "brier": float(np.mean((p - y) ** 2)),
        "log_loss": float(np.mean(-(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))),
        "accuracy": float(np.mean((p >= 0.5) == (y >= 0.5))),
    }


def assert_primary_reproduction(
    actual: dict[str, float],
    manifest: dict[str, Any],
    *,
    tolerance: float = 1e-12,
) -> None:
    primary = manifest.get("primary")
    if not isinstance(primary, dict):
        raise ValueError("PRIMARY_REPRO_MANIFEST_MISSING")
    for key in PRIMARY_KEYS:
        if key not in actual or key not in primary:
            raise ValueError(f"PRIMARY_REPRO_KEY_MISSING:{key}")
        expected = float(primary[key])
        observed = float(actual[key])
        if not math.isfinite(observed) or abs(observed - expected) > tolerance:
            raise ValueError(
                f"PRIMARY_REPRO_MISMATCH:{key}:observed={observed}:expected={expected}"
            )


def fit_calibration(
    labels: np.ndarray,
    probabilities: np.ndarray,
    *,
    max_iter: int = 100,
    tolerance: float = 1e-11,
) -> tuple[float, float]:
    y = np.asarray(labels, dtype=float)
    x = _logit(probabilities)
    design = np.column_stack([np.ones(len(x), dtype=float), x])
    beta = np.array([0.0, 1.0], dtype=float)
    for _ in range(max_iter):
        fitted = _sigmoid(design @ beta)
        weights = np.clip(fitted * (1.0 - fitted), 1e-10, None)
        gradient = design.T @ (y - fitted)
        hessian = design.T @ (weights[:, None] * design)
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hessian, gradient, rcond=None)[0]
        beta = beta + step
        if float(np.max(np.abs(step))) <= tolerance:
            return float(beta[0]), float(beta[1])
    raise RuntimeError("CALIBRATION_FIT_DID_NOT_CONVERGE")


def apply_calibration(
    probabilities: np.ndarray,
    intercept: float,
    slope: float,
) -> np.ndarray:
    return _sigmoid(float(intercept) + float(slope) * _logit(probabilities))


def calibration_summary(
    labels: np.ndarray,
    probabilities: np.ndarray,
    *,
    n_bins: int = 10,
) -> dict[str, Any]:
    intercept, slope = fit_calibration(labels, probabilities)
    curve = reliability_curve(labels, probabilities, n_bins=n_bins)
    bins = []
    for index, count in enumerate(curve["counts"]):
        if int(count) <= 0:
            continue
        low, high = curve["bins"][index]
        bins.append(
            {
                "lo": float(low),
                "hi": float(high),
                "n": int(count),
                "mean_pred": float(curve["mean_predicted"][index]),
                "observed_rate": float(curve["fraction_positive"][index]),
            }
        )
    return {
        "intercept": intercept,
        "slope": slope,
        "ece": float(expected_calibration_error(labels, probabilities, n_bins)),
        "mce": float(maximum_calibration_error(labels, probabilities, n_bins)),
        **_metrics(labels, probabilities),
        "reliability_bins": bins,
    }


def _model_probabilities(rows: list[Any], model: Any, *, augmented: bool) -> np.ndarray:
    output = []
    for item in rows:
        market_logit = order097._logit(float(item.pair.p_market))
        if augmented:
            senex_logit = order097._logit(float(item.pair.senex_raw_up))
            output.append(model.predict(market_logit, senex_logit))
        else:
            output.append(model.predict(market_logit))
    return np.asarray(output, dtype=float)


def _labels(rows: list[Any]) -> np.ndarray:
    return np.asarray([int(item.label_up) for item in rows], dtype=float)


def _bootstrap_calibration(
    labels: np.ndarray,
    probabilities: np.ndarray,
    *,
    replicates: int,
    seed: int,
) -> dict[str, list[float]]:
    if replicates <= 0:
        return {}
    rng = np.random.default_rng(seed)
    intercepts: list[float] = []
    slopes: list[float] = []
    eces: list[float] = []
    for _ in range(replicates):
        index = rng.integers(0, len(labels), size=len(labels))
        y = labels[index]
        p = probabilities[index]
        try:
            intercept, slope = fit_calibration(y, p)
        except (RuntimeError, np.linalg.LinAlgError):
            continue
        intercepts.append(intercept)
        slopes.append(slope)
        eces.append(float(expected_calibration_error(y, p, 10)))

    def interval(values: list[float]) -> list[float]:
        data = np.asarray(values, dtype=float)
        return [float(np.quantile(data, 0.025)), float(np.quantile(data, 0.975))]

    return {
        "intercept_ci95": interval(intercepts),
        "slope_ci95": interval(slopes),
        "ece10_ci95": interval(eces),
    }


def _paired_delta(
    labels: np.ndarray,
    candidate: np.ndarray,
    baseline: np.ndarray,
    *,
    metric: str,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(replicates):
        index = rng.integers(0, len(labels), size=len(labels))
        y = labels[index]
        a = candidate[index]
        b = baseline[index]
        if metric == "brier":
            delta = np.mean((a - y) ** 2 - (b - y) ** 2)
        else:
            aa = np.clip(a, 1e-12, 1.0 - 1e-12)
            bb = np.clip(b, 1e-12, 1.0 - 1e-12)
            la = -(y * np.log(aa) + (1.0 - y) * np.log(1.0 - aa))
            lb = -(y * np.log(bb) + (1.0 - y) * np.log(1.0 - bb))
            delta = np.mean(la - lb)
        values.append(float(delta))
    data = np.asarray(values, dtype=float)
    return {
        "mean": float(np.mean(data)),
        "ci95": [float(np.quantile(data, 0.025)), float(np.quantile(data, 0.975))],
    }


def build_report(
    predictions_path: Path,
    predictions_manifest_path: Path,
    resolutions_path: Path,
    resolutions_manifest_path: Path,
    run_manifest_path: Path,
    *,
    bootstrap: int = 1000,
    seed: int = 7,
) -> dict[str, Any]:
    provenance = order099.verify_order098_artifacts(
        predictions_path,
        predictions_manifest_path,
        resolutions_path,
        resolutions_manifest_path,
    )
    predictions = order097.read_jsonl(predictions_path)
    resolutions = order097.read_jsonl(resolutions_path)
    pairs = order097.extract_t0_pairs(predictions)
    joined = order097.join_resolutions(pairs, resolutions)
    train_source, holdout_source = order097.chronological_market_split(
        joined,
        train_fraction=0.67,
    )
    train = order099.unique_market_observations(train_source)
    holdout = order099.unique_market_observations(holdout_source)

    market_model, augmented_model = order097.fit_incremental_models(train)
    loss_rows = order099.nested_loss_rows(holdout, market_model, augmented_model)
    labels = np.asarray([int(row["label_up"]) for row in loss_rows], dtype=float)
    market = np.asarray([float(row["market_probability"]) for row in loss_rows], dtype=float)
    augmented = np.asarray(
        [float(row["augmented_probability"]) for row in loss_rows],
        dtype=float,
    )
    market_metrics = _metrics(labels, market)
    augmented_metrics = _metrics(labels, augmented)
    primary = {
        "market_only_brier": market_metrics["brier"],
        "market_plus_senex_brier": augmented_metrics["brier"],
        "market_only_log_loss": market_metrics["log_loss"],
        "market_plus_senex_log_loss": augmented_metrics["log_loss"],
    }
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    assert_primary_reproduction(primary, run_manifest)

    calibration = {
        "market_only": calibration_summary(labels, market),
        "market_plus_senex": calibration_summary(labels, augmented),
        "bootstrap": {
            "market_only": _bootstrap_calibration(
                labels, market, replicates=bootstrap, seed=seed
            ),
            "market_plus_senex": _bootstrap_calibration(
                labels, augmented, replicates=bootstrap, seed=seed
            ),
        },
    }

    inner_fit_source, inner_dev_source = order097.chronological_market_split(
        train,
        train_fraction=0.67,
    )
    inner_fit = order099.unique_market_observations(inner_fit_source)
    inner_dev = order099.unique_market_observations(inner_dev_source)
    inner_market, inner_augmented = order097.fit_incremental_models(inner_fit)
    dev_labels = _labels(inner_dev)
    holdout_labels = _labels(holdout)
    dev_market = _model_probabilities(inner_dev, inner_market, augmented=False)
    dev_augmented = _model_probabilities(inner_dev, inner_augmented, augmented=True)
    hold_market = _model_probabilities(holdout, inner_market, augmented=False)
    hold_augmented = _model_probabilities(holdout, inner_augmented, augmented=True)

    market_intercept, market_slope = fit_calibration(dev_labels, dev_market)
    aug_intercept, aug_slope = fit_calibration(dev_labels, dev_augmented)
    recal_market = apply_calibration(hold_market, market_intercept, market_slope)
    recal_augmented = apply_calibration(
        hold_augmented,
        aug_intercept,
        aug_slope,
    )

    lambdas = np.linspace(0.0, 1.0, 101)
    scored = []
    for value in lambdas:
        blend = (1.0 - value) * dev_market + value * dev_augmented
        scored.append((_metrics(dev_labels, blend)["brier"], float(value)))
    best_dev_brier, best_lambda = min(scored)
    hold_blend = (1.0 - best_lambda) * hold_market + best_lambda * hold_augmented

    challenger_bootstrap = max(1, bootstrap)
    challengers = {
        "status": "EXPLORATORY_HISTORICAL_ONLY",
        "inner_fit_markets": len(inner_fit),
        "inner_dev_markets": len(inner_dev),
        "recalibration": {
            "market_params": {"intercept": market_intercept, "slope": market_slope},
            "augmented_params": {"intercept": aug_intercept, "slope": aug_slope},
            "market_holdout": _metrics(holdout_labels, recal_market),
            "augmented_holdout": _metrics(holdout_labels, recal_augmented),
            "paired_brier_delta": _paired_delta(
                holdout_labels,
                recal_augmented,
                recal_market,
                metric="brier",
                replicates=challenger_bootstrap,
                seed=seed,
            ),
            "paired_log_loss_delta": _paired_delta(
                holdout_labels,
                recal_augmented,
                recal_market,
                metric="log_loss",
                replicates=challenger_bootstrap,
                seed=seed + 1,
            ),
        },
        "shrinkage": {
            "lambda_augmented_selected_on_dev_brier": best_lambda,
            "dev_brier": best_dev_brier,
            "holdout": _metrics(holdout_labels, hold_blend),
            "paired_brier_delta_vs_market": _paired_delta(
                holdout_labels,
                hold_blend,
                hold_market,
                metric="brier",
                replicates=challenger_bootstrap,
                seed=seed + 2,
            ),
            "paired_log_loss_delta_vs_market": _paired_delta(
                holdout_labels,
                hold_blend,
                hold_market,
                metric="log_loss",
                replicates=challenger_bootstrap,
                seed=seed + 3,
            ),
        },
    }

    return {
        "contract": "senex-order099-historical-calibration-v1",
        "scope": "HISTORICAL_OPENED_HOLDOUT_DESCRIPTIVE_ONLY",
        "artifact_provenance": provenance,
        "train_markets": len(train),
        "holdout_markets": len(holdout),
        "primary_reproduction": primary,
        "calibration": calibration,
        "challengers": challengers,
        "guardrails": [
            "PRIMARY_ORDER099_REPRODUCED_BEFORE_DIAGNOSTICS",
            "NO_PROSPECTIVE_LABELS_USED",
            "NO_RUNTIME_CHANGE",
            "NO_FEATURE_ADDITION",
            "EDGE_REMAINS_UNPROVEN",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--predictions-manifest", type=Path, required=True)
    parser.add_argument("--resolutions", type=Path, required=True)
    parser.add_argument("--resolutions-manifest", type=Path, required=True)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    report = build_report(
        args.predictions,
        args.predictions_manifest,
        args.resolutions,
        args.resolutions_manifest,
        args.run_manifest,
        bootstrap=max(0, int(args.bootstrap)),
        seed=int(args.seed),
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
