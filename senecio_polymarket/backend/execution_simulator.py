"""
SENECIO ORACLE — Layer 4: Execution Simulator (B8.1 realistic paper execution)
===============================================================================
Paper-only execution engine. Models:
- Fill simulation (marketable limit at mid + slippage, conservative depth walk)
- Latency (random 50-300ms)
- Partial fills bounded by OBSERVABLE book depth (no optimistic floor)
- Fees (taker fee bps per leg, conservative default 10 bps)
- LONG and SHORT positions with stop/target/time-stop exits
- Stateful hypothetical bankroll accounting (cash, fees, drawdown, return %)

NO REAL ORDERS ARE PLACED. ``allow_real`` is structurally refused by the
B8.1 HARD PAPER LOCK (backend/paper_lock.py): constructing or mutating this
simulator with allow_real=True raises immediately. Real execution would
require a separate broker adapter which this candidate does not wire.

Conservative assumptions, stated explicitly:
- Queue position is NOT modeled (unobservable in public data): fills are
  approximated as immediate marketable-limit executions against available
  depth. ``depth_source`` states whether depth was observed or assumed.
- When no order book is supplied, an explicit bounded assumed depth is used
  (``assumed_depth_usd``, default $5,000) and labeled in every payload.
- Taker fees are charged on BOTH legs at ``taker_fee_bps`` (default 10 bps,
  a conservative retail-exchange round-trip assumption).
- The bankroll is HYPOTHETICAL PAPER capital; no wallet or balance is read.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .models import ExecutionSim, Signal, MarketTick, Action, utc_now_iso, new_id
from .liquidity import Orderbook
from .paper_lock import assert_paper_locked, hard_paper_lock_active


class ExitReason(str, Enum):
    STOP = "STOP"
    TARGET = "TARGET"
    TIME_STOP = "TIME_STOP"
    MANUAL = "MANUAL"


@dataclass
class Position:
    symbol: str
    side: str  # LONG | SHORT
    qty: float
    entry_price: float
    entry_ts: str
    stop_price: float
    target_price: float
    time_stop_minutes: int = 30
    signal_id: str = ""
    status: str = "OPEN"  # OPEN | CLOSED
    exit_price: float = 0.0
    exit_ts: str = ""
    exit_reason: str = ""
    realized_pnl: float = 0.0
    fees_paid: float = 0.0


def _short_trace(signal: Signal) -> str:
    """Tail of the signal trace id (or event id) for exec trace labels."""
    tail = (signal.trace_id or signal.event_id or "notrace")
    return tail[-6:]


@dataclass
class ExecutionSimulator:
    """Realistic PAPER execution against observable market data.

    The ``cash``/``starting_cash`` pair is the HYPOTHETICAL paper bankroll.
    Orders whose notional exceeds available paper cash are rejected.
    """

    allow_real: bool = False  # structurally refused by HARD PAPER LOCK
    base_slippage_bps: float = 2.0
    rng_slippage_bps: float = 4.0
    exit_slippage_bps: float = 3.0
    taker_fee_bps: float = 10.0          # conservative per-leg taker fee
    latency_ms_min: int = 50
    latency_ms_max: int = 300
    assumed_depth_usd: float = 5_000.0   # used only when no book is supplied
    stop_pct: float = 0.02               # 2% stop
    target_pct: float = 0.04             # 4% target
    paper_bankroll_usd: float = 10_000.0 # HYPOTHETICAL paper capital
    positions: dict[str, Position] = field(default_factory=dict)  # symbol -> Position
    closed: list[Position] = field(default_factory=list)
    cash: float = 10_000.0
    starting_cash: float = 10_000.0
    fees_cumulative: float = 0.0
    _rng: random.Random = field(default=None, init=False)

    def __post_init__(self):
        if self.allow_real:
            # This candidate structurally refuses live execution (B8.1 lock).
            assert_paper_locked("ExecutionSimulator(allow_real=True)")
        self._rng = random.Random(7)
        if self.cash == 10_000.0 and self.starting_cash == 10_000.0:
            # Default construction follows the configurable bankroll.
            self.cash = float(self.paper_bankroll_usd)
            self.starting_cash = float(self.paper_bankroll_usd)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "allow_real" and value and hard_paper_lock_active():
            assert_paper_locked("ExecutionSimulator.allow_real=True")
        object.__setattr__(self, name, value)

    # ------------------------------------------------------------------ fees
    def _fee_usd(self, notional: float) -> float:
        return abs(notional) * self.taker_fee_bps / 10_000.0

    # ------------------------------------------------------------------ fills
    def _depth_usd(self, book: Orderbook | None, sizing_usd: float) -> tuple[float, str]:
        if book is not None:
            try:
                return float(book.depth_notional()), "book_observed"
            except Exception:
                return 0.0, "book_error"
        return float(self.assumed_depth_usd), "assumed"

    async def execute(self, signal: Signal, tick: MarketTick | None, book: Orderbook | None = None) -> ExecutionSim:
        action = signal.payload.get("action")
        if action not in (Action.LONG.value, "LONG", Action.SHORT.value if hasattr(Action, "SHORT") else "SHORT"):
            return ExecutionSim(
                source="exec_sim",
                symbol=signal.symbol,
                trace_id=f"exec-{_short_trace(signal)}",
                payload={
                    "order_id": new_id("ord"),
                    "status": "SKIPPED",
                    "reason": f"action={action}",
                },
            )

        if self.allow_real:
            # Intentionally unreachable under the HARD PAPER LOCK; retained as
            # defense-in-depth for any future candidate without the lock.
            raise RuntimeError("allow_real=True requires a separate broker adapter; refusing to execute live")

        sizing_usd = signal.payload.get("sizing_usd", 0)
        if sizing_usd <= 0 or tick is None:
            return ExecutionSim(
                source="exec_sim",
                symbol=signal.symbol,
                trace_id=f"exec-{_short_trace(signal)}",
                payload={"order_id": new_id("ord"), "status": "REJECTED", "reason": "no_sizing_or_tick"},
            )

        price = tick.payload.get("price", 0)
        if price <= 0:
            return ExecutionSim(
                source="exec_sim",
                symbol=signal.symbol,
                trace_id=f"exec-{_short_trace(signal)}",
                payload={"order_id": new_id("ord"), "status": "REJECTED", "reason": "invalid_price"},
            )

        if signal.symbol in self.positions and self.positions[signal.symbol].status == "OPEN":
            return ExecutionSim(
                source="exec_sim",
                symbol=signal.symbol,
                trace_id=f"exec-{_short_trace(signal)}",
                payload={"order_id": new_id("ord"), "status": "REJECTED", "reason": "position_already_open"},
            )

        # simulate decision-to-fill latency (conservative range)
        latency = self._rng.randint(self.latency_ms_min, self.latency_ms_max)
        await asyncio.sleep(latency / 1000.0)

        side = "LONG" if action in (Action.LONG.value, "LONG") else "SHORT"
        # Marketable-limit slippage: pay the spread edge on entry.
        slip_bps = self.base_slippage_bps + self._rng.uniform(0, self.rng_slippage_bps)
        if side == "LONG":
            fill_price = price * (1 + slip_bps / 10_000)   # buying up
        else:
            fill_price = price * (1 - slip_bps / 10_000)   # selling down

        # Conservative partial fill: bounded by observable (or labeled
        # assumed) depth. NO optimistic minimum-fill floor.
        depth_usd, depth_source = self._depth_usd(book, sizing_usd)
        fill_pct = min(1.0, depth_usd / sizing_usd) if sizing_usd > 0 and depth_usd > 0 else 0.0
        fill_notional = sizing_usd * fill_pct
        if fill_notional < 1.0:
            return ExecutionSim(
                source="exec_sim",
                symbol=signal.symbol,
                trace_id=f"exec-{_short_trace(signal)}",
                payload={
                    "order_id": new_id("ord"),
                    "status": "MISSED",
                    "reason": "insufficient_depth",
                    "depth_usd": round(depth_usd, 2),
                    "depth_source": depth_source,
                    "sizing_usd": round(sizing_usd, 2),
                    "fill_pct": round(fill_pct, 3),
                    "hypothetical": True,
                },
            )

        qty = fill_notional / fill_price
        entry_fee = self._fee_usd(fill_notional)
        if fill_notional + entry_fee > self.cash:
            return ExecutionSim(
                source="exec_sim",
                symbol=signal.symbol,
                trace_id=f"exec-{_short_trace(signal)}",
                payload={
                    "order_id": new_id("ord"),
                    "status": "REJECTED",
                    "reason": "insufficient_paper_cash",
                    "sizing_usd": round(sizing_usd, 2),
                    "cash_available": round(self.cash, 2),
                    "hypothetical": True,
                },
            )

        if side == "LONG":
            stop = fill_price * (1 - self.stop_pct)
            target = fill_price * (1 + self.target_pct)
        else:
            stop = fill_price * (1 + self.stop_pct)
            target = fill_price * (1 - self.target_pct)
        pos = Position(
            symbol=signal.symbol,
            side=side,
            qty=qty,
            entry_price=fill_price,
            entry_ts=utc_now_iso(),
            stop_price=stop,
            target_price=target,
            signal_id=signal.event_id,
            fees_paid=entry_fee,
        )
        self.positions[signal.symbol] = pos
        self.cash -= fill_notional + entry_fee
        self.fees_cumulative += entry_fee

        return ExecutionSim(
            source="exec_sim",
            symbol=signal.symbol,
            trace_id=f"exec-{_short_trace(signal)}",
            payload={
                "order_id": new_id("ord"),
                "side": "BUY" if side == "LONG" else "SELL",
                "direction": side,
                "qty": round(qty, 6),
                "notional_usd": round(fill_notional, 2),
                "fill_price": round(fill_price, 4),
                "slippage_bps": round(slip_bps, 2),
                "latency_ms": latency,
                "fill_pct": round(fill_pct, 3),
                "depth_source": depth_source,
                "fee_usd": round(entry_fee, 4),
                "fees_cumulative": round(self.fees_cumulative, 4),
                "status": "FILLED" if fill_pct >= 1.0 else "PARTIAL_FILL",
                "cash_after": round(self.cash, 2),
                "hypothetical": True,
                "bankroll_start": round(self.starting_cash, 2),
                "position": {
                    "stop": round(stop, 4),
                    "target": round(target, 4),
                    "entry": round(fill_price, 4),
                    "qty": round(qty, 6),
                    "side": side,
                },
            },
        )

    async def monitor_exits(self, tick: MarketTick) -> list[ExecutionSim]:
        """Check open positions against current tick. Returns list of exit events."""
        sym = tick.symbol
        price = tick.payload.get("price", 0)
        ts = tick.ts
        exits: list[ExecutionSim] = []
        pos = self.positions.get(sym)
        if not pos or pos.status != "OPEN":
            return exits

        reason = None
        if pos.side == "LONG":
            if price <= pos.stop_price:
                reason = ExitReason.STOP
            elif price >= pos.target_price:
                reason = ExitReason.TARGET
        else:  # SHORT
            if price >= pos.stop_price:
                reason = ExitReason.STOP
            elif price <= pos.target_price:
                reason = ExitReason.TARGET
        if reason is None:
            # time stop
            try:
                entry_dt = datetime.fromisoformat(pos.entry_ts.replace("Z", "+00:00"))
                now_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if (now_dt - entry_dt).total_seconds() / 60 >= pos.time_stop_minutes:
                    reason = ExitReason.TIME_STOP
            except Exception:
                pass

        if reason is None:
            return exits

        exit_slip_bps = self._rng.uniform(0, self.exit_slippage_bps)
        if pos.side == "LONG":
            exit_price = price * (1 - exit_slip_bps / 10_000)   # selling down
            gross = (exit_price - pos.entry_price) * pos.qty
        else:
            exit_price = price * (1 + exit_slip_bps / 10_000)   # buying up
            gross = (pos.entry_price - exit_price) * pos.qty
        exit_notional = exit_price * pos.qty
        exit_fee = self._fee_usd(exit_notional)
        pos.exit_price = exit_price
        pos.exit_ts = ts
        pos.exit_reason = reason.value
        pos.status = "CLOSED"
        # realized PnL nets BOTH legs' fees (entry fee is in pos.fees_paid)
        pos.realized_pnl = gross - exit_fee - pos.fees_paid
        pos.fees_paid = round(pos.fees_paid + exit_fee, 6)
        self.cash += exit_notional - exit_fee
        self.fees_cumulative += exit_fee
        self.closed.append(pos)
        del self.positions[sym]
        exits.append(ExecutionSim(
            source="exec_sim",
            symbol=sym,
            trace_id=f"exit-{(pos.signal_id or 'notrace')[-6:]}",
            payload={
                "order_id": new_id("ord"),
                "side": "SELL" if pos.side == "LONG" else "BUY",
                "direction_exit": pos.side,
                "qty": round(pos.qty, 6),
                "fill_price": round(exit_price, 4),
                "slippage_bps": round(exit_slip_bps, 2),
                "fee_usd": round(exit_fee, 4),
                "fees_cumulative": round(self.fees_cumulative, 4),
                "status": "FILLED",
                "reason": reason.value,
                "gross_pnl": round(gross, 2),
                "realized_pnl": round(pos.realized_pnl, 2),
                "cash_after": round(self.cash, 2),
                "hypothetical": True,
            },
        ))
        return exits

    def risk_state(self, last_prices: dict[str, float] | None = None) -> dict:
        last_prices = last_prices or {}
        gross = 0.0
        unrealized = 0.0
        for p in self.positions.values():
            if p.status != "OPEN":
                continue
            mark = float(last_prices.get(p.symbol, p.entry_price))  # MTM at entry when no tick (stated approximation)
            gross += p.qty * p.entry_price
            if p.side == "LONG":
                unrealized += (mark - p.entry_price) * p.qty
            else:
                unrealized += (p.entry_price - mark) * p.qty
        realized = sum(p.realized_pnl for p in self.closed)
        equity = self.cash + gross + unrealized
        peak = getattr(self, "_peak_equity", self.starting_cash)
        if equity > peak:
            peak = equity
        self._peak_equity = peak
        drawdown_pct = 0.0
        if peak > 0 and equity < peak:
            drawdown_pct = (peak - equity) / peak * 100
        return {
            "cash": round(self.cash, 2),
            "gross_exposure": round(gross, 2),
            "equity": round(equity, 2),
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(realized, 2),
            "fees_paid": round(self.fees_cumulative, 4),
            "open_positions": len([p for p in self.positions.values() if p.status == "OPEN"]),
            "closed_positions": len(self.closed),
            "win_rate": (sum(1 for p in self.closed if p.realized_pnl > 0) / len(self.closed) * 100) if self.closed else 0.0,
            "drawdown_pct": round(drawdown_pct, 2),
            "return_pct": round((equity - self.starting_cash) / self.starting_cash * 100, 4) if self.starting_cash > 0 else 0.0,
            "allow_real": self.allow_real,
            "hypothetical": True,
            "bankroll": {
                "label": "HYPOTHETICAL_PAPER_BANKROLL",
                "starting_usd": round(self.starting_cash, 2),
                "current_usd": round(equity, 2),
                "taker_fee_bps": self.taker_fee_bps,
                "assumed_depth_usd": self.assumed_depth_usd,
            },
        }
