"""
SENECIO ORACLE — ACT XXVIII Module 2: Monte Carlo Validation
==============================================================

Bootstrap + permutation-based robustness checks for a backtested trade
series.  All methods operate on a 1-D array of per-trade returns (in
decimal — e.g. 0.012 means +1.2 %) and produce distributional evidence
about the strategy's true risk profile.

Methods:
  1. trade_reshuffle     — draw without replacement (destroys serial
                           dependence; estimates the distribution of
                           aggregate stats under random trade ordering)
  2. bootstrap_equity    — draw WITH replacement (preserves marginal
                           distribution; estimates the distribution of
                           Sharpe / max-DD / profit-factor / CAGR)
  3. random_execution_order — same as trade_reshuffle but additionally
                           inserts a gap penalty between trades to
                           simulate latency jitter
  4. slippage_perturbation — multiply every trade by (1 + N(0, σ_slip))
                           where σ_slip is in basis points
  5. fee_perturbation    — subtract N(0, σ_fee_bps) bps from every trade
  6. drawdown_distribution — compute max-DD on every bootstrap sample
  7. ruin_probability    — fraction of bootstrap samples whose equity
                           falls below the ruin threshold
  8. confidence_intervals — bootstrap CI for any statistic

All reports are persisted as JSONL under
`data/research/montecarlo_reports/` for audit.

References:
  - Efron & Tibshirani (1993) — An Introduction to the Bootstrap
  - López de Prado (2018)    — Advances in Financial Machine Learning,
                                ch.13 (Backtesting Risk & RFM)
  - Pardo (2008)             — The Evaluation and Optimization of
                                Trading Strategies

STRICT_ADDITIVE — does NOT touch:
  - prediction_model / feature_engineering / signal_generation / verifier
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

log = logging.getLogger("senecio.research.montecarlo")


DEFAULTS: dict[str, Any] = {
    "reports_dir":         "data/research/montecarlo_reports",
    "n_bootstrap":         2000,
    "n_reshuffle":         1000,
    "ruin_threshold_pct":  -0.20,   # -20 % drawdown = ruin
    "slippage_bps_std":    2.0,     # 2 bps std slippage perturbation
    "fee_bps_std":         0.5,     # 0.5 bps std fee perturbation
    "gap_penalty_bps":     0.5,     # per-trade gap penalty (latency)
    "ci_levels":           [0.90, 0.95, 0.99],
    "random_seed":         1337,
}


# ---------------------------------------------------------------------------
# Core statistics helpers
# ---------------------------------------------------------------------------


def _equity_curve(returns: np.ndarray, start: float = 1.0) -> np.ndarray:
    """Cumulative equity from per-trade decimal returns."""
    r = np.asarray(returns, dtype=float).ravel()
    if r.size == 0:
        return np.asarray([start], dtype=float)
    return start * np.cumprod(1.0 + r)


def _max_drawdown(returns: np.ndarray) -> float:
    """Maximum drawdown (as a negative decimal, e.g. -0.15 = -15 %)."""
    eq = _equity_curve(returns)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    return float(dd.min()) if dd.size else 0.0


def _sharpe(returns: np.ndarray, periods_per_year: int = 252) -> float:
    """Annualised Sharpe of a per-trade return series."""
    r = np.asarray(returns, dtype=float).ravel()
    if r.size < 2:
        return 0.0
    mu = float(r.mean())
    sd = float(r.std(ddof=1))
    if sd < 1e-12:
        return 0.0
    return mu / sd * math.sqrt(periods_per_year)


def _profit_factor(returns: np.ndarray) -> float:
    """Gross profit / gross loss (no wins => 0; no losses => inf)."""
    r = np.asarray(returns, dtype=float).ravel()
    gains = r[r > 0]
    losses = r[r < 0]
    gp = float(gains.sum()) if gains.size else 0.0
    gl = float(abs(losses.sum())) if losses.size else 0.0
    if gl < 1e-12:
        return float("inf") if gp > 1e-12 else 0.0
    return gp / gl


def _expectancy(returns: np.ndarray) -> float:
    """Mean per-trade return."""
    r = np.asarray(returns, dtype=float).ravel()
    return float(r.mean()) if r.size else 0.0


def _win_rate(returns: np.ndarray) -> float:
    r = np.asarray(returns, dtype=float).ravel()
    if r.size == 0:
        return 0.0
    return float(np.mean(r > 0))


def _cagr(returns: np.ndarray, periods_per_year: int = 252) -> float:
    """Compound annual growth rate from per-trade returns."""
    r = np.asarray(returns, dtype=float).ravel()
    if r.size == 0:
        return 0.0
    eq = _equity_curve(r)
    final = float(eq[-1])
    n_years = max(r.size / periods_per_year, 1e-9)
    if final <= 0:
        return -1.0
    return float(final ** (1.0 / n_years) - 1.0)


_STAT_FNS: dict[str, Callable[[np.ndarray], float]] = {
    "sharpe":         _sharpe,
    "max_drawdown":   _max_drawdown,
    "profit_factor":  _profit_factor,
    "expectancy":     _expectancy,
    "win_rate":       _win_rate,
    "cagr":           _cagr,
}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class MonteCarloReport:
    """Aggregate Monte-Carlo robustness report."""
    run_at: str
    n_trades: int
    n_bootstrap: int
    n_reshuffle: int
    original_stats: dict[str, float] = field(default_factory=dict)
    bootstrap_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    reshuffle_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    slippage_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    fee_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    gap_penalty_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    drawdown_distribution: dict[str, Any] = field(default_factory=dict)
    ruin_probability: float = 0.0
    confidence_intervals: dict[str, dict[str, float]] = field(default_factory=dict)
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
            path = out_dir / f"montecarlo_{day}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self.to_dict(), default=str) + "\n")
            return path
        except Exception as e:
            log.warning("failed to persist montecarlo report: %s", e)
            return None


# ---------------------------------------------------------------------------
# Bootstrap / reshuffle primitives
# ---------------------------------------------------------------------------


def _bootstrap_resample(
    rng: np.random.Generator, returns: np.ndarray, n: int,
) -> np.ndarray:
    """Draw `n` samples WITH replacement."""
    idx = rng.integers(0, returns.shape[0], size=n)
    return returns[idx]


def _reshuffle(
    rng: np.random.Generator, returns: np.ndarray,
) -> np.ndarray:
    """Permute trades WITHOUT replacement (destroys serial dependence)."""
    return rng.permutation(returns)


def _summarise(values: np.ndarray, ci_levels: Sequence[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"n": 0.0}
    out: dict[str, float] = {
        "n":    float(arr.size),
        "mean": float(arr.mean()),
        "std":  float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        "min":  float(arr.min()),
        "p05":  float(np.percentile(arr, 5)),
        "p25":  float(np.percentile(arr, 25)),
        "p50":  float(np.percentile(arr, 50)),
        "p75":  float(np.percentile(arr, 75)),
        "p95":  float(np.percentile(arr, 95)),
        "max":  float(arr.max()),
    }
    for lvl in ci_levels:
        alpha = 1.0 - float(lvl)
        lo = float(np.percentile(arr, 100.0 * alpha / 2.0))
        hi = float(np.percentile(arr, 100.0 * (1.0 - alpha / 2.0)))
        out[f"ci_{int(lvl*100)}_lo"] = lo
        out[f"ci_{int(lvl*100)}_hi"] = hi
    return out


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def run_monte_carlo(
    returns: np.ndarray,
    n_bootstrap: Optional[int] = None,
    n_reshuffle: Optional[int] = None,
    ruin_threshold_pct: Optional[float] = None,
    slippage_bps_std: Optional[float] = None,
    fee_bps_std: Optional[float] = None,
    gap_penalty_bps: Optional[float] = None,
    ci_levels: Optional[Sequence[float]] = None,
    random_seed: Optional[int] = None,
    periods_per_year: int = 252,
    extra: Optional[dict[str, Any]] = None,
    persist: bool = True,
    reports_dir: Optional[str] = None,
) -> MonteCarloReport:
    """Run the full Monte-Carlo robustness battery on a return series.

    Args:
        returns: 1-D array of per-trade decimal returns (e.g. +0.012 = +1.2 %).
        n_bootstrap: number of bootstrap samples (default 2000).
        n_reshuffle: number of reshuffle permutations (default 1000).
        ruin_threshold_pct: equity drawdown that counts as ruin (default -20 %).
        slippage_bps_std: std of slippage perturbation, in bps (default 2.0).
        fee_bps_std: std of fee perturbation, in bps (default 0.5).
        gap_penalty_bps: per-trade latency-gap penalty, in bps (default 0.5).
        ci_levels: confidence levels to report (default [0.90, 0.95, 0.99]).
        random_seed: RNG seed.
        periods_per_year: annualisation factor for Sharpe / CAGR (default 252).
        extra: optional dict merged into the report.
        persist: write JSONL report if True.

    Returns:
        MonteCarloReport with every distribution summarised.
    """
    r = np.asarray(returns, dtype=float).ravel()
    n = int(r.shape[0])
    cfg = {
        "n_bootstrap":        n_bootstrap or DEFAULTS["n_bootstrap"],
        "n_reshuffle":        n_reshuffle or DEFAULTS["n_reshuffle"],
        "ruin_threshold_pct": ruin_threshold_pct if ruin_threshold_pct is not None else DEFAULTS["ruin_threshold_pct"],
        "slippage_bps_std":   slippage_bps_std if slippage_bps_std is not None else DEFAULTS["slippage_bps_std"],
        "fee_bps_std":        fee_bps_std if fee_bps_std is not None else DEFAULTS["fee_bps_std"],
        "gap_penalty_bps":    gap_penalty_bps if gap_penalty_bps is not None else DEFAULTS["gap_penalty_bps"],
        "ci_levels":          list(ci_levels or DEFAULTS["ci_levels"]),
        "random_seed":        random_seed if random_seed is not None else DEFAULTS["random_seed"],
        "periods_per_year":   periods_per_year,
    }
    report = MonteCarloReport(
        run_at=datetime.now(timezone.utc).isoformat(),
        n_trades=n,
        n_bootstrap=cfg["n_bootstrap"],
        n_reshuffle=cfg["n_reshuffle"],
        config=cfg,
        extra=dict(extra or {}),
    )
    if n == 0:
        report.errors.append("empty return series")
        return report

    rng = np.random.default_rng(cfg["random_seed"])

    # Original statistics
    report.original_stats = {
        name: float(fn(r)) for name, fn in _STAT_FNS.items()
    }
    # Wrap sharpe / pf to respect periods_per_year — done in fn closureship
    report.original_stats["sharpe"] = _sharpe(r, periods_per_year)
    report.original_stats["cagr"]   = _cagr(r, periods_per_year)

    # ----- bootstrap (with replacement) -----
    boot_sums: dict[str, list[float]] = {k: [] for k in _STAT_FNS}
    boot_dds: list[float] = []
    ruin_count = 0
    ruin_threshold = float(cfg["ruin_threshold_pct"])
    n_boot = max(1, int(cfg["n_bootstrap"]))
    for _ in range(n_boot):
        s = _bootstrap_resample(rng, r, n)
        for name, fn in _STAT_FNS.items():
            try:
                if name == "sharpe":
                    boot_sums[name].append(_sharpe(s, periods_per_year))
                elif name == "cagr":
                    boot_sums[name].append(_cagr(s, periods_per_year))
                else:
                    boot_sums[name].append(float(fn(s)))
            except Exception:
                boot_sums[name].append(float("nan"))
        dd = _max_drawdown(s)
        boot_dds.append(dd)
        if dd <= ruin_threshold:
            ruin_count += 1
    for name, vs in boot_sums.items():
        report.bootstrap_stats[name] = _summarise(vs, cfg["ci_levels"])
    report.drawdown_distribution = _summarise(boot_dds, cfg["ci_levels"])
    report.ruin_probability = float(ruin_count / n_boot)

    # ----- reshuffle (without replacement) -----
    resh_sums: dict[str, list[float]] = {k: [] for k in _STAT_FNS}
    n_resh = max(1, int(cfg["n_reshuffle"]))
    for _ in range(n_resh):
        s = _reshuffle(rng, r)
        for name, fn in _STAT_FNS.items():
            try:
                if name == "sharpe":
                    resh_sums[name].append(_sharpe(s, periods_per_year))
                elif name == "cagr":
                    resh_sums[name].append(_cagr(s, periods_per_year))
                else:
                    resh_sums[name].append(float(fn(s)))
            except Exception:
                resh_sums[name].append(float("nan"))
    for name, vs in resh_sums.items():
        report.reshuffle_stats[name] = _summarise(vs, cfg["ci_levels"])

    # ----- slippage perturbation -----
    slip_bps = float(cfg["slippage_bps_std"])
    if slip_bps > 0:
        slip_sums: dict[str, list[float]] = {k: [] for k in _STAT_FNS}
        for _ in range(n_boot):
            noise = rng.normal(0.0, slip_bps / 10000.0, size=n)
            s = r + r * noise  # multiplicative slippage on each trade
            for name, fn in _STAT_FNS.items():
                try:
                    if name == "sharpe":
                        slip_sums[name].append(_sharpe(s, periods_per_year))
                    elif name == "cagr":
                        slip_sums[name].append(_cagr(s, periods_per_year))
                    else:
                        slip_sums[name].append(float(fn(s)))
                except Exception:
                    slip_sums[name].append(float("nan"))
        for name, vs in slip_sums.items():
            report.slippage_stats[name] = _summarise(vs, cfg["ci_levels"])
    else:
        report.slippage_stats = {k: {"skipped": 1.0} for k in _STAT_FNS}

    # ----- fee perturbation -----
    fee_bps = float(cfg["fee_bps_std"])
    if fee_bps > 0:
        fee_sums: dict[str, list[float]] = {k: [] for k in _STAT_FNS}
        for _ in range(n_boot):
            noise = rng.normal(0.0, fee_bps / 10000.0, size=n)
            s = r - noise  # additive fee cost on each trade
            for name, fn in _STAT_FNS.items():
                try:
                    if name == "sharpe":
                        fee_sums[name].append(_sharpe(s, periods_per_year))
                    elif name == "cagr":
                        fee_sums[name].append(_cagr(s, periods_per_year))
                    else:
                        fee_sums[name].append(float(fn(s)))
                except Exception:
                    fee_sums[name].append(float("nan"))
        for name, vs in fee_sums.items():
            report.fee_stats[name] = _summarise(vs, cfg["ci_levels"])
    else:
        report.fee_stats = {k: {"skipped": 1.0} for k in _STAT_FNS}

    # ----- gap (latency) penalty -----
    gap_bps = float(cfg["gap_penalty_bps"])
    if gap_bps > 0:
        gap_sums: dict[str, list[float]] = {k: [] for k in _STAT_FNS}
        gap_penalty = gap_bps / 10000.0
        for _ in range(n_resh):
            s = _reshuffle(rng, r) - gap_penalty
            for name, fn in _STAT_FNS.items():
                try:
                    if name == "sharpe":
                        gap_sums[name].append(_sharpe(s, periods_per_year))
                    elif name == "cagr":
                        gap_sums[name].append(_cagr(s, periods_per_year))
                    else:
                        gap_sums[name].append(float(fn(s)))
                except Exception:
                    gap_sums[name].append(float("nan"))
        for name, vs in gap_sums.items():
            report.gap_penalty_stats[name] = _summarise(vs, cfg["ci_levels"])
    else:
        report.gap_penalty_stats = {k: {"skipped": 1.0} for k in _STAT_FNS}

    # ----- confidence intervals (from bootstrap distribution) -----
    for name, summ in report.bootstrap_stats.items():
        ci: dict[str, float] = {}
        for lvl in cfg["ci_levels"]:
            k_lo = f"ci_{int(lvl*100)}_lo"
            k_hi = f"ci_{int(lvl*100)}_hi"
            if k_lo in summ and k_hi in summ:
                ci[f"ci_{int(lvl*100)}"] = [summ[k_lo], summ[k_hi]]
        report.confidence_intervals[name] = ci

    if persist:
        report.persist(reports_dir=reports_dir)
    return report


# ---------------------------------------------------------------------------
# Convenience single-statistic bootstrap CI helper
# ---------------------------------------------------------------------------


def bootstrap_ci(
    returns: np.ndarray, statistic: Callable[[np.ndarray], float],
    n_bootstrap: int = 2000, ci_levels: Sequence[float] = (0.90, 0.95, 0.99),
    random_seed: int = 1337,
) -> dict[str, Any]:
    """Bootstrap a CI for any scalar statistic of a return series."""
    r = np.asarray(returns, dtype=float).ravel()
    if r.size == 0:
        return {"mean": 0.0, "ci": {}}
    rng = np.random.default_rng(random_seed)
    vals = []
    for _ in range(max(1, n_bootstrap)):
        s = _bootstrap_resample(rng, r, r.size)
        try:
            v = float(statistic(s))
            if math.isfinite(v):
                vals.append(v)
        except Exception:
            continue
    out: dict[str, Any] = {
        "n":      len(vals),
        "mean":   float(np.mean(vals)) if vals else 0.0,
        "std":    float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0,
        "ci":     {},
    }
    if vals:
        arr = np.asarray(vals, dtype=float)
        for lvl in ci_levels:
            alpha = 1.0 - float(lvl)
            lo = float(np.percentile(arr, 100.0 * alpha / 2.0))
            hi = float(np.percentile(arr, 100.0 * (1.0 - alpha / 2.0)))
            out["ci"][f"ci_{int(lvl*100)}"] = [lo, hi]
    return out


__all__ = [
    "MonteCarloReport",
    "DEFAULTS",
    "run_monte_carlo",
    "bootstrap_ci",
]
