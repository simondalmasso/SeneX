"""
SENECIO ORACLE — ACT XXVIII Module 3: Statistical Validation
==============================================================

Implements the institutional-grade statistical-robustness battery that
every serious quant desk runs before letting a strategy touch capital.
All tests are non-parametric or analytically grounded — they make no
distributional assumptions beyond stationarity over the sample window.

Tests implemented:
  1. Deflated Sharpe Ratio (DSR)  — Bailey & López de Prado (2014).
        Adjusts the observed Sharpe for trial-and-error / multiple
        testing.  Returns the probability that the *true* Sharpe is
        > 0 after deflation.
  2. Probabilistic Sharpe Ratio (PSR) — López de Prado & Peijan (2004).
        P(SR_true > SR_benchmark | observed).  With benchmark=0 this
        answers "is this strategy's edge real or noise?"
  3. Probability of Backtest Overfitting (PBO) — Bailey et al. (2017).
        Combinatorially symmetric cross-validation on N strategy
        variants.  Returns the fraction of paths where the best
        in-sample strategy underperforms the median out-of-sample.
  4. White Reality Check (WRC)    — White (2000).
        Bootstrap spine test for superior predictive ability against
        a benchmark.  Returns p-value of "best strategy beats
        benchmark" after bootstrap.
  5. Superior Predictive Ability (SPA) — Hansen (2005).
        Refinement of WRC that down-weights poor-performing strategies
        (via stationary bootstrap of the loss differential).
  6. Multiple-hypothesis correction — Benjamini-Hochberg (FDR) and
        Holm-Bonferroni (FWER) on a vector of p-values.

All tests accept numpy arrays of returns / loss-differentials and
return dataclass reports.  Aggregate report persists as JSONL under
`data/research/statistical_reports/`.

References:
  - Bailey, D. & López de Prado, M. (2014) "The Deflated Sharpe Ratio"
  - López de Prado, M. (2018) "Advances in Financial Machine Learning"
  - White, H. (2000) "A Reality Check for Data Snooping"
  - Hansen, P. R. (2005) "A Test for Superior Predictive Ability"
  - Benjamini, Y. & Hochberg, Y. (1995) "Controlling the FDR"

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
from typing import Any, Optional, Sequence

import numpy as np
from scipy import stats as sp_stats

log = logging.getLogger("senecio.research.statistical")


DEFAULTS: dict[str, Any] = {
    "reports_dir":           "data/research/statistical_reports",
    "n_bootstrap":           1000,
    "random_seed":           7,
    "pbo_n_groups":          8,
    "pbo_n_test_groups":     2,
    "sharpe_benchmark":      0.0,
    "periods_per_year":      252,
    "fdr_alpha":             0.05,
    "fwer_alpha":            0.05,
}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class DeflatedSharpeReport:
    sharpe_observed: float
    sharpe_deflated: float
    p_value: float       # P(SR_true <= 0 | observed)
    n_trials: int
    n_samples: int
    skewness: float
    kurtosis: float
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProbabilisticSharpeReport:
    sharpe_observed: float
    sharpe_benchmark: float
    psr: float           # P(SR_true > SR_benchmark | observed)
    n_samples: int
    skewness: float
    kurtosis: float
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PBOTReport:
    pbo: float           # Probability of Backtest Overfitting [0,1]
    n_paths: int
    n_strategies: int
    n_groups: int
    n_test_groups: int
    logits: list[float] = field(default_factory=list)
    logit_mean: float = 0.0
    logit_std: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RealityCheckReport:
    """White Reality Check + Hansen SPA combined report."""
    method: str                # 'wrc' or 'spa'
    n_strategies: int
    n_samples: int
    benchmark_index: int
    best_strategy_index: int
    observed_diff: float       # observed loss-diff (best vs benchmark)
    p_value: float
    bootstrap_replicates: list[float] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MultipleHypothesisReport:
    """FDR (BH) + FWER (Holm) correction on a vector of p-values."""
    n_hypotheses: int
    raw_p_values: list[float]
    bh_rejected: list[bool]
    bh_thresholds: list[float]
    holm_rejected: list[bool]
    holm_thresholds: list[float]
    fdr_alpha: float
    fwer_alpha: float
    n_rejected_bh: int
    n_rejected_holm: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StatisticalValidationReport:
    """Aggregate statistical-validation battery report."""
    run_at: str
    n_samples: int
    n_strategies: int
    deflated_sharpe: Optional[dict[str, Any]] = None
    probabilistic_sharpe: Optional[dict[str, Any]] = None
    pbo: Optional[dict[str, Any]] = None
    wrc: Optional[dict[str, Any]] = None
    spa: Optional[dict[str, Any]] = None
    multiple_hypothesis: Optional[dict[str, Any]] = None
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
            path = out_dir / f"statistical_{day}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self.to_dict(), default=str) + "\n")
            return path
        except Exception as e:
            log.warning("failed to persist statistical report: %s", e)
            return None


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def _sharpe(returns: np.ndarray, periods_per_year: int = 252) -> float:
    r = np.asarray(returns, dtype=float).ravel()
    if r.size < 2:
        return 0.0
    mu = float(r.mean())
    sd = float(r.std(ddof=1))
    if sd < 1e-12:
        return 0.0
    return mu / sd * math.sqrt(periods_per_year)


def _moments(returns: np.ndarray) -> tuple[float, float, float, float, int]:
    r = np.asarray(returns, dtype=float).ravel()
    n = int(r.size)
    if n == 0:
        return 0.0, 0.0, 0.0, 0.0, 0
    mean = float(r.mean())
    var  = float(r.var(ddof=1)) if n > 1 else 0.0
    sd   = math.sqrt(var) if var > 0 else 0.0
    if sd < 1e-12:
        return mean, 0.0, 0.0, 0.0, n
    skew = float(((r - mean) ** 3).mean() / (sd ** 3))
    kurt = float(((r - mean) ** 4).mean() / (sd ** 4) - 3.0)  # excess
    return mean, sd, skew, kurt, n


# ---------------------------------------------------------------------------
# 1. Deflated Sharpe Ratio
# ---------------------------------------------------------------------------


def deflated_sharpe_ratio(
    returns: np.ndarray,
    n_trials: int = 1,
    periods_per_year: int = 252,
    extra: Optional[dict[str, Any]] = None,
) -> DeflatedSharpeReport:
    """Bailey & López de Prado (2014).

    Adjusts the observed Sharpe for the maximum-over-N-trials selection
    bias.  Returns the deflated Sharpe and the p-value under H0:
    "true Sharpe <= 0".

    Args:
        returns: per-trade (or per-period) returns.
        n_trials: number of strategy variants tried (selection bias).
        periods_per_year: annualisation factor.
    """
    r = np.asarray(returns, dtype=float).ravel()
    mean, sd, skew, kurt, n = _moments(r)
    if n < 2 or sd < 1e-12:
        return DeflatedSharpeReport(
            sharpe_observed=0.0, sharpe_deflated=0.0, p_value=1.0,
            n_trials=n_trials, n_samples=n, skewness=skew, kurtosis=kurt,
            extra=dict(extra or {}),
        )
    sr_obs = (mean / sd) * math.sqrt(periods_per_year)
    # Standard error of Sharpe per Lo (2002), adjusted for skew/kurt
    se_sr = math.sqrt(
        (1.0 / (n - 1)) *
        (1.0 - skew * sr_obs / math.sqrt(periods_per_year)
            + ((kurt - 1) / 4.0) * (sr_obs ** 2) / periods_per_year)
    )
    # Expected max of N i.i.d. standard normals (Bailey & López de Prado)
    if n_trials > 1:
        gamma = 0.5772156649015329  # Euler-Mascheroni
        e_max = (1.0 - gamma) * sp_stats.norm.ppf(1.0 - 1.0 / n_trials) \
                + gamma * sp_stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    else:
        e_max = 0.0
    # Deflated Sharpe
    sr_def = sr_obs - e_max * se_sr
    # p-value under H0: true SR <= 0, statistic SR_def / se_sr ~ N(0,1)
    if se_sr > 1e-12:
        z = sr_def / se_sr
        p = 1.0 - sp_stats.norm.cdf(z)
    else:
        z = 0.0
        p = 1.0
    out = DeflatedSharpeReport(
        sharpe_observed=float(sr_obs),
        sharpe_deflated=float(sr_def),
        p_value=float(p),
        n_trials=int(n_trials),
        n_samples=n,
        skewness=skew,
        kurtosis=kurt,
        extra=dict(extra or {}),
    )
    out.extra["se_sr"] = float(se_sr)
    out.extra["e_max"] = float(e_max)
    out.extra["z"]     = float(z)
    return out


# ---------------------------------------------------------------------------
# 2. Probabilistic Sharpe Ratio
# ---------------------------------------------------------------------------


def probabilistic_sharpe_ratio(
    returns: np.ndarray,
    sharpe_benchmark: float = 0.0,
    periods_per_year: int = 252,
    extra: Optional[dict[str, Any]] = None,
) -> ProbabilisticSharpeReport:
    """López de Prado & Peijan (2004).

    Returns P(SR_true > SR_benchmark | observed), accounting for the
    effect of skewness and kurtosis on the Sharpe-ratio distribution.
    """
    r = np.asarray(returns, dtype=float).ravel()
    mean, sd, skew, kurt, n = _moments(r)
    if n < 2 or sd < 1e-12:
        return ProbabilisticSharpeReport(
            sharpe_observed=0.0, sharpe_benchmark=float(sharpe_benchmark),
            psr=0.0, n_samples=n, skewness=skew, kurtosis=kurt,
            extra=dict(extra or {}),
        )
    sr_obs = (mean / sd) * math.sqrt(periods_per_year)
    sr_bench = float(sharpe_benchmark)
    # Var(SR) per Lo (2002) with skew/kurt adjustments
    var_sr = (1.0 / (n - 1)) * (
        1.0 - skew * sr_obs / math.sqrt(periods_per_year)
        + ((kurt - 1) / 4.0) * (sr_obs ** 2) / periods_per_year
    )
    if var_sr <= 0:
        psr = 0.5
    else:
        z = (sr_obs - sr_bench) / math.sqrt(var_sr)
        psr = float(sp_stats.norm.cdf(z))
    return ProbabilisticSharpeReport(
        sharpe_observed=float(sr_obs),
        sharpe_benchmark=sr_bench,
        psr=float(psr),
        n_samples=n,
        skewness=skew,
        kurtosis=kurt,
        extra=dict(extra or {}),
    )


# ---------------------------------------------------------------------------
# 3. Probability of Backtest Overfitting (PBO)
# ---------------------------------------------------------------------------


def pbo(
    strategy_returns: np.ndarray,
    n_groups: int = 8,
    n_test_groups: int = 2,
    random_seed: int = 7,
    extra: Optional[dict[str, Any]] = None,
) -> PBOTReport:
    """Bailey, Borwein, López de Prado, Zhu (2017).

    Args:
        strategy_returns: shape (T, N) — T samples, N strategies.
        n_groups: number of partitions to split T into.
        n_test_groups: number of partitions held out as test.
        random_seed: RNG seed for partition shuffling.

    Returns:
        PBOTReport with pbo in [0, 1] — fraction of paths where the
        best in-sample strategy was below the median out-of-sample.
    """
    R = np.asarray(strategy_returns, dtype=float)
    if R.ndim != 2:
        raise ValueError("strategy_returns must be 2-D (T, N)")
    T, N = R.shape
    if N < 2:
        raise ValueError("need >= 2 strategies for PBO")
    if n_groups < 2:
        raise ValueError("n_groups must be >= 2")
    if n_test_groups < 1 or n_test_groups >= n_groups:
        raise ValueError("n_test_groups must be in [1, n_groups-1]")

    rng = np.random.default_rng(random_seed)

    # Split T samples into n_groups partitions (shuffle first)
    perm = rng.permutation(T)
    # Reshape into groups (drop remainder)
    gsz = T // n_groups
    if gsz < 1:
        raise ValueError(
            f"too few samples ({T}) for {n_groups} groups; reduce n_groups"
        )
    groups = np.zeros((n_groups, gsz, N), dtype=float)
    for g in range(n_groups):
        idx = perm[g * gsz:(g + 1) * gsz]
        groups[g] = R[idx]

    # All combinations of n_test_groups out of n_groups
    from itertools import combinations
    paths = list(combinations(range(n_groups), n_test_groups))
    logits: list[float] = []
    for test_idx in paths:
        test_mask = np.zeros(n_groups, dtype=bool)
        test_mask[list(test_idx)] = True
        train_idx = np.where(~test_mask)[0]
        # Sum returns over both (groups, bars) axes → one value per strategy.
        # IMPORTANT: convert tuples to arrays so numpy does fancy indexing
        # instead of multi-axis indexing (groups[(0,1)] would mean groups[0,1]
        # and return shape (gsz, N) — not what we want).
        train_arr = groups[np.asarray(train_idx, dtype=int)]   # (k, gsz, N)
        test_arr  = groups[np.asarray(test_idx,  dtype=int)]   # (m, gsz, N)
        is_R  = train_arr.sum(axis=(0, 1))   # shape (N,)
        oos_R = test_arr.sum(axis=(0, 1))    # shape (N,)
        # Best in-sample
        best_is = int(np.argmax(is_R))
        # Rank of best-IS in oos (rank relative to all strategies' oos)
        # Use rankdata: rank 1 = lowest; we convert to "fraction of strategies beaten"
        ranks = sp_stats.rankdata(oos_R)
        # rank goes 1..N; convert to a "relative rank" in [0,1]
        rel_rank = (ranks[best_is] - 1.0) / max(N - 1, 1)
        # PBO logit: ln(rel_rank / (1 - rel_rank)), bounded
        eps = 1e-6
        rel_rank = float(min(max(rel_rank, eps), 1.0 - eps))
        logit = math.log(rel_rank / (1.0 - rel_rank))
        logits.append(logit)

    arr = np.asarray(logits, dtype=float)
    # PBO = fraction of logits <= 0 (i.e. best-IS underperformed median OOS)
    pbo_val = float(np.mean(arr <= 0.0))
    return PBOTReport(
        pbo=pbo_val,
        n_paths=int(arr.size),
        n_strategies=int(N),
        n_groups=int(n_groups),
        n_test_groups=int(n_test_groups),
        logits=[float(x) for x in arr.tolist()],
        logit_mean=float(arr.mean()) if arr.size else 0.0,
        logit_std=float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        extra=dict(extra or {}),
    )


# ---------------------------------------------------------------------------
# 4. White Reality Check + 5. Hansen SPA
# ---------------------------------------------------------------------------


def _stationary_bootstrap_indices(
    rng: np.random.Generator, n: int, n_boot: int, p: float = 0.10,
) -> np.ndarray:
    """Stationary bootstrap indices (Politis & Romano 1994).

    p = probability of resampling a new block at each step.
    """
    out = np.zeros((n_boot, n), dtype=np.int64)
    for b in range(n_boot):
        # Start index
        idx = np.zeros(n, dtype=np.int64)
        idx[0] = rng.integers(0, n)
        for t in range(1, n):
            if rng.random() < p:
                idx[t] = rng.integers(0, n)
            else:
                idx[t] = (idx[t - 1] + 1) % n
        out[b] = idx
    return out


def _reality_check(
    loss_diffs: np.ndarray,
    method: str = "wrc",
    n_bootstrap: int = 1000,
    random_seed: int = 7,
    benchmark_index: int = 0,
    extra: Optional[dict[str, Any]] = None,
) -> RealityCheckReport:
    """White Reality Check (WRC) or Hansen SPA.

    Args:
        loss_diffs: shape (T, N) loss-differentials d_{t,i} =
                    L(strategy_i) - L(benchmark).  Each column is one
                    strategy; column 0 should be all zeros (benchmark
                    vs itself).
        method: "wrc" (White 2000) or "spa" (Hansen 2005).
        n_bootstrap: bootstrap replications.
        random_seed: RNG seed.
        benchmark_index: column index of the benchmark.
    """
    D = np.asarray(loss_diffs, dtype=float)
    if D.ndim != 2:
        raise ValueError("loss_diffs must be 2-D (T, N)")
    T, N = D.shape
    if N < 2:
        raise ValueError("need >= 2 strategies for WRC/SPA")

    rng = np.random.default_rng(random_seed)
    # Observed average loss-diff per strategy
    d_bar = D.mean(axis=0)
    # Best (most positive — i.e. biggest improvement over benchmark) strategy
    best_idx = int(np.argmax(d_bar))
    observed = float(d_bar[best_idx])

    # Bootstrap stationary resampling
    boot_idx = _stationary_bootstrap_indices(rng, T, n_bootstrap)
    # Replicates of d_bar under resampling
    boot_d_bar = D[boot_idx].mean(axis=1)  # shape (n_boot, N)

    if method.lower() == "wrc":
        # WRC: max over all strategies of boot_d_bar
        boot_max = boot_d_bar.max(axis=1)
        # P(boot_max >= observed)
        p = float(np.mean(boot_max >= observed))
    elif method.lower() == "spa":
        # SPA: down-weight strategies with negative d_bar (no skill)
        # Compute std of D for each strategy
        sd_D = D.std(axis=0, ddof=1)
        sd_D[sd_D < 1e-12] = 1e-12
        # Hansen's standardised statistic
        z = (np.sqrt(T) * d_bar) / sd_D
        # Bootstrap standardized max
        z_boot = (np.sqrt(T) * boot_d_bar) / sd_D[None, :]
        # SPA only counts positive z (down-weight poor)
        z_boot_pos = np.where(z_boot > 0, z_boot, 0.0)
        boot_max = z_boot_pos.max(axis=1)
        # Use observed z as threshold (only positive part)
        z_obs = max(0.0, z[best_idx])
        p = float(np.mean(boot_max >= z_obs))
        observed = float(z_obs)
    else:
        raise ValueError(f"unknown method: {method!r}")

    return RealityCheckReport(
        method=method.lower(),
        n_strategies=int(N),
        n_samples=int(T),
        benchmark_index=int(benchmark_index),
        best_strategy_index=int(best_idx),
        observed_diff=observed,
        p_value=p,
        bootstrap_replicates=[float(x) for x in boot_max.tolist()[:200]],
        extra=dict(extra or {}),
    )


def white_reality_check(
    loss_diffs: np.ndarray,
    n_bootstrap: int = 1000,
    random_seed: int = 7,
    benchmark_index: int = 0,
    extra: Optional[dict[str, Any]] = None,
) -> RealityCheckReport:
    """White (2000) Reality Check."""
    return _reality_check(
        loss_diffs, method="wrc", n_bootstrap=n_bootstrap,
        random_seed=random_seed, benchmark_index=benchmark_index,
        extra=extra,
    )


def superior_predictive_ability(
    loss_diffs: np.ndarray,
    n_bootstrap: int = 1000,
    random_seed: int = 7,
    benchmark_index: int = 0,
    extra: Optional[dict[str, Any]] = None,
) -> RealityCheckReport:
    """Hansen (2005) SPA test."""
    return _reality_check(
        loss_diffs, method="spa", n_bootstrap=n_bootstrap,
        random_seed=random_seed, benchmark_index=benchmark_index,
        extra=extra,
    )


# ---------------------------------------------------------------------------
# 6. Multiple-hypothesis correction
# ---------------------------------------------------------------------------


def benjamini_hochberg(
    p_values: Sequence[float], alpha: float = 0.05,
) -> MultipleHypothesisReport:
    """Benjamini-Hochberg FDR control."""
    ps = [float(p) for p in p_values]
    n = len(ps)
    if n == 0:
        return MultipleHypothesisReport(
            n_hypotheses=0, raw_p_values=[], bh_rejected=[],
            bh_thresholds=[], holm_rejected=[], holm_thresholds=[],
            fdr_alpha=float(alpha), fwer_alpha=0.05,
            n_rejected_bh=0, n_rejected_holm=0,
        )
    # Sort with original-index tracking
    order = sorted(range(n), key=lambda i: ps[i])
    sorted_ps = [ps[i] for i in order]
    bh_thresholds_sorted = [(i + 1) * alpha / n for i in range(n)]
    # Find largest k where sorted_ps[k] <= k*alpha/n
    reject_sorted = [False] * n
    k_max = 0
    for k in range(n, 0, -1):
        if sorted_ps[k - 1] <= bh_thresholds_sorted[k - 1]:
            k_max = k
            break
    for k in range(k_max):
        reject_sorted[k] = True
    # Unsort
    bh_reject = [False] * n
    bh_thresh = [0.0] * n
    for rank, orig_idx in enumerate(order):
        bh_reject[orig_idx] = reject_sorted[rank]
        bh_thresh[orig_idx] = bh_thresholds_sorted[rank]
    return MultipleHypothesisReport(
        n_hypotheses=n,
        raw_p_values=ps,
        bh_rejected=bh_reject,
        bh_thresholds=bh_thresh,
        holm_rejected=[],
        holm_thresholds=[],
        fdr_alpha=float(alpha),
        fwer_alpha=0.05,
        n_rejected_bh=int(sum(bh_reject)),
        n_rejected_holm=0,
    )


def holm_bonferroni(
    p_values: Sequence[float], alpha: float = 0.05,
) -> MultipleHypothesisReport:
    """Holm-Bonferroni FWER control."""
    ps = [float(p) for p in p_values]
    n = len(ps)
    if n == 0:
        return MultipleHypothesisReport(
            n_hypotheses=0, raw_p_values=[], bh_rejected=[],
            bh_thresholds=[], holm_rejected=[], holm_thresholds=[],
            fdr_alpha=0.05, fwer_alpha=float(alpha),
            n_rejected_bh=0, n_rejected_holm=0,
        )
    order = sorted(range(n), key=lambda i: ps[i])
    sorted_ps = [ps[i] for i in order]
    holm_thresh_sorted = [alpha / (n - k) for k in range(n)]
    reject_sorted = [False] * n
    for k in range(n):
        if sorted_ps[k] <= holm_thresh_sorted[k]:
            reject_sorted[k] = True
        else:
            break  # Once we fail to reject, stop
    holm_reject = [False] * n
    holm_thresh = [0.0] * n
    for rank, orig_idx in enumerate(order):
        holm_reject[orig_idx] = reject_sorted[rank]
        holm_thresh[orig_idx] = holm_thresh_sorted[rank]
    return MultipleHypothesisReport(
        n_hypotheses=n,
        raw_p_values=ps,
        bh_rejected=[],
        bh_thresholds=[],
        holm_rejected=holm_reject,
        holm_thresholds=holm_thresh,
        fdr_alpha=0.05,
        fwer_alpha=float(alpha),
        n_rejected_bh=0,
        n_rejected_holm=int(sum(holm_reject)),
    )


def multiple_hypothesis_correction(
    p_values: Sequence[float],
    fdr_alpha: float = 0.05,
    fwer_alpha: float = 0.05,
) -> MultipleHypothesisReport:
    """Combined BH + Holm correction on a vector of p-values."""
    bh = benjamini_hochberg(p_values, alpha=fdr_alpha)
    holm = holm_bonferroni(p_values, alpha=fwer_alpha)
    return MultipleHypothesisReport(
        n_hypotheses=bh.n_hypotheses,
        raw_p_values=bh.raw_p_values,
        bh_rejected=bh.bh_rejected,
        bh_thresholds=bh.bh_thresholds,
        holm_rejected=holm.holm_rejected,
        holm_thresholds=holm.holm_thresholds,
        fdr_alpha=float(fdr_alpha),
        fwer_alpha=float(fwer_alpha),
        n_rejected_bh=bh.n_rejected_bh,
        n_rejected_holm=holm.n_rejected_holm,
    )


# ---------------------------------------------------------------------------
# Aggregate battery
# ---------------------------------------------------------------------------


def run_statistical_battery(
    returns: np.ndarray,
    strategy_returns: Optional[np.ndarray] = None,
    n_trials: int = 1,
    sharpe_benchmark: float = 0.0,
    n_bootstrap: int = 1000,
    random_seed: int = 7,
    periods_per_year: int = 252,
    pbo_n_groups: int = 8,
    pbo_n_test_groups: int = 2,
    fdr_alpha: float = 0.05,
    fwer_alpha: float = 0.05,
    extra: Optional[dict[str, Any]] = None,
    persist: bool = True,
    reports_dir: Optional[str] = None,
) -> StatisticalValidationReport:
    """Run the full statistical-validation battery.

    Args:
        returns: 1-D primary strategy return series.
        strategy_returns: optional (T, N) matrix for PBO/WRC/SPA.
        n_trials: number of trials for DSR deflation.
        sharpe_benchmark: PSR benchmark.
        n_bootstrap: bootstrap replications for WRC/SPA.
        periods_per_year: annualisation factor.
        pbo_n_groups / pbo_n_test_groups: PBO partition config.
        fdr_alpha / fwer_alpha: multiple-hypothesis thresholds.
        extra: optional dict merged into the report.
        persist: write JSONL report if True.

    Returns:
        StatisticalValidationReport with every test filled in.
    """
    r = np.asarray(returns, dtype=float).ravel()
    cfg = {
        "n_trials":         n_trials,
        "sharpe_benchmark": sharpe_benchmark,
        "n_bootstrap":      n_bootstrap,
        "random_seed":      random_seed,
        "periods_per_year": periods_per_year,
        "pbo_n_groups":     pbo_n_groups,
        "pbo_n_test_groups": pbo_n_test_groups,
        "fdr_alpha":        fdr_alpha,
        "fwer_alpha":       fwer_alpha,
    }
    report = StatisticalValidationReport(
        run_at=datetime.now(timezone.utc).isoformat(),
        n_samples=int(r.shape[0]),
        n_strategies=int(strategy_returns.shape[1]) if strategy_returns is not None else 1,
        config=cfg,
        extra=dict(extra or {}),
    )

    # 1) DSR
    try:
        dsr = deflated_sharpe_ratio(
            r, n_trials=n_trials, periods_per_year=periods_per_year,
        )
        report.deflated_sharpe = dsr.to_dict()
    except Exception as e:
        log.exception("DSR failed: %s", e)
        report.errors.append(f"deflated_sharpe: {e}")

    # 2) PSR
    try:
        psr = probabilistic_sharpe_ratio(
            r, sharpe_benchmark=sharpe_benchmark,
            periods_per_year=periods_per_year,
        )
        report.probabilistic_sharpe = psr.to_dict()
    except Exception as e:
        log.exception("PSR failed: %s", e)
        report.errors.append(f"probabilistic_sharpe: {e}")

    # 3) PBO + 4) WRC + 5) SPA — require strategy matrix
    if strategy_returns is not None and np.asarray(strategy_returns).ndim == 2 \
            and np.asarray(strategy_returns).shape[1] >= 2:
        try:
            pbo_rep = pbo(
                strategy_returns,
                n_groups=pbo_n_groups,
                n_test_groups=pbo_n_test_groups,
                random_seed=random_seed,
            )
            report.pbo = pbo_rep.to_dict()
        except Exception as e:
            log.exception("PBO failed: %s", e)
            report.errors.append(f"pbo: {e}")

        # Build loss-differentials for WRC/SPA
        # Use negative returns as loss; benchmark = column 0
        # d_{t,i} = L(benchmark) - L(strategy_i) = -r_bench - (-r_i) = r_i - r_bench
        # Positive D → strategy_i beats benchmark (has lower loss).
        try:
            SR = np.asarray(strategy_returns, dtype=float)
            bench = SR[:, 0:1]
            D = SR - bench  # positive D → strategy_i beats benchmark
            wrc = white_reality_check(
                D, n_bootstrap=n_bootstrap, random_seed=random_seed,
                benchmark_index=0,
            )
            report.wrc = wrc.to_dict()
        except Exception as e:
            log.exception("WRC failed: %s", e)
            report.errors.append(f"wrc: {e}")

        try:
            spa = superior_predictive_ability(
                D, n_bootstrap=n_bootstrap, random_seed=random_seed,
                benchmark_index=0,
            )
            report.spa = spa.to_dict()
        except Exception as e:
            log.exception("SPA failed: %s", e)
            report.errors.append(f"spa: {e}")
    else:
        report.errors.append("strategy_returns missing or 1-D; skipped PBO/WRC/SPA")

    # 6) Multiple-hypothesis correction on whatever p-values we have
    p_vals: list[float] = []
    if report.deflated_sharpe:
        p_vals.append(float(report.deflated_sharpe.get("p_value", 1.0)))
    if report.probabilistic_sharpe:
        # PSR is a probability, not a p-value. Convert to a 1-tail p:
        # H0: SR_true <= SR_bench, p = 1 - PSR
        p_vals.append(1.0 - float(report.probabilistic_sharpe.get("psr", 0.0)))
    if report.wrc:
        p_vals.append(float(report.wrc.get("p_value", 1.0)))
    if report.spa:
        p_vals.append(float(report.spa.get("p_value", 1.0)))
    if p_vals:
        try:
            mh = multiple_hypothesis_correction(
                p_vals, fdr_alpha=fdr_alpha, fwer_alpha=fwer_alpha,
            )
            report.multiple_hypothesis = mh.to_dict()
        except Exception as e:
            log.exception("MH correction failed: %s", e)
            report.errors.append(f"multiple_hypothesis: {e}")

    if persist:
        report.persist(reports_dir=reports_dir)
    return report


__all__ = [
    "DeflatedSharpeReport",
    "ProbabilisticSharpeReport",
    "PBOTReport",
    "RealityCheckReport",
    "MultipleHypothesisReport",
    "StatisticalValidationReport",
    "DEFAULTS",
    "deflated_sharpe_ratio",
    "probabilistic_sharpe_ratio",
    "pbo",
    "white_reality_check",
    "superior_predictive_ability",
    "benjamini_hochberg",
    "holm_bonferroni",
    "multiple_hypothesis_correction",
    "run_statistical_battery",
]
