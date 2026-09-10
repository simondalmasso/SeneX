"""
SENECIO ORACLE — Layer 5+: Scheduler / Agentic Loop
====================================================
Owns the main asyncio loop that:
  1. Consumes MarketTick stream
  2. Maintains per-symbol rolling state (OHLC, SMA, prev_close)
  3. Runs Scanner A on a schedule (every 30s in demo, configurable)
  4. Runs Scanner B continuously (every tick)
  5. Feeds candidates through OracleEngine (brain)
  6. Hands signals to ExecutionSimulator
  7. Monitors open positions for exits
  8. Periodically emits RiskState snapshots

This is the "agent orchestration loop" — single-planner, multi-tool.
"""
from __future__ import annotations

import asyncio
import logging
import random
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .models import (
    MarketTick, MarketCandidate, Signal, WalletAlert, RiskState, AuditTrace,
    Action, utc_now_iso, new_id,
)
from .event_bus import EventBus
from .data_retriever import DataRetriever
from .scanner_a import ScannerA
from .scanner_b import ScannerB
from .wallet_tracker import WalletTracker
from .oracle_engine import OracleEngine
from .execution_simulator import ExecutionSimulator
from .liquidity import synth_book_from_tick, Orderbook

log = logging.getLogger("senecio.scheduler")


@dataclass
class SchedulerConfig:
    scanner_a_interval_s: float = 8.0
    risk_snapshot_interval_s: float = 10.0
    max_ticks_per_symbol: int = 500
    sma_window: int = 200


