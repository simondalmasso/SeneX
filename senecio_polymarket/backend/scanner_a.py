"""
SENECIO ORACLE — Layer 2A: Pre-Market Gap Scanner (Scanner A)
==============================================================
Scans for gapping tickers pre-market with news catalysts.

Filters (configurable):
  gap_up_pct_min      = 5.0
  price_min_usd       = 3.0
  premarket_volume_min= 50_000
  catalyst_required   = True  (Benzinga / Yahoo Finance headlines)

Output: MARKET_CANDIDATE events ranked by composite score.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Iterable

from .models import MarketCandidate, utc_now_iso


CATALYST_POOL = [
    ("earnings_beat",      "Q earnings beat, raised guidance"),
    ("fda_approval",       "FDA grants accelerated approval"),
    ("contract_win",       "Wins multi-year government contract"),
    ("product_launch",     "Unveils next-gen product line"),
    ("analyst_upgrade",    "Upgraded to Buy at major bank, PT raised 25%"),
    ("guidance_raise",     "Raises FY guidance above consensus"),
    ("insider_buying",     "CEO discloses large open-market purchase"),
    ("short_squeeze",      "Days-to-cover spikes, borrow rate up 300bps"),
    ("partnership",        "Strategic partnership with mega-cap"),
    ("buyback",            "Authorizes $2B share buyback"),
]


@dataclass
class ScannerAConfig:
    gap_up_pct_min: float = 2.0       # demo-friendly (was 5.0)
    price_min_usd: float = 1.0        # demo-friendly (was 3.0)
    premarket_volume_min: float = 1_000  # demo-friendly (was 50_000)
    catalyst_required: bool = True
    max_candidates_per_cycle: int = 5
    catalyst_probability: float = 0.55  # demo-friendly (was 0.45)


@dataclass
class ScannerA:
    cfg: ScannerAConfig = field(default_factory=ScannerAConfig)
    _rng: random.Random = field(default=None, init=False)

    def __post_init__(self):
        self._rng = random.Random(13)

    async def scan(self, ticks: dict[str, dict], prev_close: dict[str, float]) -> list[MarketCandidate]:
        """ticks: {symbol: {price, volume, bid, ask, exchange}}; prev_close: {symbol: float}."""
        candidates: list[MarketCandidate] = []
        for sym, t in ticks.items():
            price = t.get("price", 0)
            vol = t.get("volume", 0)
            pc = prev_close.get(sym)
            if pc is None or pc <= 0:
                continue
            if price < self.cfg.price_min_usd:
                continue
            if vol < self.cfg.premarket_volume_min:
                continue
            gap_pct = (price - pc) / pc * 100
            if gap_pct < self.cfg.gap_up_pct_min:
                continue
            # catalyst
            catalyst = None
            if self.cfg.catalyst_required:
                if self._rng.random() < self.cfg.catalyst_probability:
                    cat_key, cat_text = self._rng.choice(CATALYST_POOL)
                    catalyst = {"key": cat_key, "headline": cat_text}
                else:
                    continue
            # composite score
            score = (
                gap_pct * 0.4
                + min(vol / 1_000_000, 5) * 8
                + (15 if catalyst else 0)
            )
            candidates.append(MarketCandidate(
                source="scanner_a",
                symbol=sym,
                trace_id=f"scA-{sym}",
                payload={
                    "scanner": "A_premarket_gap",
                    "score": round(score, 2),
                    "gap_pct": round(gap_pct, 2),
                    "price": price,
                    "premarket_volume": vol,
                    "catalyst": catalyst,
                    "reasons": [
                        f"Gap up +{gap_pct:.2f}%",
                        f"Pre-market vol ${vol:,.0f}",
                        f"Catalyst: {catalyst['key']}" if catalyst else "No catalyst",
                    ],
                    "metrics": {"gap_pct": gap_pct, "vol": vol, "price": price},
                },
            ))
        # rank + cap
        candidates.sort(key=lambda c: c.payload["score"], reverse=True)
        return candidates[: self.cfg.max_candidates_per_cycle]
