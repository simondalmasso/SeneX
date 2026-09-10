"""
SENECIO ORACLE — ACT XXVII Priority 4: Research Metrics
========================================================

Institutional research-grade metrics that quantify prediction quality and
stability over time. These metrics COMPLEMENT (do not replace) the existing
PortfolioAnalytics module — they are computed from the prediction record
stream (not the trade ledger).

Metrics implemented:
  - Information Coefficient (IC)       : Spearman rank correlation between
                                         predicted confidence and realized return
  - Feature Stability                  : how stable each feature's importance is
                                         across rolling windows
  - Prediction Stability               : how stable predictions are when the
                                         input is perturbed by 1 tick
  - Rolling Sharpe                     : per-step Sharpe over a rolling window
  - Rolling Profit Factor              : gross wins / gross losses (rolling)
  - Rolling Max Drawdown               : peak-to-trough over rolling equity curve

All rolling metrics support configurable window size and step.

This module is STRICT_ADDITIVE — consumes only existing prediction records
and never touches the prediction model / feature engineering / signal
generation / verifier.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
from scipy import stats as sp_stats

log = logging.getLogger("senecio.research.metrics")


DEFAULTS: dict[str, Any] = {
    "rolling_window":          50,        # trades per window
    "rolling_step":            1,         # step between windows
    "trades_per_year":         35_040,
    "risk_free_rate":          0.0,
    "reports_dir":             "data/research/research_metrics",
}


# ---------------------------------------------------------------------------
# Information Coefficient (IC)
# ---------------------------------------------------------------------------


def information_coefficient(
    predictions: np.ndarray,
    realized_returns: np.ndarray,
    method: str = "spearman",
) -> float:
    """Information Coefficient = rank correlation between prediction and return.

    Args:
        predictions        : array of predicted scores (e.g. confidence × sign)
        realized_returns   : array of realized returns (signed, % or abs)
        method             : "spearman" (default) or "pearson"

    Returns:
        correlation in [-1, 1]. NaN if undefined.
    """
    p = np.asarray(predictions, dtype=float).reshape(-1)
    r = np.asarray(realized_returns, dtype=float).reshape(-1)
    n = min(p.shape[0], r.shape[0])
    if n < 3:
        return float("nan")
    p = p[:n]
    r = r[:n]
    # Drop NaN pairs
    mask = np.isfinite(p) & np.isfinite(r)
    if mask.sum() < 3:
        return float("nan")
    p = p[mask]
    r = r[mask]
    if method.lower() == "pearson":
        if p.std() == 0 or r.std() == 0:
            return float("nan")
        return float(sp_stats.pearsonr(p, r)[0])
    # Default: Spearman (rank correlation)
    return float(sp_stats.spearmanr(p, r).correlation)


def rolling_information_coefficient(
    predictions: np.ndarray,
    realized_returns: np.ndarray,
    window: int = DEFAULTS["rolling_window"],
    step: int = DEFAULTS["rolling_step"],
    method: str = "spearman",
) -> list[dict[str, Any]]:
    """Compute IC over a rolling window. Returns list of {start, end, ic, n}."""
    p = np.asarray(predictions, dtype=float).reshape(-1)
    r = np.asarray(realized_returns, dtype=float).reshape(-1)
    n = min(p.shape[0], r.shape[0])
    if n < window:
        return []
    out: list[dict[str, Any]] = []
    for start in range(0, n - window + 1, step):
        end = start + window
        ic = information_coefficient(p[start:end], r[start:end], method=method)
        out.append({
            "start": int(start),
            "end": int(end),
            "ic": float(ic) if math.isfinite(ic) else None,
            "n": int(window),
        })
    return out


# ---------------------------------------------------------------------------
# Feature Stability
# ---------------------------------------------------------------------------


def feature_stability(
    feature_importance_history: np.ndarray,
) -> dict[str, Any]:
    """Compute stability of each feature's importance across time windows.

    Args:
        feature_importance_history : array of shape (n_windows, n_features).
                                     Each row is the feature importance vector
                                     computed on one window.

    Returns:
        {
            "per_feature": [
                {"feature_idx": i, "mean": ..., "std": ..., "cv": ...}, ...
            ],
            "stability_score": float,    # 1 - mean(CV) — higher = more stable
            "n_windows": int,
            "n_features": int,
        }
    """
    arr = np.asarray(feature_importance_history, dtype=float)
    if arr.ndim != 2:
        raise ValueError(
            "feature_importance_history must be 2D (n_windows, n_features)"
        )
    n_windows, n_features = arr.shape
    per_feature: list[dict[str, Any]] = []
    cvs: list[float] = []
    for i in range(n_features):
        col = arr[:, i]
        mean = float(col.mean())
        std = float(col.std(ddof=1)) if n_windows > 1 else 0.0
        cv = float(std / abs(mean)) if mean != 0 else float("nan")
        per_feature.append({
            "feature_idx": int(i),
            "mean": mean,
            "std": std,
            "cv": cv,
        })
        if math.isfinite(cv):
            cvs.append(cv)
    stability_score = float(1.0 - np.mean(cvs)) if cvs else float("nan")
    return {
        "per_feature": per_feature,
        "stability_score": stability_score,
        "n_windows": int(n_windows),
        "n_features": int(n_features),
    }


# ---------------------------------------------------------------------------
# Prediction Stability
# ---------------------------------------------------------------------------


def prediction_stability(
    original_predictions: np.ndarray,
    perturbed_predictions: np.ndarray,
) -> dict[str, Any]:
    """Measure how stable predictions are under small input perturbations.

    Args:
        original_predictions  : predictions on original inputs
        perturbed_predictions : predictions on perturbed inputs (e.g. +1 tick)

    Returns:
        {
            "mean_abs_change": float,
            "mean_pct_change": float,
            "sign_flip_rate": float,      # fraction where sign(pred) flipped
            "correlation": float,         # Pearson correlation
            "stable_rate": float,         # frac where |change| < 0.05
        }
    """
    a = np.asarray(original_predictions, dtype=float).reshape(-1)
    b = np.asarray(perturbed_predictions, dtype=float).reshape(-1)
    n = min(a.shape[0], b.shape[0])
    if n == 0:
        return {
            "mean_abs_change": 0.0, "mean_pct_change": 0.0,
            "sign_flip_rate": 0.0, "correlation": 0.0, "stable_rate": 0.0,
            "n": 0,
        }
    a = a[:n]
    b = b[:n]
    diff = b - a
    abs_change = float(np.mean(np.abs(diff)))
    pct_change = float(np.mean(np.abs(diff) / np.maximum(np.abs(a), 1e-9)))
    sign_a = np.sign(a)
    sign_b = np.sign(b)
    sign_flips = int(np.sum((sign_a != sign_b) & (sign_a != 0) & (sign_b != 0)))
    sign_flip_rate = float(sign_flips / n)
    if a.std() > 0 and b.std() > 0:
        corr = float(sp_stats.pearsonr(a, b)[0])
    else:
        corr = float("nan")
    stable_rate = float(np.mean(np.abs(diff) < 0.05))
    return {
        "mean_abs_change": abs_change,
        "mean_pct_change": pct_change,
        "sign_flip_rate": sign_flip_rate,
        "correlation": corr,
        "stable_rate": stable_rate,
        "n": int(n),
    }


# ---------------------------------------------------------------------------
# Rolling Sharpe / Profit Factor / Max Drawdown
# ---------------------------------------------------------------------------


def rolling_sharpe(
    returns: np.ndarray,
    window: int = DEFAULTS["rolling_window"],
    step: int = DEFAULTS["rolling_step"],
    trades_per_year: int = DEFAULTS["trades_per_year"],
    risk_free_rate: float = DEFAULTS["risk_free_rate"],
) -> list[dict[str, Any]]:
    """Rolling annualized Sharpe ratio over a window of per-trade returns."""
    r = np.asarray(returns, dtype=float).reshape(-1)
    n = r.shape[0]
    if n < window:
        return []
    out: list[dict[str, Any]] = []
    for start in range(0, n - window + 1, step):
        end = start + window
        w = r[start:end]
        mean_r = float(w.mean()) - risk_free_rate
        std = float(w.std(ddof=1)) if window > 1 else 0.0
        if std == 0:
            sharpe = 0.0
        else:
            sharpe = (mean_r / std) * math.sqrt(trades_per_year)
        out.append({
            "start": int(start), "end": int(end),
            "sharpe": float(sharpe),
            "mean_return": float(mean_r),
            "std_return": float(std),
            "n": int(window),
        })
    return out


def rolling_profit_factor(
    pnls: np.ndarray,
    window: int = DEFAULTS["rolling_window"],
    step: int = DEFAULTS["rolling_step"],
) -> list[dict[str, Any]]:
    """Rolling Profit Factor = Σ(wins) / |Σ(losses)| over a window of $-PnLs."""
    p = np.asarray(pnls, dtype=float).reshape(-1)
    n = p.shape[0]
    if n < window:
        return []
    out: list[dict[str, Any]] = []
    for start in range(0, n - window + 1, step):
        end = start + window
        w = p[start:end]
        wins = float(w[w > 0].sum())
        losses = float(abs(w[w < 0].sum()))
        pf = float(wins / losses) if losses > 0 else float("inf")
        out.append({
            "start": int(start), "end": int(end),
            "profit_factor": pf,
            "wins": wins,
            "losses": losses,
            "n": int(window),
        })
    return out


def rolling_max_drawdown(
    pnls: np.ndarray,
    window: int = DEFAULTS["rolling_window"],
    step: int = DEFAULTS["rolling_step"],
    starting_equity: float = 10_000.0,
) -> list[dict[str, Any]]:
    """Rolling max drawdown over a window of $-PnLs (equity curve)."""
    p = np.asarray(pnls, dtype=float).reshape(-1)
    n = p.shape[0]
    if n < window:
        return []
    out: list[dict[str, Any]] = []
    for start in range(0, n - window + 1, step):
        end = start + window
        w = p[start:end]
        equity = starting_equity
        peak = equity
        max_dd_usd = 0.0
        max_dd_pct = 0.0
        for v in w:
            equity += v
            if equity > peak:
                peak = equity
            dd_usd = peak - equity
            if dd_usd > max_dd_usd:
                max_dd_usd = dd_usd
                max_dd_pct = (dd_usd / peak * 100.0) if peak > 0 else 0.0
        out.append({
            "start": int(start), "end": int(end),
            "max_drawdown_usd": float(max_dd_usd),
            "max_drawdown_pct": float(max_dd_pct),
            "n": int(window),
        })
    return out


# ---------------------------------------------------------------------------
# Composite report
# ---------------------------------------------------------------------------


@dataclass
class ResearchMetricsReport:
    """Aggregate research metrics report."""
    computed_at: str
    n_samples: int
    ic: float
    ic_method: str
    rolling_ic: list[dict[str, Any]] = field(default_factory=list)
    rolling_sharpe: list[dict[str, Any]] = field(default_factory=list)
    rolling_profit_factor: list[dict[str, Any]] = field(default_factory=list)
    rolling_max_drawdown: list[dict[str, Any]] = field(default_factory=list)
    feature_stability: Optional[dict[str, Any]] = None
    prediction_stability: Optional[dict[str, Any]] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_research_metrics(
    predictions: np.ndarray,
    realized_returns: np.ndarray,
    pnls: Optional[np.ndarray] = None,
    feature_importance_history: Optional[np.ndarray] = None,
    perturbed_predictions: Optional[np.ndarray] = None,
    window: int = DEFAULTS["rolling_window"],
    step: int = DEFAULTS["rolling_step"],
    trades_per_year: int = DEFAULTS["trades_per_year"],
    ic_method: str = "spearman",
    reports_dir: str = DEFAULTS["reports_dir"],
    extra: Optional[dict] = None,
) -> ResearchMetricsReport:
    """Compute the full research-metrics report and persist as JSONL.

    Args:
        predictions                : 1D array of prediction scores
        realized_returns           : 1D array of realized returns (same length)
        pnls                       : optional 1D array of $-PnLs per trade
        feature_importance_history : optional (n_windows, n_features) array
        perturbed_predictions      : optional 1D array of predictions on
                                     perturbed inputs (for prediction stability)
        window                     : rolling window size
        step                       : rolling step
        trades_per_year            : annualization factor for Sharpe
        ic_method                  : "spearman" or "pearson"
        reports_dir                : where to persist the JSONL report
        extra                      : extra metadata
    """
    preds = np.asarray(predictions, dtype=float).reshape(-1)
    rets  = np.asarray(realized_returns, dtype=float).reshape(-1)
    n = min(preds.shape[0], rets.shape[0])
    ic_val = information_coefficient(preds, rets, method=ic_method)
    ric = rolling_information_coefficient(preds, rets, window=window, step=step, method=ic_method)

    rsharpe: list[dict[str, Any]] = []
    rpf: list[dict[str, Any]] = []
    rmdd: list[dict[str, Any]] = []
    if pnls is not None:
        p_arr = np.asarray(pnls, dtype=float).reshape(-1)
        rsharpe = rolling_sharpe(
            (p_arr / 10_000.0),  # convert $-PnL to per-trade return
            window=window, step=step, trades_per_year=trades_per_year,
        )
        rpf = rolling_profit_factor(p_arr, window=window, step=step)
        rmdd = rolling_max_drawdown(p_arr, window=window, step=step)

    fs = None
    if feature_importance_history is not None:
        fs = feature_stability(feature_importance_history)

    ps = None
    if perturbed_predictions is not None:
        ps = prediction_stability(preds, perturbed_predictions)

    report = ResearchMetricsReport(
        computed_at=datetime.now(timezone.utc).isoformat(),
        n_samples=int(n),
        ic=float(ic_val) if math.isfinite(ic_val) else float("nan"),
        ic_method=ic_method,
        rolling_ic=ric,
        rolling_sharpe=rsharpe,
        rolling_profit_factor=rpf,
        rolling_max_drawdown=rmdd,
        feature_stability=fs,
        prediction_stability=ps,
        extra=extra or {},
    )
    _persist_metrics_report(report, reports_dir)
    return report


def _persist_metrics_report(
    report: ResearchMetricsReport, reports_dir: str,
) -> None:
    try:
        out_dir = Path(reports_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = out_dir / f"research_metrics_{day}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(report.to_dict(), default=str) + "\n")
        log.info("research metrics report persisted to %s", path)
    except Exception as e:
        log.warning("failed to persist research metrics: %s", e)


__all__ = [
    "information_coefficient",
    "rolling_information_coefficient",
    "feature_stability",
    "prediction_stability",
    "rolling_sharpe",
    "rolling_profit_factor",
    "rolling_max_drawdown",
    "ResearchMetricsReport",
    "compute_research_metrics",
    "DEFAULTS",
]
