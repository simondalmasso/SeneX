"""
SENECIO ORACLE — Layer 1: Incremental Data Retriever
=====================================================
Polymarket-style data layer (poly_data-inspired).

Modes:
- HISTORICAL: backfill raw market ticks + wallet activity from local/synthetic source
- LIVE: incremental updates with resume cursors

Sources (mocked for paper-only operation):
- CLOB-like synthetic feed (deterministic seed → reproducible)
- On-chain wallet activity (synthetic whale patterns)
- Market metadata (static catalog)

In production, replace `synthetic_*` calls with real adapters:
- Polymarket real-time-data-client (CLOB + gamma API)
- Etherscan / Alchemy (on-chain)
- yfinance / ccxt (price feeds)
"""
from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator

from .models import MarketTick, WalletAlert, utc_now_iso, new_id


# ---- static catalog ----
CATALOG = [
    {"symbol": "MU",     "name": "Micron Technology",   "sector": "semis",      "base_price": 110.0},
    {"symbol": "NVDA",   "name": "NVIDIA",              "sector": "semis",      "base_price": 880.0},
    {"symbol": "AMD",    "name": "Advanced Micro Dev",  "sector": "semis",      "base_price": 165.0},
    {"symbol": "AAPL",   "name": "Apple",               "sector": "tech",       "base_price": 195.0},
    {"symbol": "TSLA",   "name": "Tesla",               "sector": "ev",         "base_price": 250.0},
    {"symbol": "PLTR",   "name": "Palantir",            "sector": "ai-data",    "base_price": 28.0},
    {"symbol": "SOFI",   "name": "SoFi Technologies",   "sector": "fintech",    "base_price": 8.5},
    {"symbol": "COIN",   "name": "Coinbase",            "sector": "crypto",     "base_price": 245.0},
    {"symbol": "MARA",   "name": "Marathon Digital",    "sector": "crypto-min", "base_price": 18.0},
    {"symbol": "GME",    "name": "GameStop",            "sector": "meme",       "base_price": 14.0},
    {"symbol": "AMC",    "name": "AMC Entertainment",   "sector": "meme",       "base_price": 4.5},
    {"symbol": "BBBYQ",  "name": "Bed Bath & Beyond",   "sector": "meme",       "base_price": 0.25},
    {"symbol": "NVDA",   "name": "NVIDIA",              "sector": "semis",      "base_price": 880.0},
    {"symbol": "META",   "name": "Meta Platforms",      "sector": "tech",       "base_price": 495.0},
    {"symbol": "MSFT",   "name": "Microsoft",           "sector": "tech",       "base_price": 420.0},
    {"symbol": "GOOGL",  "name": "Alphabet",            "sector": "tech",       "base_price": 165.0},
]


@dataclass
class Cursor:
    source: str
    offset: int = 0
    last_ts: str | None = None
    digest: str = ""

    def advance(self, n: int, ts: str) -> None:
        self.offset += n
        self.last_ts = ts
        self.digest = hashlib.sha1(f"{self.source}:{self.offset}:{ts}".encode()).hexdigest()[:10]


@dataclass
class DataRetriever:
    mode: str = "LIVE"  # LIVE | HISTORICAL
    seed: int = 42
    cursors: dict[str, Cursor] = field(default_factory=lambda: {
        "clob": Cursor(source="clob"),
        "onchain": Cursor(source="onchain"),
        "metadata": Cursor(source="metadata"),
    })
    _rng: random.Random = field(default=None, init=False)

    def __post_init__(self):
        self._rng = random.Random(self.seed)

    # ---- public API ----
    async def stream_ticks(self, interval_s: float = 1.0) -> AsyncIterator[MarketTick]:
        """Endless stream of synthetic MarketTick events for all symbols."""
        cursor = self.cursors["clob"]
        # initialize per-symbol price state
        state: dict[str, dict] = {}
        for item in CATALOG:
            state[item["symbol"]] = {
                "price": item["base_price"],
                "volume": 0.0,
                "volatility": 0.012 + self._rng.random() * 0.015,
            }
        while True:
            for item in CATALOG:
                sym = item["symbol"]
                s = state[sym]
                # random-walk with mean reversion
                drift = (item["base_price"] - s["price"]) * 0.005
                shock = self._rng.gauss(0, s["volatility"])
                s["price"] = max(0.01, s["price"] * (1 + drift + shock))
                s["volume"] += abs(shock) * 10000 + self._rng.uniform(500, 5000)
                bid = s["price"] * (1 - self._rng.uniform(0.0002, 0.0008))
                ask = s["price"] * (1 + self._rng.uniform(0.0002, 0.0008))
                cursor.advance(1, utc_now_iso())
                yield MarketTick(
                    source="clob_synthetic",
                    symbol=sym,
                    trace_id=f"clob-{cursor.digest}",
                    payload={
                        "price": round(s["price"], 4),
                        "volume": round(s["volume"], 2),
                        "bid": round(bid, 4),
                        "ask": round(ask, 4),
                        "ts_exchange": utc_now_iso(),
                        "exchange": "SYNTH",
                        "cursor_offset": cursor.offset,
                    },
                )
            await asyncio.sleep(interval_s)

    async def stream_wallet_alerts(self, interval_s: float = 3.0) -> AsyncIterator[WalletAlert]:
        """Sparse stream of whale wallet actions."""
        cursor = self.cursors["onchain"]
        wallets = [
            ("0xwhale1", "smart_money"),
            ("0xwhale2", "smart_money"),
            ("0xwhale3", "retail_whale"),
            ("0xmev4",   "mev_bot"),
            ("0xvc5",    "vc_wallet"),
        ]
        while True:
            # 40% chance to emit per cycle
            if self._rng.random() < 0.4:
                w, label = self._rng.choice(wallets)
                sym = self._rng.choice(CATALOG)["symbol"]
                action = self._rng.choice(["BUY", "SELL", "ACCUMULATE", "DISTRIBUTE"])
                size_usd = round(self._rng.uniform(50_000, 2_500_000), 2)
                cursor.advance(1, utc_now_iso())
                yield WalletAlert(
                    source="onchain_synthetic",
                    symbol=sym,
                    trace_id=f"oc-{cursor.digest}",
                    payload={
                        "wallet": w,
                        "label": label,
                        "action": action,
                        "size_usd": size_usd,
                        "token": sym,
                        "tx_hash": f"0x{self._rng.randbytes(16).hex()}",
                        "block_number": 18_500_000 + cursor.offset,
                    },
                )
            await asyncio.sleep(interval_s)

    def catalog(self) -> list[dict]:
        return CATALOG.copy()

    def cursor_state(self) -> dict[str, dict]:
        return {k: {"offset": v.offset, "last_ts": v.last_ts, "digest": v.digest} for k, v in self.cursors.items()}
