"""
SENECIO ORACLE — ACT XXVIII Module 4: Capacity Model
======================================================

Estimates the maximum deployable capital for a strategy given market
microstructure constraints.  Combines:

  1. ADV (Average Daily Volume) estimation — EMA + median + robust
     estimators so we can pick the most pessimistic.
  2. Market-impact models — Almgren-Chriss square-root impact
     (impact_bps = k * sqrt(q/ADV) * 1e4), plus the Kissell linear
     approximation for small orders.
  3. Liquidity constraints — max % of ADV per order (default 1 %),
     max % of bid-ask depth per order (default 25 %).
  4. Capacity limits — order-level cap, daily cap, position-level cap.
  5. Capital scalability — sweep capital from small to large, find the
     inflection point where market-impact cost erodes expected edge.
  6. Maximum deployable capital — the largest capital for which the
     after-impact expected edge remains > 0 (or > a configurable
     minimum risk-adjusted return).

Inputs are:
  - historical volume series (per bar — e.g. 15-minute bars)
  - historical price series
  - orderbook depth snapshots (optional but recommended)
  - expected edge per trade (decimal return, e.g. 0.005 = 0.5 %)
  - target frequency (trades per day)

Reports persist as JSONL under `data/research/capacity_reports/`.

References:
  - Almgren, R. & Chriss, N. (2000) "Optimal Execution of Portfolio
    Transactions"
  - Kissell, R. (2013) "The Science of Algorithmic Trading and Portfolio
    Management"
  - Torre, N. (1997) "Market Impact Model" (BARRA)

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
from typing import Any, Optional, Sequence

import numpy as np

log = logging.getLogger("senecio.research.capacity")


DEFAULTS: dict[str, Any] = {
    "reports_dir":                "data/research/capacity_reports",
    # ADV
    "adv_ema_span":               20,
    # Liquidity
    "max_pct_adv_per_order":      0.01,   # 1 % ADV
    "max_pct_depth_per_order":    0.25,   # 25 % top-of-book depth
    "max_pct_adv_per_day":        0.10,   # 10 % ADV per day
    # Impact
    "almgren_k":                  0.10,   # square-root coefficient
    "kissell_eta":                0.05,   # linear-impact coefficient
    "slippage_bps_floor":         1.0,    # min realised slippage
    # Scalability sweep
    "min_capital":                1_000.0,
    "max_capital":                10_000_000.0,
    "capital_steps":              40,
    # Capacity thresholds
    "min_edge_after_impact":      0.0005, # 5 bps minimum post-impact
    "min_risk_adjusted_return":   0.5,    # min Sharpe-like ratio
    # Trade frequency
    "trades_per_day":             10.0,
    "fee_bps_per_trade":          2.0,
    "holding_periods_per_year":   252.0,
}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class ADVEstimate:
    """Average Daily Volume estimate from a volume series."""
    n_samples: int
    adv_ema: float
    adv_median: float
    adv_mean: float
    adv_p05: float
    adv_p25: float
    adv_p75: float
    adv_p95: float
    adv_min: float
    adv_max: float
    recommended_adv: float     # most pessimistic usable number
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketImpactEstimate:
    """Per-order market-impact estimate."""
    order_qty: float           # in base-asset units
    order_value_usd: float
    adv: float
    pct_adv: float             # order / ADV
    almgren_chriss_bps: float
    kissell_bps: float
    total_impact_bps: float    # max of the two estimates
    slippage_bps: float        # total impact + floor
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScalabilityPoint:
    capital: float
    order_qty: float
    pct_adv: float
    pct_depth: float | None
    impact_bps: float
    fee_bps: float
    gross_edge_bps: float
    net_edge_bps: float
    passable: bool


@dataclass
class CapacityReport:
    """Full capacity-model report."""
    run_at: str
    adv_estimate: dict[str, Any] = field(default_factory=dict)
    liquidity_limits: dict[str, Any] = field(default_factory=dict)
    scalability_curve: list[dict[str, Any]] = field(default_factory=list)
    max_deployable_capital: float = 0.0
    max_capital_reason: str = ""
    recommended_capital: float = 0.0
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
            path = out_dir / f"capacity_{day}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self.to_dict(), default=str) + "\n")
            return path
        except Exception as e:
            log.warning("failed to persist capacity report: %s", e)
            return None


# ---------------------------------------------------------------------------
# ADV estimation
# ---------------------------------------------------------------------------


def estimate_adv(
    volumes: np.ndarray, ema_span: int = 20,
) -> ADVEstimate:
    """Estimate ADV from a volume series.

    The series can be either:
      - 1-D daily volumes → used directly
      - 1-D intraday volumes at fixed bar size → aggregated by
        `periods_per_day` if provided (we infer heuristically if not)
    """
    v = np.asarray(volumes, dtype=float).ravel()
    n = int(v.size)
    if n == 0:
        return ADVEstimate(
            n_samples=0, adv_ema=0.0, adv_median=0.0, adv_mean=0.0,
            adv_p05=0.0, adv_p25=0.0, adv_p75=0.0, adv_p95=0.0,
            adv_min=0.0, adv_max=0.0, recommended_adv=0.0,
        )
    # EMA
    alpha = 2.0 / max(ema_span, 1)
    ema = float(v[0])
    for x in v[1:]:
        ema = alpha * float(x) + (1.0 - alpha) * ema
    # Robust percentiles
    p = np.percentile(v, [5, 25, 50, 75, 95])
    # Recommended ADV = min(EMA, p25) — most pessimistic
    recommended = float(min(ema, p[1])) if n > 0 else 0.0
    return ADVEstimate(
        n_samples=n,
        adv_ema=ema,
        adv_median=float(p[2]),
        adv_mean=float(v.mean()),
        adv_p05=float(p[0]),
        adv_p25=float(p[1]),
        adv_p75=float(p[3]),
        adv_p95=float(p[4]),
        adv_min=float(v.min()),
        adv_max=float(v.max()),
        recommended_adv=recommended,
    )


# ---------------------------------------------------------------------------
# Market-impact models
# ---------------------------------------------------------------------------


def almgren_chriss_impact(
    order_qty: float, adv: float, k: float = 0.10,
) -> float:
    """Square-root market impact in *decimal* (multiply by 1e4 for bps).

    impact = k * sqrt(q / ADV)
    """
    if adv <= 0:
        return 0.0
    ratio = float(order_qty) / float(adv)
    if ratio <= 0:
        return 0.0
    return float(k) * math.sqrt(ratio)


def kissell_linear_impact(
    order_qty: float, adv: float, eta: float = 0.05,
) -> float:
    """Linear (Kissell) market impact in *decimal*.

    impact = eta * (q / ADV)
    """
    if adv <= 0:
        return 0.0
    ratio = float(order_qty) / float(adv)
    if ratio <= 0:
        return 0.0
    return float(eta) * ratio


def estimate_market_impact(
    order_qty: float, adv: float,
    k: float = 0.10, eta: float = 0.05,
    slippage_bps_floor: float = 1.0,
) -> MarketImpactEstimate:
    """Combine Almgren-Chriss + Kissell + floor into a single estimate."""
    ac = almgren_chriss_impact(order_qty, adv, k=k) * 10_000.0  # bps
    kl = kissell_linear_impact(order_qty, adv, eta=eta) * 10_000.0  # bps
    total = max(ac, kl)
    slippage = total + slippage_bps_floor
    return MarketImpactEstimate(
        order_qty=float(order_qty),
        order_value_usd=0.0,  # filled by caller if price is known
        adv=float(adv),
        pct_adv=float(order_qty / adv) if adv > 0 else 0.0,
        almgren_chriss_bps=float(ac),
        kissell_bps=float(kl),
        total_impact_bps=float(total),
        slippage_bps=float(slippage),
    )


# ---------------------------------------------------------------------------
# Capacity model
# ---------------------------------------------------------------------------


def estimate_capacity(
    volumes: np.ndarray,
    prices: Optional[np.ndarray] = None,
    depth_usd: Optional[float] = None,
    gross_edge_bps: float = 50.0,
    trades_per_day: float = 10.0,
    fee_bps_per_trade: float = 2.0,
    max_pct_adv_per_order: float = 0.01,
    max_pct_depth_per_order: float = 0.25,
    max_pct_adv_per_day: float = 0.10,
    almgren_k: float = 0.10,
    kissell_eta: float = 0.05,
    slippage_bps_floor: float = 1.0,
    min_edge_after_impact_bps: float = 5.0,
    min_capital: float = 1_000.0,
    max_capital: float = 10_000_000.0,
    capital_steps: int = 40,
    extra: Optional[dict[str, Any]] = None,
    persist: bool = True,
    reports_dir: Optional[str] = None,
) -> CapacityReport:
    """Estimate maximum deployable capital.

    Args:
        volumes: historical volume series (per bar).
        prices: historical price series (per bar). Used to convert
            capital → order quantity. If None, uses 1.0 as price.
        depth_usd: top-of-book depth in USD (single representative
            snapshot). If None, depth check is skipped.
        gross_edge_bps: expected per-trade edge before impact/fees.
        trades_per_day: average number of trades executed per day.
        fee_bps_per_trade: round-trip fee in bps.
        max_pct_adv_per_order: max fraction of ADV per order.
        max_pct_depth_per_order: max fraction of top-of-book depth.
        max_pct_adv_per_day: max fraction of ADV per day across all orders.
        almgren_k: square-root impact coefficient.
        kissell_eta: linear impact coefficient.
        slippage_bps_floor: minimum realised slippage.
        min_edge_after_impact_bps: minimum net edge for capital to be
            "passable" at a given point.
        min_capital / max_capital: scalability sweep bounds.
        capital_steps: scalability sweep resolution.
        extra: optional dict merged into the report.
        persist: write JSONL report if True.
    """
    cfg = {k: v for k, v in locals().items() if k not in
           {"volumes", "prices", "depth_usd", "extra", "persist", "reports_dir"}}
    report = CapacityReport(
        run_at=datetime.now(timezone.utc).isoformat(),
        config=cfg,
        extra=dict(extra or {}),
    )

    # ADV
    adv_rep = estimate_adv(volumes)
    report.adv_estimate = adv_rep.to_dict()
    adv = adv_rep.recommended_adv or adv_rep.adv_median or 0.0
    if adv <= 0:
        report.errors.append("ADV estimate is zero — cannot model capacity")
        return report

    # Liquidity constraints
    max_order_qty_by_adv = adv * max_pct_adv_per_order
    max_order_qty_by_depth = (
        (depth_usd / (prices[-1] if prices is not None and prices.size else 1.0))
        * max_pct_depth_per_order
        if depth_usd is not None else None
    )
    max_daily_qty_by_adv = adv * max_pct_adv_per_day
    max_order_qty_by_daily = (
        max_daily_qty_by_adv / max(trades_per_day, 1e-9)
    )

    # Build the list of binding constraints (filter out None depth)
    _candidates = [max_order_qty_by_adv, max_order_qty_by_daily]
    if max_order_qty_by_depth is not None:
        _candidates.append(max_order_qty_by_depth)
    binding_max_order_qty = float(min(_candidates)) if _candidates else 0.0

    constraints = {
        "adv": adv,
        "max_order_qty_by_adv": float(max_order_qty_by_adv),
        "max_order_qty_by_depth": (
            float(max_order_qty_by_depth) if max_order_qty_by_depth is not None else None
        ),
        "max_order_qty_by_daily_adv": float(max_order_qty_by_daily),
        "binding_max_order_qty": binding_max_order_qty,
    }
    report.liquidity_limits = constraints

    # Scalability sweep
    price_now = float(prices[-1]) if prices is not None and prices.size else 1.0
    capitals = np.geomspace(
        max(min_capital, 1.0), max(max_capital, min_capital + 1.0),
        capital_steps,
    )
    curve: list[ScalabilityPoint] = []
    max_passable_capital = 0.0
    reason = "no_passable_point"
    for cap in capitals:
        # Order qty = capital / price (one-shot, no leverage)
        # Use half the capital per trade (conservative) so a position can be
        # rotated without forcing the full capital into a single fill.
        order_qty = (cap / price_now) * 0.5
        impact = estimate_market_impact(
            order_qty=order_qty, adv=adv,
            k=almgren_k, eta=kissell_eta,
            slippage_bps_floor=slippage_bps_floor,
        )
        # Update impact's order_value_usd
        impact.order_value_usd = float(order_qty * price_now)
        net_edge = gross_edge_bps - impact.slippage_bps - fee_bps_per_trade
        passable = net_edge >= min_edge_after_impact_bps
        # Also enforce liquidity caps
        if order_qty > constraints["binding_max_order_qty"]:
            passable = False
            reason = "liquidity_constraint"
        pct_depth = (
            (impact.order_value_usd / depth_usd) if depth_usd and depth_usd > 0 else None
        )
        if pct_depth is not None and pct_depth > max_pct_depth_per_order:
            passable = False
            reason = "depth_constraint"
        curve.append(ScalabilityPoint(
            capital=float(cap),
            order_qty=float(order_qty),
            pct_adv=float(impact.pct_adv),
            pct_depth=float(pct_depth) if pct_depth is not None else None,
            impact_bps=float(impact.slippage_bps),
            fee_bps=float(fee_bps_per_trade),
            gross_edge_bps=float(gross_edge_bps),
            net_edge_bps=float(net_edge),
            passable=bool(passable),
        ))
        if passable:
            max_passable_capital = float(cap)
            reason = "edge_exhausted"
    # If the very first point is already failing, the strategy is
    # unprofitable even at minimum capital
    if max_passable_capital == 0.0:
        if curve and curve[0].passable is False:
            reason = "no_edge_at_min_capital"
    report.scalability_curve = [p.__dict__ for p in curve]
    report.max_deployable_capital = float(max_passable_capital)
    report.max_capital_reason = reason
    # Recommended capital = 50 % of max (safety margin for vol expansion)
    report.recommended_capital = float(max_passable_capital * 0.5)
    if persist:
        report.persist(reports_dir=reports_dir)
    return report


__all__ = [
    "ADVEstimate",
    "MarketImpactEstimate",
    "ScalabilityPoint",
    "CapacityReport",
    "DEFAULTS",
    "estimate_adv",
    "almgren_chriss_impact",
    "kissell_linear_impact",
    "estimate_market_impact",
    "estimate_capacity",
]
