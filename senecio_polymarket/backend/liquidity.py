"""
SENECIO ORACLE — Layer 2D: Liquidity Layer (poly-maker-inspired)
================================================================
Maintains per-symbol orderbook depth view + computes:
- spread (bps)
- mid-price
- liquidity score (depth-weighted)
- slippage estimate for a target notional
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import MarketTick


@dataclass
class BookSide:
    """Price/size pairs ordered best-first."""
    levels: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Orderbook:
    bids: BookSide = field(default_factory=BookSide)
    asks: BookSide = field(default_factory=BookSide)
    ts: str = ""

    def mid(self) -> float:
        if not self.bids.levels or not self.asks.levels:
            return 0.0
        return (self.bids.levels[0][0] + self.asks.levels[0][0]) / 2

    def spread_bps(self) -> float:
        if not self.bids.levels or not self.asks.levels:
            return 0.0
        b, a = self.bids.levels[0][0], self.asks.levels[0][0]
        mid = (b + a) / 2
        if mid <= 0:
            return 0.0
        return (a - b) / mid * 10_000

    def depth_notional(self, levels: int = 5) -> float:
        bid_depth = sum(p * s for p, s in self.bids.levels[:levels])
        ask_depth = sum(p * s for p, s in self.asks.levels[:levels])
        return bid_depth + ask_depth

    def liquidity_score(self, levels: int = 5) -> float:
        """0..100 score. Higher = more liquid."""
        d = self.depth_notional(levels)
        return round(min(100, math.log10(max(1, d)) * 12.5), 2)

    def slippage_bps(self, side: str, notional_usd: float, levels: int = 5) -> float:
        """Estimate slippage in bps for eating into the book."""
        book = self.asks if side == "BUY" else self.bids
        if not book.levels:
            return 0.0
        remaining = notional_usd
        avg_px = 0.0
        total_qty = 0.0
        for px, sz in book.levels[:levels]:
            take_qty = min(sz, remaining / px) if px > 0 else 0
            avg_px += px * take_qty
            total_qty += take_qty
            remaining -= take_qty * px
            if remaining <= 0:
                break
        if total_qty == 0:
            return 999.0
        avg_px /= total_qty
        best = book.levels[0][0]
        if best <= 0:
            return 0.0
        return abs(avg_px - best) / best * 10_000


def synth_book_from_tick(t: MarketTick, n_levels: int = 6) -> Orderbook:
    """Synthesize a plausible orderbook around the tick price."""
    px = t.payload.get("price", 0)
    vol = t.payload.get("volume", 0)
    if px <= 0:
        return Orderbook()
    spread_frac = 0.0008
    bid_levels = []
    ask_levels = []
    base_size = max(100, vol / 5000)
    for i in range(n_levels):
        depth_factor = (i + 1) ** 1.4
        bid_levels.append((round(px * (1 - spread_frac / 2 - 0.0005 * i), 4), round(base_size * depth_factor, 2)))
        ask_levels.append((round(px * (1 + spread_frac / 2 + 0.0005 * i), 4), round(base_size * depth_factor * 0.9, 2)))
    return Orderbook(bids=BookSide(levels=bid_levels), asks=BookSide(levels=ask_levels), ts=t.ts)
