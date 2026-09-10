"""
SENECIO ORACLE — ACT XXVIII Module 1: Walk-Forward Optimizer
==============================================================

Implements three leakage-resistant walk-forward schemes plus a parameter
stability analyser.  All schemes consume a labelled series of trades /
predictions and emit a per-window performance report plus an aggregate
"stability score" that quantifies how brittle a strategy is across
regimes.

Schemes:
  1. ROLLING  — fixed-size train/test window that slides forward by
                `step` each iteration. Both windows have the same size.
  2. ANCHORED — test window is fixed-size; train window expands (every
                new test adds its samples back to the train set).
  3. EXPANDING— both windows expand (train size grows by `step` each
                iteration, test size also grows by `step`). Useful for
                very small datasets where rolling would discard too
                much data.

Parameter stability is measured by repeatedly evaluating a *callable*
that produces a metric for each window's prediction set, then computing:
  - mean & std of the metric across windows
  - coefficient of variation (CV = std / |mean|)
  - degradation ratio (last-window metric / first-window metric)
  - fraction of windows where the metric exceeded `min_passable`
  - Sharpe of the per-window metric stream

The optimizer is callable-agnostic: any function
`score_fn(y_true_train, y_pred_train, y_true_test, y_pred_test) -> dict`
works. A default `score_fn` (returns accuracy, precision, recall, F1,
Brier score, IC) is provided.

Reports are persisted as JSONL under `data/research/walkforward_reports/`
so every historical run is auditable.

STRICT_ADDITIVE — does NOT touch:
  - prediction_model (predict_only.py)
  - feature_engineering (institutional_core.py)
  - signal_generation (institutional_core.py)
  - verifier (oracle_runner.py)
  - existing live-gate logic
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np

log = logging.getLogger("senecio.research.walkforward")


DEFAULTS: dict[str, Any] = {
    "reports_dir":        "data/research/walkforward_reports",
    "train_size":         100,
    "test_size":          30,
    "step":               20,
    "min_passable_acc":   0.50,
    "purge_td_seconds":   900.0,
    "embargo_td_seconds": 900.0,
}


# ---------------------------------------------------------------------------
# Windowing schemes
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardWindow:
    """One train/test split produced by the walk-forward optimizer."""
    index: int
    scheme: str
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_size: int
    test_size: int


@dataclass
class WindowResult:
    """Performance of one window's test set."""
    window: WalkForwardWindow
    metrics: dict[str, float]
    n_test: int
    train_y_mean: float
    test_y_mean: float


@dataclass
class WalkForwardReport:
    """Aggregate walk-forward report."""
    scheme: str
    run_at: str
    n_samples: int
    n_windows: int
    windows: list[dict[str, Any]] = field(default_factory=list)
    aggregate_metrics: dict[str, float] = field(default_factory=dict)
    stability: dict[str, float] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def persist(self, reports_dir: Optional[str] = None) -> Optional[Path]:
        out_dir = Path(reports_dir or DEFAULTS["reports_dir"])
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = out_dir / f"walkforward_{self.scheme}_{day}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self.to_dict(), default=str) + "\n")
            return path
        except Exception as e:
            log.warning("failed to persist walkforward report: %s", e)
            return None


# ---------------------------------------------------------------------------
# Window generators
# ---------------------------------------------------------------------------


def _rolling_windows(
    n: int, train_size: int, test_size: int, step: int,
) -> list[WalkForwardWindow]:
    out: list[WalkForwardWindow] = []
    idx = 0
    t0 = 0
    while t0 + train_size + test_size <= n:
        t1 = t0 + train_size
        te0 = t1
        te1 = t1 + test_size
        out.append(WalkForwardWindow(
            index=idx, scheme="rolling",
            train_start=t0, train_end=t1,
            test_start=te0, test_end=te1,
            train_size=t1 - t0, test_size=te1 - te0,
        ))
        idx += 1
        t0 += step
    return out


def _anchored_windows(
    n: int, train_size: int, test_size: int, step: int,
) -> list[WalkForwardWindow]:
    out: list[WalkForwardWindow] = []
    idx = 0
    anchor_end = train_size
    while anchor_end + test_size <= n:
        te0 = anchor_end
        te1 = anchor_end + test_size
        out.append(WalkForwardWindow(
            index=idx, scheme="anchored",
            train_start=0, train_end=anchor_end,
            test_start=te0, test_end=te1,
            train_size=anchor_end, test_size=te1 - te0,
        ))
        idx += 1
        anchor_end += step
    return out