@dataclass
class Scheduler:
    bus: EventBus
    retriever: DataRetriever
    scanner_a: ScannerA
    scanner_b: ScannerB
    wallet_tracker: WalletTracker
    engine: OracleEngine
    executor: ExecutionSimulator
    cfg: SchedulerConfig = field(default_factory=SchedulerConfig)
    # rolling state
    ticks: dict[str, deque] = field(default_factory=lambda: {})
    closes: dict[str, list[float]] = field(default_factory=lambda: {})
    prev_close: dict[str, float] = field(default_factory=lambda: {})
    yesterday_high: dict[str, float] = field(default_factory=lambda: {})
    premarket_high: dict[str, float] = field(default_factory=lambda: {})
    latest_tick: dict[str, MarketTick] = field(default_factory=lambda: {})
    # book cache
    books: dict[str, Orderbook] = field(default_factory=lambda: {})
    # tasks
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _running: bool = False
    # counters
    ticks_processed: int = 0
    candidates_a: int = 0
    candidates_b: int = 0
    signals_emitted: int = 0

    async def _emit_audit(self, layer: str, msg: str, severity: str = "INFO", **ctx) -> None:
        await self.bus.publish(AuditTrace(
            source="scheduler",
            trace_id=f"audit-{new_id('')}",
            payload={"layer": layer, "msg": msg, "severity": severity, "context": ctx},
        ))

    async def _tick_loop(self) -> None:
        """Consume market ticks, dispatch to scanner_b, monitor exits."""
        async for tick in self.retriever.stream_ticks(interval_s=1.0):
            self.ticks.setdefault(tick.symbol, deque(maxlen=self.cfg.max_ticks_per_symbol)).append(tick)
            self.latest_tick[tick.symbol] = tick
            # close history (5-tick rolling close)
            closes = self.closes.setdefault(tick.symbol, [])
            closes.append(tick.payload["price"])
            if len(closes) > self.cfg.sma_window + 10:
                closes = closes[-(self.cfg.sma_window + 10):]
                self.closes[tick.symbol] = closes
            # update SMA + day context (synthetic)
            if len(closes) >= self.cfg.sma_window:
                sma = statistics.mean(closes[-self.cfg.sma_window:])
                self.scanner_b.sma_cache[tick.symbol] = sma
                # set day context only ONCE per symbol (avoids resetting today_high)
                if tick.symbol not in self.prev_close:
                    self.prev_close[tick.symbol] = closes[-2] if len(closes) >= 2 else tick.payload["price"]
                if tick.symbol not in self.yesterday_high:
                    self.yesterday_high[tick.symbol] = max(closes[-5:]) if len(closes) >= 5 else tick.payload["price"]
                if tick.symbol not in self.premarket_high:
                    self.premarket_high[tick.symbol] = tick.payload["price"] * 1.005
                # propagate to scanner_b ONLY if not already set
                if tick.symbol not in self.scanner_b.prev_close:
                    self.scanner_b.set_day_context(
                        tick.symbol,
                        self.prev_close[tick.symbol],
                        self.yesterday_high[tick.symbol],
                        self.premarket_high[tick.symbol],
                    )
            # publish tick
            await self.bus.publish(tick)
            # build book + cache
            book = synth_book_from_tick(tick)
            self.books[tick.symbol] = book
            # scanner B (continuous)
            cand_b = self.scanner_b.scan(tick.symbol, tick.payload["price"], tick.payload.get("volume", 0))
            if cand_b:
                self.candidates_b += 1
                await self.bus.publish(cand_b)
                signal = await self.engine.decide(cand_b, tick)
                self.signals_emitted += 1
                await self.bus.publish(signal)
                # execute
                exec_ev = await self.executor.execute(signal, tick, book)
                await self.bus.publish(exec_ev)
            # monitor exits on this tick
            exits = await self.executor.monitor_exits(tick)
            for ex in exits:
                await self.bus.publish(ex)
                # feedback for brain calibration
                pnl_pct = ex.payload.get("realized_pnl", 0) / max(1, ex.payload.get("fill_price", 1) * ex.payload.get("qty", 1))
                self.engine.record_outcome("B_trend_join_long", pnl_pct * 100)
            self.ticks_processed += 1

    async def _wallet_loop(self) -> None:
        async for ev in self.retriever.stream_wallet_alerts(interval_s=2.0):
            await self.bus.publish(ev)
            self.engine.observe_wallet(ev)
            derived = self.wallet_tracker.ingest(ev)
            for d in derived:
                await self.bus.publish(d)

    async def _scanner_a_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.cfg.scanner_a_interval_s)
            try:
                # build a snapshot from latest ticks (pretend this is pre-market)
                snapshot = {sym: t.payload for sym, t in self.latest_tick.items()}
                candidates = await self.scanner_a.scan(snapshot, self.prev_close)
                for c in candidates:
                    self.candidates_a += 1
                    await self.bus.publish(c)
                    tick = self.latest_tick.get(c.symbol)
                    signal = await self.engine.decide(c, tick)
                    self.signals_emitted += 1
                    await self.bus.publish(signal)
                    book = self.books.get(c.symbol)
                    exec_ev = await self.executor.execute(signal, tick, book)
                    await self.bus.publish(exec_ev)
            except Exception as e:
                log.exception("scanner_a loop error: %s", e)
                await self._emit_audit("scanner_a", f"error: {e}", severity="ERROR")

    async def _risk_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.cfg.risk_snapshot_interval_s)
            try:
                r = self.executor.risk_state()
                await self.bus.publish(RiskState(
                    source="scheduler",
                    trace_id=f"risk-{new_id('')}",
                    payload=r,
                ))
            except Exception as e:
                log.exception("risk loop error: %s", e)

    def start(self) -> None:
        self._running = True
        # pre-seed per-symbol history so SMA(200) + scanners can fire immediately
        self._seed_history()
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="tick_loop"),
            asyncio.create_task(self._wallet_loop(), name="wallet_loop"),
            asyncio.create_task(self._scanner_a_loop(), name="scanner_a_loop"),
            asyncio.create_task(self._risk_loop(), name="risk_loop"),
        ]
        log.info("scheduler started with %d tasks (history pre-seeded)", len(self._tasks))

    def _seed_history(self) -> None:
        """Synthesize 250 prior closes per symbol so SMA + scanners activate immediately."""
        import random as _r
        rng = _r.Random(99)
        for item in self.retriever.catalog():
            sym = item["symbol"]
            base = item["base_price"]
            # 250 random-walk closes with mild uptrend for some, downtrend for others
            trend = rng.choice([-0.0005, 0.0, 0.001, 0.002])
            vol  = 0.01 + rng.random() * 0.02
            px = base * 0.9
            closes = []
            for _ in range(250):
                px = max(0.1, px * (1 + trend + rng.gauss(0, vol)))
                closes.append(px)
            self.closes[sym] = closes
            # yesterday_high = max of last 5 closes (realistic intraday range)
            # premarket_high = just above yesterday's close (typical gap setup)
            recent = closes[-5:]
            self.prev_close[sym]       = closes[-1]
            self.yesterday_high[sym]   = max(recent) * 0.998  # slightly under so today's ticks can break it
            self.premarket_high[sym]   = closes[-1] * (1 + rng.uniform(-0.001, 0.005))
            # SMA(200) from the last 200 closes
            self.scanner_b.sma_cache[sym] = statistics.mean(closes[-200:])
            self.scanner_b.set_day_context(
                sym,
                self.prev_close[sym],
                self.yesterday_high[sym],
                self.premarket_high[sym],
            )
            # also seed scanner_b.history so set_day_context isn't overwritten
            self.scanner_b.history[sym] = [{"c": c, "h": c * 1.005, "l": c * 0.995, "o": c, "v": 1000} for c in closes[-50:]]

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    def stats(self) -> dict:
        return {
            "ticks_processed": self.ticks_processed,
            "candidates_a": self.candidates_a,
            "candidates_b": self.candidates_b,
            "signals_emitted": self.signals_emitted,
            "symbols_tracked": len(self.ticks),
            "running": self._running,
        }
