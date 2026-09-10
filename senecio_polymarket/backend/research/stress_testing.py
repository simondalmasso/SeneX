"""
SENECIO ORACLE — ACT XXVIII Module 5: Stress Testing
======================================================

Scenario-based stress tests for the oracle's trade series.  Each test
takes a baseline trade history (per-trade decimal returns + market
context) and produces a stressed-equity outcome + key risk metrics.

Scenarios implemented:
  1. volatility_shock     — multiply per-trade return volatility by k
                            (e.g. 3×) and recompute Sharpe / max-DD.
  2. spread_shock         — add `shock_bps` of extra spread cost to
                            every trade.
  3. latency_shock        — penalise every trade by `shock_bps` of
                            slippage (mimics API/execution latency).
  4. exchange_outage      — zero out N consecutive trades (force-close
                            at market) starting at a random index.
  5. funding_shock        — for SHORT trades, subtract `shock_bps` of
                            funding; for LONG trades, add half that.
  6. gap_simulation       — inject one +X% / -X% overnight-style gap
                            at a configurable position in the series.
  7. black_swan           — combine: 3× vol + 5× spread + 2× latency +
                            one -10 % gap + 5 % outage.

Each scenario returns a `StressScenarioResult`.  The aggregate report
persists as JSONL under `data/research/stress_reports/`.

STRICT_ADDITIVE — does NOT touch prediction / feature / signal /
verifier / live-gate logic.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

log = logging.getLogger("senecio.research.stress")


DEFAULTS: dict[str, Any] = {
    "reports_dir":         "data/research/stress_reports",
    "random_seed":         17,
    "periods_per_year":    252,
}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class StressScenarioResult:
    """Outcome of one stress scenario."""
    name: str
    description: str
    n_trades: int
    baseline_metrics: dict[str, float] = field(default_factory=dict)
    stressed_metrics: dict[str, float] = field(default_factory=dict)
    delta_metrics: dict[str, float] = field(default_factory=dict)
    survived: bool = False           # final equity > 0
    final_equity: float = 1.0
    max_drawdown_stressed: float = 0.0
    config: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StressReport:
    """Aggregate stress-test battery report."""
    run_at: str
    n_trades: int
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    aggregate: dict[str, Any] = field(default_factory=dict)
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
            path = out_dir / f"stress_{day}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self.to_dict(), default=str) + "\n")
            return path
        except Exception as e:
            log.warning("failed to persist stress report: %s", e)
            return None


# ---------------------------------------------------------------------------
# Core metrics
# ---------------------------------------------------------------------------


def _metrics(returns: np.ndarray, periods_per_year: int = 252) -> dict[str, float]:
    r = np.asarray(returns, dtype=float).ravel()
    n = int(r.size)
    if n == 0:
        return {"n": 0.0}
    mu = float(r.mean())
    sd = float(r.std(ddof=1)) if n > 1 else 0.0
    sharpe = (mu / sd * math.sqrt(periods_per_year)) if sd > 1e-12 else 0.0
    # Max drawdown
    eq = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = float(dd.min()) if dd.size else 0.0
    # Profit factor
    gains = r[r > 0]
    losses = r[r < 0]
    gp = float(gains.sum()) if gains.size else 0.0
    gl = float(abs(losses.sum())) if losses.size else 0.0
    pf = (gp / gl) if gl > 1e-12 else (float("inf") if gp > 1e-12 else 0.0)
    final_eq = float(eq[-1]) if eq.size else 1.0
    return {
        "n":           float(n),
        "mean":        mu,
        "std":         sd,
        "sharpe":      float(sharpe),
        "max_drawdown": float(max_dd),
        "profit_factor": float(pf),
        "win_rate":    float(np.mean(r > 0)) if n else 0.0,
        "final_equity": float(final_eq),
    }


def _delta(baseline: dict[str, float], stressed: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in baseline.items():
        if isinstance(v, (int, float)) and math.isfinite(v):
            s = stressed.get(k, 0.0)
            if isinstance(s, (int, float)) and math.isfinite(s):
                out[f"delta_{k}"] = float(s - v)
                if abs(v) > 1e-12:
                    out[f"pct_delta_{k}"] = float((s - v) / abs(v))
    return out


# ---------------------------------------------------------------------------
# Scenario implementations
# ---------------------------------------------------------------------------


def _survived(stressed_returns: np.ndarray) -> tuple[bool, float, float]:
    eq = np.cumprod(1.0 + np.asarray(stressed_returns, dtype=float).ravel())
    final = float(eq[-1]) if eq.size else 1.0
    peak = np.maximum.accumulate(eq) if eq.size else np.asarray([1.0])
    dd = (eq - peak) / peak if eq.size else np.asarray([0.0])
    return (final > 0.0), final, float(dd.min())


def volatility_shock(
    returns: np.ndarray, k: float = 3.0,
    periods_per_year: int = 252,
) -> StressScenarioResult:
    """Multiply per-trade return std by `k` (preserves mean)."""
    r = np.asarray(returns, dtype=float).ravel()
    n = int(r.size)
    base = _metrics(r, periods_per_year)
    mu = float(r.mean()) if n else 0.0
    sd = float(r.std(ddof=1)) if n > 1 else 0.0
    if sd > 1e-12 and n > 0:
        stressed = mu + (r - mu) * float(k)
    else:
        stressed = r.copy()
    sm = _metrics(stressed, periods_per_year)
    surv, final, mdd = _survived(stressed)
    return StressScenarioResult(
        name="volatility_shock",
        description=f"Return std multiplied by {k}x (mean preserved)",
        n_trades=n,
        baseline_metrics=base,
        stressed_metrics=sm,
        delta_metrics=_delta(base, sm),
        survived=surv,
        final_equity=final,
        max_drawdown_stressed=mdd,
        config={"k": float(k)},
    )


def spread_shock(
    returns: np.ndarray, shock_bps: float = 5.0,
    periods_per_year: int = 252,
) -> StressScenarioResult:
    """Add `shock_bps` of extra cost to every trade."""
    r = np.asarray(returns, dtype=float).ravel()
    n = int(r.size)
    base = _metrics(r, periods_per_year)
    penalty = float(shock_bps) / 10000.0
    stressed = r - penalty
    sm = _metrics(stressed, periods_per_year)
    surv, final, mdd = _survived(stressed)
    return StressScenarioResult(
        name="spread_shock",
        description=f"+{shock_bps} bps spread cost per trade",
        n_trades=n,
        baseline_metrics=base,
        stressed_metrics=sm,
        delta_metrics=_delta(base, sm),
        survived=surv,
        final_equity=final,
        max_drawdown_stressed=mdd,
        config={"shock_bps": float(shock_bps)},
    )


def latency_shock(
    returns: np.ndarray, shock_bps: float = 3.0,
    periods_per_year: int = 252,
) -> StressScenarioResult:
    """Penalise every trade by `shock_bps` of slippage (latency)."""
    r = np.asarray(returns, dtype=float).ravel()
    n = int(r.size)
    base = _metrics(r, periods_per_year)
    penalty = float(shock_bps) / 10000.0
    stressed = r - penalty
    sm = _metrics(stressed, periods_per_year)
    surv, final, mdd = _survived(stressed)
    return StressScenarioResult(
        name="latency_shock",
        description=f"+{shock_bps} bps latency slippage per trade",
        n_trades=n,
        baseline_metrics=base,
        stressed_metrics=sm,
        delta_metrics=_delta(base, sm),
        survived=surv,
        final_equity=final,
        max_drawdown_stressed=mdd,
        config={"shock_bps": float(shock_bps)},
    )


def exchange_outage(
    returns: np.ndarray, outage_trades: int = 5,
    random_seed: int = 17,
    periods_per_year: int = 252,
) -> StressScenarioResult:
    """Zero-out N consecutive trades (force-close at market)."""
    r = np.asarray(returns, dtype=float).ravel()
    n = int(r.size)
    base = _metrics(r, periods_per_year)
    rng = np.random.default_rng(random_seed)
    stressed = r.copy()
    if n > 0 and outage_trades > 0:
        start = int(rng.integers(0, max(1, n - outage_trades + 1)))
        end = min(n, start + outage_trades)
        # Force-close = each closed trade loses a "panic slippage" of 10 bps
        stressed[start:end] = -0.001
    sm = _metrics(stressed, periods_per_year)
    surv, final, mdd = _survived(stressed)
    return StressScenarioResult(
        name="exchange_outage",
        description=f"{outage_trades} consecutive trades force-closed at -10 bps panic slippage",
        n_trades=n,
        baseline_metrics=base,
        stressed_metrics=sm,
        delta_metrics=_delta(base, sm),
        survived=surv,
        final_equity=final,
        max_drawdown_stressed=mdd,
        config={"outage_trades": int(outage_trades), "random_seed": int(random_seed)},
    )


def funding_shock(
    returns: np.ndarray, directions: Optional[np.ndarray] = None,
    shock_bps: float = 10.0,
    periods_per_year: int = 252,
) -> StressScenarioResult:
    """Apply a funding shock: SHORT trades pay `shock_bps`, LONG receive half.

    Args:
        returns: per-trade decimal returns.
        directions: array of +1 (LONG) / -1 (SHORT). If None, all trades
            are treated as LONG.
        shock_bps: funding shock magnitude in bps.
    """
    r = np.asarray(returns, dtype=float).ravel()
    n = int(r.size)
    base = _metrics(r, periods_per_year)
    if directions is None:
        dirs = np.ones(n, dtype=int)
    else:
        dirs = np.asarray(directions, dtype=int).ravel()
        if dirs.size != n:
            # Pad / truncate
            dirs = np.concatenate([dirs, np.ones(n - dirs.size, dtype=int)]) if dirs.size < n else dirs[:n]
    shock_dec = float(shock_bps) / 10000.0
    # SHORT (-1) pays shock_dec ; LONG (+1) receives shock_dec/2
    funding = np.where(dirs < 0, -shock_dec, shock_dec / 2.0)
    stressed = r + funding
    sm = _metrics(stressed, periods_per_year)
    surv, final, mdd = _survived(stressed)
    return StressScenarioResult(
        name="funding_shock",
        description=f"+{shock_bps} bps funding cost on SHORTs, +{shock_bps/2} bps on LONGs",
        n_trades=n,
        baseline_metrics=base,
        stressed_metrics=sm,
        delta_metrics=_delta(base, sm),
        survived=surv,
        final_equity=final,
        max_drawdown_stressed=mdd,
        config={"shock_bps": float(shock_bps)},
    )


def gap_simulation(
    returns: np.ndarray, gap_pct: float = -10.0,
    gap_position: float = 0.5,
    periods_per_year: int = 252,
) -> StressScenarioResult:
    """Inject one overnight-style gap at a configurable position.

    Args:
        returns: per-trade decimal returns.
        gap_pct: gap magnitude in percent (negative = adverse).
        gap_position: fractional position in series [0, 1].
    """
    r = np.asarray(returns, dtype=float).ravel()
    n = int(r.size)
    base = _metrics(r, periods_per_year)
    stressed = r.copy()
    if n > 0:
        pos = int(np.clip(gap_position, 0.0, 1.0) * (n - 1))
        stressed[pos] = stressed[pos] + float(gap_pct) / 100.0
    sm = _metrics(stressed, periods_per_year)
    surv, final, mdd = _survived(stressed)
    return StressScenarioResult(
        name="gap_simulation",
        description=f"Inject {gap_pct}% gap at position {gap_position:.2f}",
        n_trades=n,
        baseline_metrics=base,
        stressed_metrics=sm,
        delta_metrics=_delta(base, sm),
        survived=surv,
        final_equity=final,
        max_drawdown_stressed=mdd,
        config={"gap_pct": float(gap_pct), "gap_position": float(gap_position)},
    )


def black_swan(
    returns: np.ndarray, directions: Optional[np.ndarray] = None,
    vol_mult: float = 3.0,
    spread_bps: float = 5.0,
    latency_bps: float = 3.0,
    funding_bps: float = 10.0,
    gap_pct: float = -10.0,
    outage_trades: int = 5,
    gap_position: float = 0.5,
    random_seed: int = 17,
    periods_per_year: int = 252,
) -> StressScenarioResult:
    """Compound black-swan scenario: vol + spread + latency + funding + gap + outage."""
    r = np.asarray(returns, dtype=float).ravel()
    n = int(r.size)
    base = _metrics(r, periods_per_year)

    # Apply volatility scaling first (preserve mean)
    mu = float(r.mean()) if n else 0.0
    stressed = mu + (r - mu) * float(vol_mult) if n else r.copy()

    # Spread shock
    stressed = stressed - float(spread_bps) / 10000.0

    # Latency shock
    stressed = stressed - float(latency_bps) / 10000.0

    # Funding shock
    if directions is None:
        dirs = np.ones(n, dtype=int)
    else:
        dirs = np.asarray(directions, dtype=int).ravel()
        if dirs.size != n:
            dirs = np.concatenate([dirs, np.ones(n - dirs.size, dtype=int)]) if dirs.size < n else dirs[:n]
    funding_dec = float(funding_bps) / 10000.0
    funding = np.where(dirs < 0, -funding_dec, funding_dec / 2.0)
    stressed = stressed + funding

    # Gap
    if n > 0:
        pos = int(np.clip(gap_position, 0.0, 1.0) * (n - 1))
        stressed[pos] = stressed[pos] + float(gap_pct) / 100.0

    # Outage
    if n > 0 and outage_trades > 0:
        rng = np.random.default_rng(random_seed)
        start = int(rng.integers(0, max(1, n - outage_trades + 1)))
        end = min(n, start + outage_trades)
        stressed[start:end] = -0.001

    sm = _metrics(stressed, periods_per_year)
    surv, final, mdd = _survived(stressed)
    return StressScenarioResult(
        name="black_swan",
        description=(
            f"Compound: {vol_mult}x vol + {spread_bps} bps spread + "
            f"{latency_bps} bps latency + {funding_bps} bps funding + "
            f"{gap_pct}% gap + {outage_trades}-trade outage"
        ),
        n_trades=n,
        baseline_metrics=base,
        stressed_metrics=sm,
        delta_metrics=_delta(base, sm),
        survived=surv,
        final_equity=final,
        max_drawdown_stressed=mdd,
        config={
            "vol_mult": float(vol_mult),
            "spread_bps": float(spread_bps),
            "latency_bps": float(latency_bps),
            "funding_bps": float(funding_bps),
            "gap_pct": float(gap_pct),
            "gap_position": float(gap_position),
            "outage_trades": int(outage_trades),
            "random_seed": int(random_seed),
        },
    )


# ---------------------------------------------------------------------------
# Battery runner
# ---------------------------------------------------------------------------


def run_stress_battery(
    returns: np.ndarray,
    directions: Optional[np.ndarray] = None,
    vol_mult: float = 3.0,
    spread_bps: float = 5.0,
    latency_bps: float = 3.0,
    funding_bps: float = 10.0,
    gap_pct: float = -10.0,
    gap_position: float = 0.5,
    outage_trades: int = 5,
    random_seed: int = 17,
    periods_per_year: int = 252,
    extra: Optional[dict[str, Any]] = None,
    persist: bool = True,
    reports_dir: Optional[str] = None,
) -> StressReport:
    """Run all 7 stress scenarios on a trade series.

    Returns an aggregate StressReport with one entry per scenario plus
    an aggregate summary (worst-case max-DD, survival count, etc.).
    """
    r = np.asarray(returns, dtype=float).ravel()
    cfg = {
        "vol_mult":        vol_mult,
        "spread_bps":      spread_bps,
        "latency_bps":     latency_bps,
        "funding_bps":     funding_bps,
        "gap_pct":         gap_pct,
        "gap_position":    gap_position,
        "outage_trades":   outage_trades,
        "random_seed":     random_seed,
        "periods_per_year": periods_per_year,
    }
    report = StressReport(
        run_at=datetime.now(timezone.utc).isoformat(),
        n_trades=int(r.shape[0]),
        config=cfg,
        extra=dict(extra or {}),
    )
    if r.size == 0:
        report.errors.append("empty return series")
        return report

    scenarios: list[StressScenarioResult] = []
    scenarios.append(volatility_shock(r, k=vol_mult, periods_per_year=periods_per_year))
    scenarios.append(spread_shock(r, shock_bps=spread_bps, periods_per_year=periods_per_year))
    scenarios.append(latency_shock(r, shock_bps=latency_bps, periods_per_year=periods_per_year))
    scenarios.append(exchange_outage(
        r, outage_trades=outage_trades, random_seed=random_seed,
        periods_per_year=periods_per_year,
    ))
    scenarios.append(funding_shock(
        r, directions=directions, shock_bps=funding_bps,
        periods_per_year=periods_per_year,
    ))
    scenarios.append(gap_simulation(
        r, gap_pct=gap_pct, gap_position=gap_position,
        periods_per_year=periods_per_year,
    ))
    scenarios.append(black_swan(
        r, directions=directions,
        vol_mult=vol_mult, spread_bps=spread_bps, latency_bps=latency_bps,
        funding_bps=funding_bps, gap_pct=gap_pct, gap_position=gap_position,
        outage_trades=outage_trades, random_seed=random_seed,
        periods_per_year=periods_per_year,
    ))

    report.scenarios = [s.to_dict() for s in scenarios]

    # Aggregate
    surv_count = sum(1 for s in scenarios if s.survived)
    worst_dd = min((s.max_drawdown_stressed for s in scenarios), default=0.0)
    worst_eq = min((s.final_equity for s in scenarios), default=0.0)
    # Worst-case Sharpe delta
    worst_sharpe_delta = min(
        (s.delta_metrics.get("delta_sharpe", 0.0) for s in scenarios
         if "delta_sharpe" in s.delta_metrics),
        default=0.0,
    )
    report.aggregate = {
        "n_scenarios":          len(scenarios),
        "n_survived":           int(surv_count),
        "survival_rate":        float(surv_count / max(len(scenarios), 1)),
        "worst_max_drawdown":   float(worst_dd),
        "worst_final_equity":   float(worst_eq),
        "worst_sharpe_delta":   float(worst_sharpe_delta),
        "any_ruin":             bool(any(not s.survived for s in scenarios)),
    }

    if persist:
        report.persist(reports_dir=reports_dir)
    return report


__all__ = [
    "StressScenarioResult",
    "StressReport",
    "DEFAULTS",
    "volatility_shock",
    "spread_shock",
    "latency_shock",
    "exchange_outage",
    "funding_shock",
    "gap_simulation",
    "black_swan",
    "run_stress_battery",
]
