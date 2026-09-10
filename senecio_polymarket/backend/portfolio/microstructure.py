"""
SENECIO ORACLE — ACT XXVI: Microstructure Intelligence (priority 3)
====================================================================

Adds toxic-flow detection to the RiskKernel's decision pipeline.

WHAT THIS MODULE ADDS (additive — does NOT modify RiskKernel directly)
-----------------------------------------------------------------------
  1. **VPIN (Volume-Synchronized Probability of Informed Trading)**
     — Easley, López de Prado, O'Hara (2012). Bulk-volume classification
       of trade flow into BUY-initiated vs SELL-initiated buckets, then
       measures the *imbalance* per volume bucket. High VPIN ⇒ toxic flow
       ⇒ makers pull quotes ⇒ spreads widen ⇒ adverse selection.

  2. **OFI (Order Flow Imbalance)**
     — Cont, Kukanov, Stoikov (2014). Tracks changes in quantities at
       the top of the book: every tick, ΔOFI = Δbid_size − Δask_size
       (when bid/ask prices unchanged) plus jumps when prices move.
       Persistent negative OFI ⇒ aggressive sellers eating bids.

  3. **Liquidation heatmap proximity**
     — Approximation: distance from current price to the nearest
       "liquidation cluster" (psychological round numbers + recent
       high-volume nodes). When price is within X% of a cluster, the
       risk of cascading liquidations spikes.

  4. **Funding/OI divergence**
     — When funding rate flips extreme (|funding| > 0.05% per 8h = ~
       0.55% per day) AND open-interest is rising, the market is
       overcrowded → mean-reversion risk.

OUTPUT
------
A `MicrostructureReport` with a composite `toxic_score` in [0, 1]:
  - 0.0  = clean two-sided flow, safe to trade
  - 0.5  = elevated toxicity, halve the size
  - 1.0  = broken zone, REJECT all new entries

INTEGRATION
-----------
The RiskKernel consults this report via an injected `microstructure`
attribute. If toxic_score > reject_threshold (default 0.75), the
kernel REJECTS the proposal. If toxic_score > reduce_threshold
(default 0.40), size_scale is multiplied by 0.5.

The ExecutionEngine's FillSimulator also reads toxic_flow_score to
apply the adverse-selection multiplier on slippage.

This module is REACTIVE — it observes the same market_state dict
that institutional_core.ingest_market() already produces. It does NOT
modify the prediction pipeline or feature engineering.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("senecio.microstructure")


# -------------------- config --------------------

DEFAULTS: dict[str, Any] = {
    # VPIN
    "vpin_bucket_size_usd":       100_000.0,   # $100k per volume bucket
    "vpin_window_buckets":        50,           # 50 buckets = $5M volume lookback
    "vpin_toxic_threshold":       0.30,         # VPIN > 0.30 = elevated toxicity
    "vpin_extreme_threshold":     0.50,         # VPIN > 0.50 = extreme (broken zone)
    # OFI
    "ofi_window_ticks":           30,           # last 30 ticks (rolling)
    "ofi_toxic_threshold":        0.40,         # |OFI_normalized| > 0.40 = one-sided
    # Liquidation clusters (psychological round numbers)
    "liq_cluster_pct_threshold":  0.015,        # within 1.5% of a cluster
    "liq_cluster_round_sizes":    [10_000, 1_000, 100, 10],  # $10k, $1k, $100, $10 levels
    # Funding / OI
    "funding_extreme_bps":        5.0,          # |funding| > 5 bps per 8h = extreme
    "oi_growth_extreme_pct":      10.0,         # OI 24h change > +10% = overcrowded
    # Composite scoring weights (must sum to 1.0)
    "weight_vpin":                0.40,
    "weight_ofi":                 0.25,
    "weight_liquidation":         0.20,
    "weight_funding_oi":          0.15,
    # Risk-kernel thresholds (read by RiskKernel)
    "reject_threshold":           0.75,
    "reduce_threshold":           0.40,
    "reduce_size_scale":          0.50,
}


# -------------------- data classes --------------------

@dataclass
class MicrostructureReport:
    """Snapshot of microstructure state + composite toxic score."""
    toxic_score: float = 0.0                # 0..1 composite
    vpin: float = 0.0                       # 0..1 (probability of informed trading)
    ofi_normalized: float = 0.0             # -1..+1 (negative = seller pressure)
    ofi_toxic: bool = False
    near_liquidation_cluster: bool = False
    distance_to_cluster_pct: float = 0.0
    funding_extreme: bool = False
    funding_bps: float = 0.0
    oi_extreme: bool = False
    oi_change_24h_pct: float = 0.0
    # Components breakdown (for observability)
    components: dict = field(default_factory=dict)
    # Decision recommendation
    action: str = "ALLOW"                   # ALLOW | REDUCE | REJECT
    size_scale: float = 1.0
    ts: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -------------------- VPIN estimator --------------------

class VPINEstimator:
    """Volume-synchronized VPIN (Easley et al. 2012).

    Bulk-volume classification: divide the last N trades into equal-sized
    volume buckets, classify each bucket as BUY or SELL using price change
    direction within the bucket, then VPIN = Σ|buy_vol - sell_vol| / (N * bucket_size).

    In our low-frequency context (15-min cadence) we synthesize "trades"
    from OHLCV candles: each candle's volume is split into buy/sell using
    the close position within the high-low range (a poor man's bulk-volume
    classification, but sufficient for relative toxicity comparisons).
    """

    def __init__(self, config: Optional[dict] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self.buckets: deque[dict] = deque(maxlen=self.cfg["vpin_window_buckets"])
        self._current_bucket: dict = {"buy_vol": 0.0, "sell_vol": 0.0}

    def ingest_candle(self, ohlcv_row: list) -> None:
        """Add a single OHLCV row [ts, o, h, l, c, vol] to the VPIN stream."""
        try:
            o, h, l, c, v = float(ohlcv_row[1]), float(ohlcv_row[2]), float(ohlcv_row[3]), float(ohlcv_row[4]), float(ohlcv_row[5])
        except (IndexError, ValueError, TypeError):
            return
        if v <= 0 or h <= l:
            return
        # Bulk-volume classification: fraction of volume classified as "buy"
        # is the close position within the high-low range (0..1).
        buy_frac = (c - l) / (h - l) if (h - l) > 0 else 0.5
        buy_frac = max(0.0, min(1.0, buy_frac))
        # Convert volume to USD notional (use close as proxy)
        vol_usd = v * c
        buy_usd = vol_usd * buy_frac
        sell_usd = vol_usd * (1.0 - buy_frac)
        bucket_size = self.cfg["vpin_bucket_size_usd"]
        # Add to current bucket
        self._current_bucket["buy_vol"] += buy_usd
        self._current_bucket["sell_vol"] += sell_usd
        # If bucket is full, push to history and start new one
        total = self._current_bucket["buy_vol"] + self._current_bucket["sell_vol"]
        if total >= bucket_size:
            self.buckets.append(dict(self._current_bucket))
            self._current_bucket = {"buy_vol": 0.0, "sell_vol": 0.0}

    def compute(self) -> float:
        """Return current VPIN estimate (0..1)."""
        if not self.buckets:
            return 0.0
        total_imbalance = 0.0
        for b in self.buckets:
            total_imbalance += abs(b["buy_vol"] - b["sell_vol"])
        total_vol = sum(b["buy_vol"] + b["sell_vol"] for b in self.buckets)
        if total_vol <= 0:
            return 0.0
        return total_imbalance / total_vol

    def stats(self) -> dict:
        return {
            "buckets_in_memory": len(self.buckets),
            "current_bucket_usd": self._current_bucket["buy_vol"] + self._current_bucket["sell_vol"],
            "vpin": self.compute(),
        }


# -------------------- OFI estimator --------------------

class OFIEstimator:
    """Order Flow Imbalance (Cont et al. 2014) — top-of-book version.

    Tracks rolling ΔOFI = Σ(Δbid_size) - Σ(Δask_size), normalized by
    average top-of-book depth to produce a -1..+1 score.
    """

    def __init__(self, config: Optional[dict] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self.tick_history: deque[float] = deque(maxlen=self.cfg["ofi_window_ticks"])
        self._last_bid_size: float = 0.0
        self._last_ask_size: float = 0.0
        self._cum_ofi: float = 0.0

    def ingest_top_of_book(self, bid_size: float, ask_size: float) -> None:
        """Add a top-of-book snapshot."""
        if self._last_bid_size > 0 or self._last_ask_size > 0:
            delta_bid = bid_size - self._last_bid_size
            delta_ask = ask_size - self._last_ask_size
            ofi_tick = delta_bid - delta_ask
            self._cum_ofi += ofi_tick
            self.tick_history.append(ofi_tick)
        self._last_bid_size = bid_size
        self._last_ask_size = ask_size

    def compute(self) -> float:
        """Return normalized OFI in [-1, +1].

        Negative = aggressive sellers (toxic for LONG entries).
        Positive = aggressive buyers (toxic for SHORT entries).
        """
        if not self.tick_history:
            return 0.0
        recent = list(self.tick_history)
        total = sum(abs(t) for t in recent) + 1e-9
        signed = sum(recent)
        return max(-1.0, min(1.0, signed / total))

    def stats(self) -> dict:
        return {
            "ticks_in_memory": len(self.tick_history),
            "cum_ofi": self._cum_ofi,
            "ofi_normalized": self.compute(),
        }


# -------------------- Liquidation cluster detector --------------------

class LiquidationClusterDetector:
    """Detects proximity to psychological price levels (round numbers)."""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self._recent_high_volume_nodes: list[tuple[float, float]] = []  # [(price, vol), ...]

    def ingest_high_volume_node(self, price: float, volume: float) -> None:
        """Add a recent high-volume candle as a "node" (called from coordinator)."""
        self._recent_high_volume_nodes.append((price, volume))
        # Keep last 20 nodes
        if len(self._recent_high_volume_nodes) > 20:
            self._recent_high_volume_nodes = self._recent_high_volume_nodes[-20:]

    def distance_to_nearest_cluster(self, current_price: float) -> tuple[float, float]:
        """Return (distance_pct, cluster_price) for nearest cluster.

        Considers both psychological round numbers AND recent high-volume nodes.
        Returns (1.0, current_price) if nothing within 5%.
        """
        if current_price <= 0:
            return 1.0, current_price
        candidates = []
        # Psychological round numbers
        for size in self.cfg["liq_cluster_round_sizes"]:
            nearest_round = round(current_price / size) * size
            if nearest_round > 0:
                candidates.append(nearest_round)
        # Recent high-volume nodes
        for price, _vol in self._recent_high_volume_nodes:
            if price > 0:
                candidates.append(price)
        # Find nearest
        best_dist_pct = 1.0
        best_cluster = current_price
        for c in candidates:
            if c <= 0:
                continue
            dist_pct = abs(c - current_price) / current_price
            if dist_pct < best_dist_pct:
                best_dist_pct = dist_pct
                best_cluster = c
        return best_dist_pct, best_cluster

    def is_near_cluster(self, current_price: float) -> tuple[bool, float, float]:
        """Return (is_near, distance_pct, cluster_price)."""
        dist_pct, cluster = self.distance_to_nearest_cluster(current_price)
        return (dist_pct <= self.cfg["liq_cluster_pct_threshold"], dist_pct, cluster)


# -------------------- Main module --------------------

class MicrostructureIntelligence:
    """Composite microstructure observer — produces toxic_score for RiskKernel.

    Usage:
        mi = MicrostructureIntelligence()
        # On each prediction cycle (additive — does NOT modify prediction):
        mi.ingest_ohlcv(ohlcv_rows)
        mi.ingest_top_of_book(bid_size, ask_size)
        mi.ingest_funding_oi(funding_rate, oi_change_pct)
        report = mi.evaluate(current_price=1700.0, direction="LONG")
        # report.toxic_score, report.action ("ALLOW"/"REDUCE"/"REJECT"), report.size_scale
    """

    def __init__(self, config: Optional[dict] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self.vpin = VPINEstimator(self.cfg)
        self.ofi = OFIEstimator(self.cfg)
        self.liq = LiquidationClusterDetector(self.cfg)
        self._last_funding_bps: float = 0.0
        self._last_oi_change_pct: float = 0.0
        log.info(
            "MicrostructureIntelligence init: vpin_w=%.2f ofi_w=%.2f liq_w=%.2f fund_w=%.2f",
            self.cfg["weight_vpin"], self.cfg["weight_ofi"],
            self.cfg["weight_liquidation"], self.cfg["weight_funding_oi"],
        )

    # -------- ingestion (called from coordinator) --------

    def ingest_ohlcv(self, ohlcv_rows: list[list]) -> None:
        """Push recent OHLCV rows to VPIN."""
        for row in ohlcv_rows[-10:]:  # last 10 candles is plenty per cycle
            try:
                self.vpin.ingest_candle(row)
            except Exception as e:
                log.debug("vpin ingest failed: %s", e)
        # Mark highest-volume candle as a high-volume node
        try:
            if ohlcv_rows:
                # Find the highest-volume candle in the last 30
                recent = ohlcv_rows[-30:]
                hv_row = max(recent, key=lambda r: r[5] if len(r) > 5 else 0)
                if len(hv_row) >= 6 and hv_row[4] > 0:
                    self.liq.ingest_high_volume_node(float(hv_row[4]), float(hv_row[5]))
        except Exception:
            pass

    def ingest_top_of_book(self, bid_size: float, ask_size: float) -> None:
        self.ofi.ingest_top_of_book(bid_size, ask_size)

    def ingest_funding_oi(self, funding_rate: float, oi_change_24h_pct: float) -> None:
        # funding_rate is typically a fraction (0.0001 = 1 bps); convert to bps
        self._last_funding_bps = funding_rate * 10_000 if abs(funding_rate) < 1 else funding_rate
        self._last_oi_change_pct = oi_change_24h_pct

    # -------- evaluation --------

    def evaluate(
        self,
        current_price: float,
        direction: str = "LONG",
    ) -> MicrostructureReport:
        """Compute composite toxic_score + recommended action."""
        vpin_score = self.vpin.compute()
        ofi_norm = self.ofi.compute()
        # OFI toxicity: high |ofi_norm| is toxic regardless of direction
        ofi_toxic = abs(ofi_norm) > self.cfg["ofi_toxic_threshold"]
        # VPIN toxicity: scalar in 0..1, scale up linearly above the threshold
        vpin_toxic_score = max(0.0, (vpin_score - 0.10) / 0.50) if vpin_score > 0.10 else 0.0
        vpin_toxic_score = min(1.0, vpin_toxic_score)
        # Liquidation cluster
        near_cluster, dist_pct, _cluster_price = self.liq.is_near_cluster(current_price)
        liq_score = max(0.0, 1.0 - dist_pct / self.cfg["liq_cluster_pct_threshold"]) if near_cluster else 0.0
        # Funding / OI
        funding_extreme = abs(self._last_funding_bps) > self.cfg["funding_extreme_bps"]
        oi_extreme = abs(self._last_oi_change_pct) > self.cfg["oi_growth_extreme_pct"]
        fund_oi_score = 0.0
        if funding_extreme and oi_extreme:
            fund_oi_score = 1.0  # both → maximum toxicity
        elif funding_extreme or oi_extreme:
            fund_oi_score = 0.5

        # Composite
        toxic = (
            self.cfg["weight_vpin"] * vpin_toxic_score
            + self.cfg["weight_ofi"] * (1.0 if ofi_toxic else 0.0)
            + self.cfg["weight_liquidation"] * liq_score
            + self.cfg["weight_funding_oi"] * fund_oi_score
        )
        toxic = max(0.0, min(1.0, toxic))

        # Action
        if toxic >= self.cfg["reject_threshold"]:
            action = "REJECT"
            size_scale = 0.0
        elif toxic >= self.cfg["reduce_threshold"]:
            action = "REDUCE"
            size_scale = self.cfg["reduce_size_scale"]
        else:
            action = "ALLOW"
            size_scale = 1.0

        return MicrostructureReport(
            toxic_score=round(toxic, 4),
            vpin=round(vpin_score, 4),
            ofi_normalized=round(ofi_norm, 4),
            ofi_toxic=ofi_toxic,
            near_liquidation_cluster=near_cluster,
            distance_to_cluster_pct=round(dist_pct, 4),
            funding_extreme=funding_extreme,
            funding_bps=round(self._last_funding_bps, 2),
            oi_extreme=oi_extreme,
            oi_change_24h_pct=round(self._last_oi_change_pct, 2),
            components={
                "vpin_toxic_score": round(vpin_toxic_score, 4),
                "ofi_toxic": ofi_toxic,
                "liquidation_score": round(liq_score, 4),
                "funding_oi_score": round(fund_oi_score, 4),
            },
            action=action,
            size_scale=size_scale,
            ts=datetime.now(timezone.utc).isoformat(),
        )

    # -------- introspection --------

    def stats(self) -> dict[str, Any]:
        return {
            "vpin": self.vpin.stats(),
            "ofi": self.ofi.stats(),
            "funding_bps": self._last_funding_bps,
            "oi_change_24h_pct": self._last_oi_change_pct,
            "thresholds": {
                "reject": self.cfg["reject_threshold"],
                "reduce": self.cfg["reduce_threshold"],
            },
        }
