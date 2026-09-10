"""
SENECIO ORACLE — ACT XXVI: Execution Fidelity (priority 1)
==========================================================

HftBacktest-inspired fill simulator that replaces the toy stochastic
slippage model in ExecutionEngine with a realistic L1/L2 microstructure
walk + queue-position accounting.

WHY THIS MODULE EXISTS
-----------------------
The ACT-XXV ExecutionEngine used `random.uniform(base, base+rng)` bps
slippage + `book_depth / notional` partial-fill ratio. That model has
three systematic biases vs. a real exchange:

  1. **No adverse selection** — fills always happen "soon enough" with
     uniform slippage, regardless of order sign. In reality, marketable
     BUY orders during toxic-flow regimes pay ~2-3× the spread because
     makers pull quotes.
  2. **No queue position** — a passive LIMIT order earns the maker fee
     only if it reaches the front of the queue before price moves away.
     The old model assumed immediate fill at the limit price.
  3. **No book walk** — large orders eat multiple levels of the L2 book,
     producing VWAP slippage that scales with sqrt(order_size / top_level).
     The old model linearly capped fills at 100% of book depth.

WHAT THIS MODULE ADDS (additive — does NOT modify ExecutionEngine)
-------------------------------------------------------------------
  - `BookSnapshot`           : L2 (top-N levels) + last-trade price
  - `QueuePositionModel`     : estimates the # of contracts ahead of us
                                at the same price level + fill probability
  - `walk_book()`            : consumes L2 levels sequentially, returns
                                VWAP fill price + cumulative qty
  - `estimate_market_impact()`: Almgren-Chriss square-root model
  - `simulate_fill()`        : end-to-end: order + book → Fill event
                                with realistic slippage/latency/fees

INTEGRATION
-----------
ExecutionEngine stays unchanged. The coordinator calls `simulate_fill()`
*before* the existing `_try_fill()` so we get a high-fidelity expected
fill, then the legacy code records the same Fill (with our numbers) into
the audit stream. ShadowLive then compares this expected fill against
the real orderbook snapshot — exactly what ACT-XXVI asks for.

If `BookSnapshot` is None (no L2 available), we fall back to the legacy
stochastic model — graceful degradation.

NO LIVE TRADING — paper mode only. allow_live stays False.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("senecio.execution_fidelity")


# -------------------- config --------------------

DEFAULTS: dict[str, Any] = {
    # Square-root market impact (Almgren-Chriss form)
    "impact_coeff":              0.10,    # k in: impact_bps = k * sqrt(qty / adv)
    "adv_assumed_usd":           50_000_000.0,  # assumed daily volume if unknown
    # Queue position model
    "queue_decay_per_sec":       0.15,    # 15% of queue drains per second (typical crypto)
    "queue_arrival_rate_per_s":  5.0,     # new orders ahead of us arriving per second
    "queue_max_fill_prob":       0.95,    # even at front of queue, 5% chance of cancellation
    # Adverse selection
    "adverse_selection_toxic":   2.5,     # multiplier on slippage when toxic flow detected
    "adverse_selection_normal":  1.0,
    # Latency
    "latency_ms_min":            50,
    "latency_ms_max":            300,
    "latency_ms_quote_decay":    8,       # ms of "quote decay" per simulated fill round
    # Fees
    "taker_fee_bps":             5.0,
    "maker_fee_bps":             2.0,
    # Book walk
    "max_levels_to_walk":        8,       # don't eat past level 8 (slippage cap)
    # Fallback model (when no book snapshot)
    "fallback_base_slippage_bps": 2.0,
    "fallback_rng_slippage_bps":  4.0,
}


# -------------------- data classes --------------------

@dataclass
class BookLevel:
    """One price level of the L2 order book."""
    price: float
    size: float  # in base currency (e.g. ETH)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BookSnapshot:
    """L2 snapshot — top-N bid + ask levels + last trade price."""
    symbol: str
    bids: list[BookLevel] = field(default_factory=list)   # best → worst (descending price)
    asks: list[BookLevel] = field(default_factory=list)   # best → worst (ascending price)
    last_trade_price: float = 0.0
    ts: str = ""
    # Optional context (used by adverse-selection model)
    toxic_flow_score: float = 0.0  # 0..1, from MicrostructureIntelligence module

    def mid(self) -> float:
        if not self.bids or not self.asks:
            return self.last_trade_price or 0.0
        return (self.bids[0].price + self.asks[0].price) / 2

    def spread_bps(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        b, a = self.bids[0].price, self.asks[0].price
        mid = (b + a) / 2
        if mid <= 0:
            return 0.0
        return (a - b) / mid * 10_000

    def top_level_depth_usd(self, side: str) -> float:
        """Dollar depth at the best bid or ask."""
        levels = self.bids if side == "BID" else self.asks
        if not levels:
            return 0.0
        return levels[0].price * levels[0].size

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bids": [l.to_dict() for l in self.bids[:5]],
            "asks": [l.to_dict() for l in self.asks[:5]],
            "last_trade_price": self.last_trade_price,
            "ts": self.ts,
            "toxic_flow_score": self.toxic_flow_score,
            "mid": self.mid(),
            "spread_bps": self.spread_bps(),
        }


@dataclass
class FillEstimate:
    """Output of simulate_fill() — what we expect to happen on a real exchange."""
    expected_qty: float
    expected_vwap_price: float
    expected_slippage_bps: float
    expected_market_impact_bps: float
    expected_fee_usd: float
    expected_fee_bps: float
    expected_latency_ms: int
    is_partial: bool
    queue_position: int
    queue_fill_prob: float
    levels_consumed: int
    adverse_selection_mult: float
    book_present: bool
    model: str  # "l2_walk" | "queue_almgren_chriss" | "fallback_stochastic"
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -------------------- models --------------------

class QueuePositionModel:
    """Estimate fill probability for a passive (maker) limit order.

    The model: at submission time we land at the BACK of the queue at our
    price level. Queue drains at `queue_decay_per_sec` per second; new
    orders arrive ahead of us at `queue_arrival_rate_per_s` per second.

    The probability of being filled within `t` seconds is:

        P(fill, t) = max_fill_prob * (1 - exp(-(decay - arrival) * t))
                     if decay > arrival, else a small floor.

    For marketable (taker) orders this returns 1.0 — they cross immediately.
    """

    def __init__(self, config: Optional[dict] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self._rng = random.Random(42)

    def estimate(
        self,
        side: str,                     # "BUY" | "SELL"
        is_marketable: bool,           # True if limit crosses spread
        queue_ahead_contracts: float,  # contracts at our price level ahead of us
        seconds_at_front: float,       # how long we'd wait before canceling
    ) -> tuple[int, float]:
        """Return (queue_position_int, fill_probability)."""
        if is_marketable:
            # Taker — crosses immediately, no queue
            return 0, 1.0
        decay = self.cfg["queue_decay_per_sec"]
        arrival = self.cfg["queue_arrival_rate_per_s"]
        net = decay - arrival
        max_p = self.cfg["queue_max_fill_prob"]
        if net <= 0:
            # Queue is growing faster than draining — pessimistic
            p = 0.10
        else:
            p = max_p * (1.0 - math.exp(-net * max(0.1, seconds_at_front)))
        # Stochastic adjustment based on actual queue depth
        if queue_ahead_contracts > 0:
            # More orders ahead → less likely to fill
            depth_penalty = min(0.5, queue_ahead_contracts / 1000.0)
            p = max(0.0, p - depth_penalty * 0.5)
        queue_pos = max(0, int(queue_ahead_contracts))
        return queue_pos, max(0.0, min(1.0, p))


def walk_book(
    side: str,
    notional_usd: float,
    book: BookSnapshot,
    max_levels: int = 8,
) -> tuple[float, float, int]:
    """Walk the L2 book consuming `notional_usd` worth of liquidity.

    Returns (vwap_price, filled_qty, levels_consumed).
    """
    levels = book.asks if side == "BUY" else book.bids
    if not levels:
        return 0.0, 0.0, 0
    remaining = notional_usd
    cum_px_times_qty = 0.0
    cum_qty = 0.0
    levels_consumed = 0
    for lvl in levels[:max_levels]:
        if remaining <= 0:
            break
        take_notional = min(remaining, lvl.price * lvl.size)
        take_qty = take_notional / lvl.price if lvl.price > 0 else 0
        cum_px_times_qty += lvl.price * take_qty
        cum_qty += take_qty
        remaining -= take_notional
        levels_consumed += 1
    if cum_qty <= 0:
        return 0.0, 0.0, 0
    vwap = cum_px_times_qty / cum_qty
    return vwap, cum_qty, levels_consumed


def estimate_market_impact(
    order_notional_usd: float,
    adv_usd: float,
    impact_coeff: float = 0.10,
) -> float:
    """Almgren-Chriss square-root impact in bps.

    impact_bps = coeff * sqrt(order_notional / adv) * 10_000

    For a $10k order on $50M ADV: sqrt(10000/50e6) = 0.0141 → 14.1 bps with k=1.0.
    We use k=0.10 by default → ~1.4 bps impact (sane for liquid crypto pairs).
    """
    if adv_usd <= 0 or order_notional_usd <= 0:
        return 0.0
    return impact_coeff * math.sqrt(order_notional_usd / adv_usd) * 10_000


# -------------------- main simulator --------------------

class FillSimulator:
    """High-fidelity fill estimator — replaces stochastic slippage.

    Usage:
        sim = FillSimulator()
        book = fetch_l2_snapshot(symbol)  # BookSnapshot
        estimate = sim.simulate_fill(side="BUY", notional_usd=1000, book=book, is_marketable=True)
        # estimate.expected_vwap_price, expected_slippage_bps, ...
    """

    def __init__(self, config: Optional[dict] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self._rng = random.Random(13)
        self.queue_model = QueuePositionModel(self.cfg)
        log.info(
            "FillSimulator init: impact_coeff=%.2f adv=$%.0f toxic_mult=%.1f",
            self.cfg["impact_coeff"], self.cfg["adv_assumed_usd"],
            self.cfg["adverse_selection_toxic"],
        )

    # -------- public API --------

    def simulate_fill(
        self,
        side: str,                              # "BUY" | "SELL"
        notional_usd: float,                    # target $ size
        book: Optional[BookSnapshot] = None,
        is_marketable: bool = True,             # marketable limit / market order
        adv_usd: Optional[float] = None,
        seconds_at_front: float = 30.0,         # for passive orders
        toxic_flow_score: Optional[float] = None,
    ) -> FillEstimate:
        """Estimate the realistic fill for an order.

        Strategy:
          1. If book is None → fall back to stochastic model (legacy compat).
          2. If marketable (taker) → walk the L2 book + add market impact.
          3. If passive (maker) → use queue-position model.
          4. Apply adverse-selection multiplier if toxic_flow_score high.
          5. Add latency + fees.
        """
        if notional_usd <= 0:
            return self._empty_estimate(side)

        toxic = (
            float(toxic_flow_score)
            if toxic_flow_score is not None
            else (book.toxic_flow_score if book else 0.0)
        )
        adverse_mult = self._adverse_selection_multiplier(toxic)

        # Branch: no book → fallback
        if book is None or (not book.bids and not book.asks):
            return self._fallback_stochastic(side, notional_usd, adverse_mult)

        mid = book.mid()
        if mid <= 0:
            return self._fallback_stochastic(side, notional_usd, adverse_mult)

        adv = adv_usd or self.cfg["adv_assumed_usd"]

        # Latency
        latency_ms = self._rng.randint(
            self.cfg["latency_ms_min"], self.cfg["latency_ms_max"]
        )

        # Marketable (taker) → walk the book
        if is_marketable:
            vwap, qty, levels_consumed = walk_book(
                side=side,
                notional_usd=notional_usd,
                book=book,
                max_levels=self.cfg["max_levels_to_walk"],
            )
            if qty <= 0:
                # Book was empty / notional too small — fall back
                return self._fallback_stochastic(side, notional_usd, adverse_mult)

            # Slippage vs mid
            raw_slip_bps = abs(vwap - mid) / mid * 10_000
            # Add market impact (Almgren-Chriss)
            impact_bps = estimate_market_impact(
                order_notional_usd=notional_usd,
                adv_usd=adv,
                impact_coeff=self.cfg["impact_coeff"],
            )
            slippage_bps = (raw_slip_bps + impact_bps) * adverse_mult
            # Apply fee
            fee_bps = self.cfg["taker_fee_bps"]
            fee_usd = qty * vwap * fee_bps / 10_000
            is_partial = qty * vwap < notional_usd * 0.999
            return FillEstimate(
                expected_qty=round(qty, 8),
                expected_vwap_price=round(vwap, 6),
                expected_slippage_bps=round(slippage_bps, 2),
                expected_market_impact_bps=round(impact_bps, 2),
                expected_fee_usd=round(fee_usd, 4),
                expected_fee_bps=fee_bps,
                expected_latency_ms=latency_ms,
                is_partial=is_partial,
                queue_position=0,
                queue_fill_prob=1.0,
                levels_consumed=levels_consumed,
                adverse_selection_mult=adverse_mult,
                book_present=True,
                model="l2_walk",
                detail={
                    "mid": mid,
                    "raw_slip_bps": round(raw_slip_bps, 2),
                    "notional_requested": notional_usd,
                    "notional_filled": round(qty * vwap, 2),
                },
            )

        # Passive (maker) → queue position model
        # Assume we post at best bid (BUY) or best ask (SELL)
        our_price = book.bids[0].price if side == "BUY" else book.asks[0].price
        # Rough queue: 2× the top-level size in contracts
        top_size = (
            book.bids[0].size if side == "BUY" else book.asks[0].size
        )
        queue_ahead = top_size * 2.0
        queue_pos, fill_prob = self.queue_model.estimate(
            side=side,
            is_marketable=False,
            queue_ahead_contracts=queue_ahead,
            seconds_at_front=seconds_at_front,
        )
        # Did we get filled?
        filled = self._rng.random() < fill_prob
        if not filled:
            return FillEstimate(
                expected_qty=0.0,
                expected_vwap_price=our_price,
                expected_slippage_bps=0.0,  # never filled → no slippage realized
                expected_market_impact_bps=0.0,
                expected_fee_usd=0.0,
                expected_fee_bps=self.cfg["maker_fee_bps"],
                expected_latency_ms=latency_ms,
                is_partial=False,
                queue_position=queue_pos,
                queue_fill_prob=round(fill_prob, 3),
                levels_consumed=0,
                adverse_selection_mult=adverse_mult,
                book_present=True,
                model="queue_almgren_chriss",
                detail={
                    "mid": mid,
                    "our_price": our_price,
                    "filled": False,
                    "queue_ahead_contracts": queue_ahead,
                },
            )
        # Filled as maker → small negative slippage (we earned the spread)
        spread_bps = book.spread_bps()
        # Maker "slippage" is negative (we got the better price); represent as negative
        realized_slip_bps = -spread_bps / 2.0 * adverse_mult
        qty = notional_usd / our_price if our_price > 0 else 0
        fee_bps = self.cfg["maker_fee_bps"]
        fee_usd = qty * our_price * fee_bps / 10_000
        return FillEstimate(
            expected_qty=round(qty, 8),
            expected_vwap_price=round(our_price, 6),
            expected_slippage_bps=round(realized_slip_bps, 2),
            expected_market_impact_bps=0.0,
            expected_fee_usd=round(fee_usd, 4),
            expected_fee_bps=fee_bps,
            expected_latency_ms=latency_ms,
            is_partial=False,
            queue_position=queue_pos,
            queue_fill_prob=round(fill_prob, 3),
            levels_consumed=1,
            adverse_selection_mult=adverse_mult,
            book_present=True,
            model="queue_almgren_chriss",
            detail={
                "mid": mid,
                "our_price": our_price,
                "filled": True,
                "queue_ahead_contracts": queue_ahead,
                "spread_bps": spread_bps,
            },
        )

    # -------- helpers --------

    def _adverse_selection_multiplier(self, toxic_flow_score: float) -> float:
        """Linearly interpolate between normal (1.0) and toxic (2.5×) slippage."""
        toxic_score = max(0.0, min(1.0, toxic_flow_score))
        normal = self.cfg["adverse_selection_normal"]
        toxic = self.cfg["adverse_selection_toxic"]
        return normal + (toxic - normal) * toxic_score

    def _fallback_stochastic(self, side: str, notional_usd: float, adverse_mult: float) -> FillEstimate:
        """Legacy stochastic fill (when no L2 book is available)."""
        slip_bps = self._rng.uniform(
            self.cfg["fallback_base_slippage_bps"],
            self.cfg["fallback_base_slippage_bps"] + self.cfg["fallback_rng_slippage_bps"],
        ) * adverse_mult
        # Use notional / mid-assumed-1.0 if no price; caller should provide a book.
        # This branch is a fallback — production should always have a book.
        mid = 1.0
        if side == "BUY":
            vwap = mid * (1 + slip_bps / 10_000)
        else:
            vwap = mid * (1 - slip_bps / 10_000)
        qty = notional_usd / vwap if vwap > 0 else 0
        fee_bps = self.cfg["taker_fee_bps"]
        fee_usd = qty * vwap * fee_bps / 10_000
        latency_ms = self._rng.randint(
            self.cfg["latency_ms_min"], self.cfg["latency_ms_max"]
        )
        return FillEstimate(
            expected_qty=round(qty, 8),
            expected_vwap_price=round(vwap, 6),
            expected_slippage_bps=round(slip_bps, 2),
            expected_market_impact_bps=0.0,
            expected_fee_usd=round(fee_usd, 4),
            expected_fee_bps=fee_bps,
            expected_latency_ms=latency_ms,
            is_partial=False,
            queue_position=0,
            queue_fill_prob=1.0,
            levels_consumed=1,
            adverse_selection_mult=adverse_mult,
            book_present=False,
            model="fallback_stochastic",
            detail={"mid": mid, "warning": "no L2 book — fallback stochastic used"},
        )

    @staticmethod
    def _empty_estimate(side: str) -> FillEstimate:
        return FillEstimate(
            expected_qty=0.0,
            expected_vwap_price=0.0,
            expected_slippage_bps=0.0,
            expected_market_impact_bps=0.0,
            expected_fee_usd=0.0,
            expected_fee_bps=0.0,
            expected_latency_ms=0,
            is_partial=False,
            queue_position=0,
            queue_fill_prob=0.0,
            levels_consumed=0,
            adverse_selection_mult=1.0,
            book_present=False,
            model="empty",
            detail={"side": side, "reason": "notional <= 0"},
        )

    def stats(self) -> dict[str, Any]:
        return {
            "model": "FillSimulator",
            "config": dict(self.cfg),
            "queue_model": "QueuePositionModel",
        }


# -------------------- helpers for callers --------------------

def book_snapshot_from_dict(d: dict) -> BookSnapshot:
    """Build a BookSnapshot from a generic orderbook dict.

    Expected shape (compatible with liquidity.Orderbook):
        {
            "bids": [[price, size], ...],
            "asks": [[price, size], ...],
            "last_price": float,
            "ts": str,
            "toxic_flow_score": float,  # optional
            "symbol": str,
        }
    """
    bids = [BookLevel(price=float(p), size=float(s)) for p, s in (d.get("bids") or [])]
    asks = [BookLevel(price=float(p), size=float(s)) for p, s in (d.get("asks") or [])]
    return BookSnapshot(
        symbol=d.get("symbol", ""),
        bids=bids,
        asks=asks,
        last_trade_price=float(d.get("last_price") or d.get("last") or 0.0),
        ts=d.get("ts") or datetime.now(timezone.utc).isoformat(),
        toxic_flow_score=float(d.get("toxic_flow_score") or 0.0),
    )
