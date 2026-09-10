"""
SENECIO ORACLE — Layer 4: Execution Simulator
==============================================
Paper-only execution engine. Models:
- Fill simulation (marketable limit at midpoint + slippage)
- Latency (random 50-300ms)
- Partial fills (based on book depth)
- Stateful position tracking
- Exit monitor (stop / target / time-stop)

NO REAL ORDERS ARE PLACED unless an explicit allow_real flag is set,
and even then this module refuses to send live orders (it only logs
the intent). Real execution must be added by a separate adapter.
"""
from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .models import ExecutionSim, Signal, MarketTick, Action, utc_now_iso, new_id
from .liquidity import Orderbook, synth_book_from_tick


class ExitReason(str, Enum):
    STOP = "STOP"
    TARGET = "TARGET"
    TIME_STOP = "TIME_STOP"
    MANUAL = "MANUAL"


@dataclass
class Position:
    symbol: str
    side: str  # LONG
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


@dataclass
class ExecutionSimulator:
    allow_real: bool = False  # NEVER flip this without separate adapter
    base_slippage_bps: float = 2.0
    rng_slippage_bps: float = 4.0
    latency_ms_min: int = 50
    latency_ms_max: int = 300
    positions: dict[str, Position] = field(default_factory=dict)  # symbol -> Position
    closed: list[Position] = field(default_factory=list)
    cash: float = 10_000.0
    starting_cash: float = 10_000.0
    _rng: random.Random = field(default=None, init=False)

    def __post_init__(self):
        self._rng = random.Random(7)

    async def execute(self, signal: Signal, tick: MarketTick | None, book: Orderbook | None = None) -> ExecutionSim:
        if signal.payload.get("action") != Action.LONG.value:
            return ExecutionSim(
                source="exec_sim",
                symbol=signal.symbol,
                trace_id=f"exec-{signal.trace_id[-6:]}",
                payload={
                    "order_id": new_id("ord"),
                    "status": "SKIPPED",
                    "reason": f"action={signal.payload.get('action')}",
                },
            )

        if self.allow_real:
            # this branch is intentionally a hard error — we never auto-place live orders
            raise RuntimeError("allow_real=True requires a separate broker adapter; refusing to execute live")

        sizing_usd = signal.payload.get("sizing_usd", 0)
        if sizing_usd <= 0 or tick is None:
            return ExecutionSim(
                source="exec_sim",
                symbol=signal.symbol,
                trace_id=f"exec-{signal.trace_id[-6:]}",
                payload={"order_id": new_id("ord"), "status": "REJECTED", "reason": "no_sizing_or_tick"},
            )

        price = tick.payload.get("price", 0)
        if price <= 0:
            return ExecutionSim(
                source="exec_sim",
                symbol=signal.symbol,
                trace_id=f"exec-{signal.trace_id[-6:]}",
                payload={"order_id": new_id("ord"), "status": "REJECTED", "reason": "invalid_price"},
            )

        # simulate latency
        latency = self._rng.randint(self.latency_ms_min, self.latency_ms_max)
        await asyncio.sleep(latency / 1000.0)

        # slippage
        slip_bps = self.base_slippage_bps + self._rng.uniform(0, self.rng_slippage_bps)
        fill_price = price * (1 + slip_bps / 10_000)  # we're BUYING

        # partial fill: based on book depth
        depth = book.depth_notional() if book else sizing_usd * 2
        fill_pct = min(1.0, depth / sizing_usd) if sizing_usd > 0 else 0
        if fill_pct < 0.3:
            fill_pct = 0.3  # minimum 30% fill
        qty = (sizing_usd * fill_pct) / fill_price

        # position management — flat-to-open only (no pyramiding in v1)
        if signal.symbol in self.positions and self.positions[signal.symbol].status == "OPEN":
            return ExecutionSim(
                source="exec_sim",
                symbol=signal.symbol,
                trace_id=f"exec-{signal.trace_id[-6:]}",
                payload={
                    "order_id": new_id("ord"),
                    "status": "REJECTED",
                    "reason": "position_already_open",
                },
            )

        # set stop / target (2% stop, 4% target by default)
        stop = fill_price * 0.98
        target = fill_price * 1.04
        pos = Position(
            symbol=signal.symbol,
            side="LONG",
            qty=qty,
            entry_price=fill_price,
            entry_ts=utc_now_iso(),
            stop_price=stop,
            target_price=target,
            signal_id=signal.event_id,
        )
        self.positions[signal.symbol] = pos
        self.cash -= qty * fill_price

        return ExecutionSim(
            source="exec_sim",
            symbol=signal.symbol,
            trace_id=f"exec-{signal.trace_id[-6:]}",
            payload={
                "order_id": new_id("ord"),
                "side": "BUY",
                "qty": round(qty, 6),
                "notional_usd": round(qty * fill_price, 2),
                "fill_price": round(fill_price, 4),
                "slippage_bps": round(slip_bps, 2),
                "latency_ms": latency,
                "fill_pct": round(fill_pct, 3),
                "status": "FILLED",
                "cash_after": round(self.cash, 2),
                "position": {
                    "stop": round(stop, 4),
                    "target": round(target, 4),
                    "entry": round(fill_price, 4),
                    "qty": round(qty, 6),
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
        if price <= pos.stop_price:
            reason = ExitReason.STOP
        elif price >= pos.target_price:
            reason = ExitReason.TARGET
        else:
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

        exit_price = price
        slippage = self._rng.uniform(0, 3)
        exit_price *= (1 - slippage / 10_000)  # we're SELLING
        pos.exit_price = exit_price
        pos.exit_ts = ts
        pos.exit_reason = reason.value
        pos.status = "CLOSED"
        pos.realized_pnl = (exit_price - pos.entry_price) * pos.qty
        self.cash += pos.qty * exit_price
        self.closed.append(pos)
        del self.positions[sym]
        exits.append(ExecutionSim(
            source="exec_sim",
            symbol=sym,
            trace_id=f"exit-{pos.signal_id[-6:]}",
            payload={
                "order_id": new_id("ord"),
                "side": "SELL",
                "qty": round(pos.qty, 6),
                "fill_price": round(exit_price, 4),
                "slippage_bps": round(slippage, 2),
                "status": "FILLED",
                "reason": reason.value,
                "realized_pnl": round(pos.realized_pnl, 2),
                "cash_after": round(self.cash, 2),
            },
        ))
        return exits

    def risk_state(self) -> dict:
        gross = sum(p.qty * p.entry_price for p in self.positions.values() if p.status == "OPEN")
        realized = sum(p.realized_pnl for p in self.closed)
        equity = self.cash + gross  # mark-to-market at entry
        drawdown_pct = 0.0
        if self.starting_cash > 0 and equity < self.starting_cash:
            drawdown_pct = (self.starting_cash - equity) / self.starting_cash * 100
        return {
            "cash": round(self.cash, 2),
            "gross_exposure": round(gross, 2),
            "equity": round(equity, 2),
            "realized_pnl": round(realized, 2),
            "open_positions": len([p for p in self.positions.values() if p.status == "OPEN"]),
            "closed_positions": len(self.closed),
            "win_rate": (sum(1 for p in self.closed if p.realized_pnl > 0) / len(self.closed) * 100) if self.closed else 0.0,
            "drawdown_pct": round(drawdown_pct, 2),
            "allow_real": self.allow_real,
        }