def _expanding_windows(
    n: int, train_size: int, test_size: int, step: int,
) -> list[WalkForwardWindow]:
    out: list[WalkForwardWindow] = []
    idx = 0
    t0 = 0
    t_size = train_size
    te_size = test_size
    while t0 + t_size + te_size <= n:
        t1 = t0 + t_size
        te0 = t1
        te1 = t1 + te_size
        out.append(WalkForwardWindow(
            index=idx, scheme="expanding",
            train_start=t0, train_end=t1,
            test_start=te0, test_end=te1,
            train_size=t1 - t0, test_size=te1 - te0,
        ))
        idx += 1
        t0 += step
        t_size += step
        te_size += step
    return out


_SCHEMES = {
    "rolling":   _rolling_windows,
    "anchored":  _anchored_windows,
    "expanding": _expanding_windows,
}


def generate_windows(
    n: int, scheme: str = "rolling",
    train_size: int = 100, test_size: int = 30, step: int = 20,
) -> list[WalkForwardWindow]:
    """Generate the list of walk-forward windows for a given scheme."""
    if n <= 0:
        return []
    scheme = (scheme or "rolling").lower()
    if scheme not in _SCHEMES:
        raise ValueError(f"unknown walk-forward scheme: {scheme!r}")
    if train_size < 1 or test_size < 1 or step < 1:
        raise ValueError(
            f"train_size/test_size/step must be >= 1, got "
            f"{train_size}/{test_size}/{step}"
        )
    return _SCHEMES[scheme](n, train_size, test_size, step)


# ---------------------------------------------------------------------------
# Default score function
# ---------------------------------------------------------------------------


