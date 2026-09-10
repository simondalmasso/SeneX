"""
SENECIO ORACLE — Layer 2B: Trend Join Long Scanner (Scanner B)
===============================================================
Breakout scanner that fires when a candidate exhibits all of:
  1. current price > yesterday's high
  2. previous close > SMA(200)
  3. current price > premarket high
  4. current price > today's high (intraday momentum)

Input: market ticks + cached OHLC + SMA cache.
Output: MARKET_CANDIDATE events of type "B_trend_join_long".
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Iterable

from .models import MarketCandidate, utc_now_iso


@dataclass
class ScannerBConfig:
    sma_window: int = 200
    min_volume_today: float = 100_000
    max_candidates_per_cycle: int = 5


@dataclass
class ScannerB:
    cfg: ScannerBConfig = field(default_factory=ScannerBConfig)
    # rolling OHLC history per symbol (in-memory; replace with audit replay)
    history: dict[str, list[dict]] = field(default_factory=dict)
    sma_cache: dict[str, float] = field(default_factory=dict)
    today_high: dict[str, float] = field(default_factory=dict)
    yesterday_high: dict[str, float] = field(default_factory=dict)
    premarket_high: dict[str, float] = field(default_factory=dict)
    prev_close: dict[str, float] = field(default_factory=dict)

    def update_history(self, sym: str, ohlc: dict) -> None:
        self.history.setdefault(sym, []).append(ohlc)
        # keep last N+50 for SMA computation
        if len(self.history[sym]) > self.cfg.sma_window + 50:
            self.history[sym] = self.history[sym][-(self.cfg.sma_window + 50):]
        # recompute SMA
        if len(self.history[sym]) >= self.cfg.sma_window:
            closes = [bar["c"] for bar in self.history[sym][-self.cfg.sma_window:]]
            self.sma_cache[sym] = statistics.mean(closes)

    def set_day_context(self, sym: str, prev_close: float, yesterday_high: float, premarket_high: float) -> None:
        self.prev_close[sym] = prev_close
        self.yesterday_high[sym] = yesterday_high
        self.premarket_high[sym] = premarket_high
        self.today_high[sym] = 0.0

    def scan(self, sym: str, price: float, volume_today: float) -> MarketCandidate | None:
        # update today's high
        if price > self.today_high.get(sym, 0):
            self.today_high[sym] = price

        sma = self.sma_cache.get(sym)
        pc = self.prev_close.get(sym)
        yh = self.yesterday_high.get(sym)
        pmh = self.premarket_high.get(sym)
        th = self.today_high.get(sym, price)

        if None in (sma, pc, yh, pmh):
            return None
        if volume_today < self.cfg.min_volume_today:
            return None

        checks = {
            "price_gt_yesterday_high": price > yh,
            "prev_close_gt_sma200": pc > sma,
            "price_gt_premarket_high": price > pmh,
            "price_gt_today_high_momentum": price >= th,  # current tick = new high
        }
        if not all(checks.values()):
            return None

        score = (
            ((price - yh) / yh * 100) * 1.5
            + ((pc - sma) / sma * 100) * 1.0
            + min(volume_today / 1_000_000, 5) * 5
        )
        return MarketCandidate(
            source="scanner_b",
            symbol=sym,
            trace_id=f"scB-{sym}",
            payload={
                "scanner": "B_trend_join_long",
                "score": round(score, 2),
                "price": price,
                "checks": checks,
                "sma200": round(sma, 4),
                "yesterday_high": round(yh, 4),
                "premarket_high": round(pmh, 4),
                "today_high": round(th, 4),
                "volume_today": volume_today,
                "reasons": [
                    "Price > yesterday high",
                    "Prev close > SMA(200)",
                    "Price > premarket high",
                    "Price = new intraday high",
                ],
                "metrics": {"score": score, "sma200": sma},
            },
        )