ScoreFn = Callable[
    [np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    dict[str, float],
]


def default_score_fn(
    y_true_train: np.ndarray, y_pred_train: np.ndarray,
    y_true_test: np.ndarray,  y_pred_test: np.ndarray,
) -> dict[str, float]:
    """Compute classification metrics + Brier + rank IC for a test window.

    Treats y as binary 0/1. y_pred is interpreted as a probability in [0,1].
    """
    yt = np.asarray(y_true_test, dtype=float)
    yp = np.asarray(y_pred_test, dtype=float)
    if yt.size == 0:
        return {"n": 0.0}
    n = float(yt.size)
    yp_bin = (yp >= 0.5).astype(float)
    tp = float(np.sum((yp_bin == 1) & (yt == 1)))
    fp = float(np.sum((yp_bin == 1) & (yt == 0)))
    fn = float(np.sum((yp_bin == 0) & (yt == 1)))
    tn = float(np.sum((yp_bin == 0) & (yt == 0)))
    acc = (tp + tn) / n if n > 0 else 0.0
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    brier = float(np.mean((yp - yt) ** 2))
    # Rank IC (Spearman)
    try:
        from scipy.stats import spearmanr
        if yt.size >= 2 and np.unique(yt).size > 1:
            ic, _ = spearmanr(yp, yt)
            ic = float(ic) if math.isfinite(ic) else 0.0
        else:
            ic = 0.0
    except Exception:
        ic = 0.0
    return {
        "n":          n,
        "accuracy":   float(acc),
        "precision":  float(prec),
        "recall":     float(rec),
        "f1":         float(f1),
        "brier":      float(brier),
        "ic":         float(ic),
        "win_rate":   float(acc),  # alias for compatibility with gating
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_walk_forward(
    y: np.ndarray,
    y_pred: np.ndarray,
    scheme: str = "rolling",
    train_size: int = 100,
    test_size: int = 30,
    step: int = 20,
    score_fn: Optional[ScoreFn] = None,
    min_passable_acc: float = 0.50,
    extra: Optional[dict[str, Any]] = None,
    persist: bool = True,
    reports_dir: Optional[str] = None,
) -> WalkForwardReport:
    """Run a walk-forward optimization across the given series.

    Args:
        y: binary 0/1 ground-truth labels (one per sample).
        y_pred: predicted probabilities in [0,1] (one per sample).
        scheme: "rolling" | "anchored" | "expanding".
        train_size: minimum training window length.
        test_size: test window length.
        step: window slide step (in samples).
        score_fn: callable returning a metrics dict. Defaults to
            `default_score_fn`.
        min_passable_acc: threshold below which a window is considered
            "failing" for the stability pass-rate calculation.
        extra: optional dict merged into the report.
        persist: if True, write the report as JSONL.

    Returns:
        WalkForwardReport with per-window + aggregate stats + stability.
    """
    y_arr = np.asarray(y, dtype=float).ravel()
    yp_arr = np.asarray(y_pred, dtype=float).ravel()
    if y_arr.shape != yp_arr.shape:
        raise ValueError(
            f"y and y_pred must have same shape; got {y_arr.shape} vs {yp_arr.shape}"
        )
    n = int(y_arr.shape[0])
    score_fn = score_fn or default_score_fn
    cfg = {
        "scheme": scheme, "train_size": train_size,
        "test_size": test_size, "step": step,
        "min_passable_acc": min_passable_acc,
    }
    report = WalkForwardReport(
        scheme=scheme, run_at=datetime.now(timezone.utc).isoformat(),
        n_samples=n, n_windows=0, config=cfg,
        extra=dict(extra or {}),
    )
    if n == 0:
        report.errors.append("empty input")
        return report

    try:
        windows = generate_windows(
            n=n, scheme=scheme,
            train_size=train_size, test_size=test_size, step=step,
        )
    except Exception as e:
        report.errors.append(f"window generation failed: {e}")
        return report

    if not windows:
        report.errors.append(
            f"no windows produced for n={n} train={train_size} "
            f"test={test_size} step={step}"
        )
        return report

    per_metric: dict[str, list[float]] = {}
    for w in windows:
        try:
            ytr = y_arr[w.train_start:w.train_end]
            yptr = yp_arr[w.train_start:w.train_end]
            yte = y_arr[w.test_start:w.test_end]
            ypte = yp_arr[w.test_start:w.test_end]
            metrics = score_fn(ytr, yptr, yte, ypte) or {}
            metrics = {k: float(v) if isinstance(v, (int, float)) else float(v)
                       for k, v in metrics.items() if isinstance(v, (int, float, np.floating))}
        except Exception as e:
            log.warning("score_fn failed for window %d: %s", w.index, e)
            metrics = {"error": 1.0}
        wr = WindowResult(
            window=w, metrics=metrics,
            n_test=int(w.test_size),
            train_y_mean=float(np.mean(ytr)) if ytr.size else 0.0,
            test_y_mean=float(np.mean(yte)) if yte.size else 0.0,
        )
        report.windows.append({
            "window": asdict(w),
            "metrics": metrics,
            "n_test": wr.n_test,
            "train_y_mean": wr.train_y_mean,
            "test_y_mean": wr.test_y_mean,
        })
        for k, v in metrics.items():
            per_metric.setdefault(k, []).append(float(v))

    report.n_windows = len(windows)

    # Aggregate
    agg: dict[str, float] = {}
    for k, vs in per_metric.items():
        arr = np.asarray([v for v in vs if math.isfinite(v)], dtype=float)
        if arr.size == 0:
            continue
        agg[f"{k}_mean"]  = float(arr.mean())
        agg[f"{k}_std"]   = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
        agg[f"{k}_min"]   = float(arr.min())
        agg[f"{k}_max"]   = float(arr.max())
        agg[f"{k}_last"]  = float(arr[-1])
        agg[f"{k}_first"] = float(arr[0])
    report.aggregate_metrics = agg

    # Stability
    stab: dict[str, float] = {}
    accs = per_metric.get("accuracy") or per_metric.get("win_rate") or []
    accs = [a for a in accs if math.isfinite(a)]
    if accs:
        arr = np.asarray(accs, dtype=float)
        mean = float(arr.mean())
        std = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
        stab["accuracy_mean"] = mean
        stab["accuracy_std"] = std
        stab["accuracy_cv"] = float(std / abs(mean)) if abs(mean) > 1e-12 else float("inf")
        stab["degradation_ratio"] = (
            float(arr[-1] / arr[0]) if abs(arr[0]) > 1e-12 else float("inf")
        )
        stab["pass_rate"] = float(np.mean(arr >= min_passable_acc))
        # Sharpe of the per-window accuracy stream (treats each window as one observation)
        if std > 1e-12:
            stab["accuracy_window_sharpe"] = float(mean / std)
        else:
            stab["accuracy_window_sharpe"] = 0.0
    # Brier stability (lower is better; we report CV of brier)
    briers = per_metric.get("brier", [])
    briers = [b for b in briers if math.isfinite(b)]
    if briers:
        arr = np.asarray(briers, dtype=float)
        m = float(arr.mean())
        s = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
        stab["brier_mean"] = m
        stab["brier_std"] = s
        stab["brier_cv"] = float(s / abs(m)) if abs(m) > 1e-12 else float("inf")
    # IC stability
    ics = per_metric.get("ic", [])
    ics = [v for v in ics if math.isfinite(v)]
    if ics:
        arr = np.asarray(ics, dtype=float)
        m = float(arr.mean())
        s = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
        stab["ic_mean"] = m
        stab["ic_std"] = s
        stab["ic_cv"] = float(s / abs(m)) if abs(m) > 1e-12 else float("inf")
        # Fraction of windows with positive IC
        stab["ic_positive_rate"] = float(np.mean(arr > 0.0))
    # Overall composite robustness score (0..1, higher is better)
    composite = 0.5  # baseline
    if "pass_rate" in stab:
        composite = 0.5 * stab["pass_rate"]
    if "ic_positive_rate" in stab:
        composite += 0.3 * stab["ic_positive_rate"]
    if "degradation_ratio" in stab and stab["degradation_ratio"] != float("inf"):
        # ratio 1.0 means no degradation; we cap [0,2] and map to [0,1]
        r = max(0.0, min(2.0, stab["degradation_ratio"]))
        composite += 0.2 * (1.0 - abs(r - 1.0))
    stab["composite_robustness"] = float(max(0.0, min(1.0, composite)))
    report.stability = stab

    if persist:
        report.persist(reports_dir=reports_dir)
    return report


# ---------------------------------------------------------------------------
# Parameter stability analysis
# ---------------------------------------------------------------------------


@dataclass
class ParameterStabilityReport:
    """Summary of how a metric varies as a single parameter is swept."""
    parameter_name: str
    parameter_values: list[float]
    metric_values: list[float]
    metric_name: str
    mean: float
    std: float
    cv: float
    min: float
    max: float
    range: float
    optimal_value: Optional[float]
    optimal_metric: Optional[float]
    sensitivity: float  # (max - min) / |mean| — higher = more sensitive

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parameter_sweep(
    y: np.ndarray,
    y_pred_factory: Callable[[float], np.ndarray],
    parameter_name: str,
    parameter_values: Sequence[float],
    metric_name: str = "accuracy",
    scheme: str = "rolling",
    train_size: int = 100,
    test_size: int = 30,
    step: int = 20,
    score_fn: Optional[ScoreFn] = None,
) -> ParameterStabilityReport:
    """Sweep one parameter and report the stability of a target metric.

    For each value in `parameter_values`, calls
    `y_pred_factory(value)` to produce y_pred, then runs a walk-forward
    pass. Collects the *aggregate mean* of `metric_name` across windows
    and reports min/max/std/cv/optimal/sensitivity.

    The "optimal" parameter is the one whose metric mean is closest to
    the best value seen (max if metric_name in {accuracy, f1, ic,
    precision, recall, win_rate}, min if metric_name == 'brier').
    """
    score_fn = score_fn or default_score_fn
    means: list[float] = []
    valid_params: list[float] = []
    for v in parameter_values:
        try:
            yp = np.asarray(y_pred_factory(float(v)), dtype=float).ravel()
            rep = run_walk_forward(
                y=y, y_pred=yp, scheme=scheme,
                train_size=train_size, test_size=test_size, step=step,
                score_fn=score_fn, persist=False,
            )
            key = f"{metric_name}_mean"
            if key in rep.aggregate_metrics:
                means.append(float(rep.aggregate_metrics[key]))
                valid_params.append(float(v))
            else:
                log.warning("metric %s missing for param=%s", metric_name, v)
        except Exception as e:
            log.warning("parameter_sweep failed for %s=%s: %s", parameter_name, v, e)

    if not means:
        return ParameterStabilityReport(
            parameter_name=parameter_name,
            parameter_values=list(parameter_values),
            metric_values=[],
            metric_name=metric_name,
            mean=0.0, std=0.0, cv=float("inf"),
            min=0.0, max=0.0, range=0.0,
            optimal_value=None, optimal_metric=None,
            sensitivity=float("inf"),
        )

    arr = np.asarray(means, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    cv = float(std / abs(mean)) if abs(mean) > 1e-12 else float("inf")
    mn = float(arr.min())
    mx = float(arr.max())
    rng = mx - mn
    sens = float(rng / abs(mean)) if abs(mean) > 1e-12 else float("inf")
    if metric_name.lower() in {"brier"}:
        opt_idx = int(np.argmin(arr))
    else:
        opt_idx = int(np.argmax(arr))
    return ParameterStabilityReport(
        parameter_name=parameter_name,
        parameter_values=valid_params,
        metric_values=means,
        metric_name=metric_name,
        mean=mean, std=std, cv=cv, min=mn, max=mx, range=rng,
        optimal_value=valid_params[opt_idx],
        optimal_metric=means[opt_idx],
        sensitivity=sens,
    )


__all__ = [
    "WalkForwardWindow",
    "WindowResult",
    "WalkForwardReport",
    "ParameterStabilityReport",
    "DEFAULTS",
    "generate_windows",
    "default_score_fn",
    "run_walk_forward",
    "parameter_sweep",
    "ScoreFn",
]
