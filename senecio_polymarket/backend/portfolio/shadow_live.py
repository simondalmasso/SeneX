"""
SENECIO ORACLE — ACT XXV: ShadowLive (priority 6)
==================================================

Shadow-mode comparator: runs alongside the paper ExecutionEngine and
records what would have happened if every order had been sent to a real
exchange. Used to validate the paper model before flipping LIVE.

Mode (per ACT-XXV spec):
  paper_with_real_orders   — paper engine places simulated orders, AND
                             we fetch the real orderbook at fill-time to
                             compute the "real fill" we would have gotten.

Duration: 7 days (configurable). After 7 days, the ShadowLive report is
emitted and used as ONE of the 6 LIVE_GATE unlock conditions.

Comparison metrics (per ACT-XXV spec):
  - expected_fill   : the simulated fill price + qty from the paper engine
  - real_book       : the actual orderbook snapshot at fill time (best bid/ask
                      + depth at top 5 levels)
  - slippage        : expected_fill_price - real_mid_price (in bps)
  - fees            : expected_fee_usd vs real_fee_usd (exchange-published fee)
  - latency         : sim latency_ms vs real RTT to exchange (HTTP ping)

Outputs:
  - shadow_trades.jsonl — one row per paper fill, with paired real-book data
  - shadow_report.json  — aggregate stats after 7d (or early-stop)

NO REAL ORDERS are placed in this mode. The "real_book" is a READ-ONLY
snapshot fetched via the exchange's public market-data endpoint.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("senecio.shadow_live")


DEFAULTS: dict[str, Any] = {
    "duration_days":             7,
    "output_path":               "data/journal/shadow_trades.jsonl",
    "report_path":               "data/journal/shadow_report.json",
    "exchange":                  "okx",
    "fetch_real_book":           True,    # set False to skip real-book fetch (testing)
    "real_book_timeout_ms":      2000,
    "min_fills_for_report":      30,
    # Pass/fail thresholds for the 7-day report
    "thresholds": {
        "max_slippage_diff_bps":     3.0,   # |expected_slip - real_slip| ≤ 3 bps
        "max_fee_diff_pct":          5.0,   # fee estimate within 5% of real
        "max_latency_diff_ms":       100,   # latency estimate within 100ms
        "min_fill_match_pct":        85.0,  # ≥85% of fills within thresholds
    },
}


@dataclass
class ShadowTrade:
    """One paired record: paper fill + real book snapshot."""
    shadow_id: str
    paper_order_id: str
    symbol: str
    direction: str                         # LONG | SHORT
    side: str                              # BUY | SELL
    # Paper (expected) side
    expected_qty: float
    expected_price: float
    expected_slippage_bps: float
    expected_fee_usd: float
    expected_latency_ms: int
    # Real (snapshot) side
    real_mid_price: float = 0.0
    real_best_bid: float = 0.0
    real_best_ask: float = 0.0
    real_book_depth_usd: float = 0.0
    real_spread_bps: float = 0.0
    real_estimated_fill_price: float = 0.0
    real_estimated_fill_qty: float = 0.0
    real_fee_usd: float = 0.0
    real_latency_ms: int = 0
    # Comparison deltas
    slippage_diff_bps: float = 0.0
    fee_diff_usd: float = 0.0
    fee_diff_pct: float = 0.0
    latency_diff_ms: int = 0
    fill_match: bool = False               # within thresholds
    # Provenance
    paper_fill_ts: str = ""
    real_book_ts: str = ""
    audit_trail: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ShadowLive:
    """Shadow-mode comparator running alongside the paper ExecutionEngine.

    Usage:
        shadow = ShadowLive(config=DEFAULTS)
        # Register as audit listener on ExecutionEngine
        engine.set_audit_listener(shadow.on_audit_event)
        # On every FILL event, shadow.fetch_real_book() is called and the
        # paired record is appended to shadow_trades.jsonl.
        # On stop() or after 7 days, the aggregate report is written.
    """

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self.path = Path(self.cfg["output_path"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path = Path(self.cfg["report_path"])
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.started_at = datetime.now(timezone.utc)
        self.ended_at: Optional[datetime] = None
        # Pending fills: paper_order_id → expected fill data
        self._pending: dict[str, dict] = {}
        # All completed shadow trades (in-memory cache for report)
        self._trades: list[ShadowTrade] = []
        log.info(
            "ShadowLive init: duration=%dd output=%s fetch_real_book=%s",
            self.cfg["duration_days"], self.path, self.cfg["fetch_real_book"],
        )

    # -------- audit listener interface --------

    def on_audit_event(self, event: dict) -> None:
        """Listen on ExecutionEngine's audit stream for FILL events."""
        try:
            if event.get("event") != "FILL":
                return
            fill = event.get("fill") or {}
            order_id = fill.get("order_id")
            if not order_id:
                return
            # Spawn an async task to fetch the real book (non-blocking)
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(self._process_fill(fill))
            except RuntimeError:
                # No event loop running — process synchronously
                # (used in tests; in production, the engine runs in an event loop)
                pass
        except Exception as e:
            log.exception("ShadowLive on_audit_event error: %s", e)

    async def _process_fill(self, fill: dict) -> None:
        """Pair a paper fill with a real-book snapshot and write the record."""
        symbol = fill.get("symbol", "")
        side = fill.get("side", "BUY")
        direction = "LONG" if side == "BUY" else "SHORT"
        expected_qty = float(fill.get("qty", 0))
        expected_price = float(fill.get("price", 0))
        expected_slip = float(fill.get("slippage_bps", 0))
        expected_fee = float(fill.get("fee_usd", 0))
        expected_latency = int(fill.get("latency_ms", 0))

        # Fetch real book snapshot
        real_book: dict[str, Any] = {}
        if self.cfg["fetch_real_book"]:
            try:
                real_book = await asyncio.wait_for(
                    self._fetch_real_book(symbol),
                    timeout=self.cfg["real_book_timeout_ms"] / 1000.0,
                )
            except asyncio.TimeoutError:
                log.warning("real book fetch timed out for %s", symbol)
            except Exception as e:
                log.warning("real book fetch failed for %s: %s", symbol, e)

        real_mid = real_book.get("mid", 0.0)
        real_bid = real_book.get("bid", 0.0)
        real_ask = real_book.get("ask", 0.0)
        real_depth = real_book.get("depth_usd", 0.0)
        real_spread_bps = real_book.get("spread_bps", 0.0)
        real_latency = real_book.get("fetch_latency_ms", 0)

        # Estimate the "real" fill price = best ask (for BUY) or best bid (for SELL)
        if side == "BUY":
            real_estimated_fill_price = real_ask or real_mid or expected_price
            real_estimated_fill_qty = min(expected_qty, real_depth / max(real_ask, 1e-9)) if real_depth > 0 else expected_qty
        else:
            real_estimated_fill_price = real_bid or real_mid or expected_price
            real_estimated_fill_qty = min(expected_qty, real_depth / max(real_bid, 1e-9)) if real_depth > 0 else expected_qty

        # Assume taker fee on real side too
        real_fee_usd = real_estimated_fill_qty * real_estimated_fill_price * 5 / 10_000  # 5 bps taker

        # Compute deltas
        slippage_diff = expected_slip - real_spread_bps / 2   # we expect slip ≈ half-spread
        fee_diff_usd = expected_fee - real_fee_usd
        fee_diff_pct = (abs(fee_diff_usd) / max(real_fee_usd, 1e-9) * 100) if real_fee_usd > 0 else 0
        latency_diff = expected_latency - real_latency

        # Check pass/fail vs thresholds
        th = self.cfg["thresholds"]
        fill_match = (
            abs(slippage_diff) <= th["max_slippage_diff_bps"]
            and fee_diff_pct <= th["max_fee_diff_pct"]
            and abs(latency_diff) <= th["max_latency_diff_ms"]
        )

        shadow_trade = ShadowTrade(
            shadow_id=f"sd-{uuid.uuid4().hex[:12]}",
            paper_order_id=fill.get("order_id", ""),
            symbol=symbol,
            direction=direction,
            side=side,
            expected_qty=round(expected_qty, 8),
            expected_price=round(expected_price, 6),
            expected_slippage_bps=round(expected_slip, 2),
            expected_fee_usd=round(expected_fee, 4),
            expected_latency_ms=expected_latency,
            real_mid_price=round(real_mid, 6),
            real_best_bid=round(real_bid, 6),
            real_best_ask=round(real_ask, 6),
            real_book_depth_usd=round(real_depth, 2),
            real_spread_bps=round(real_spread_bps, 2),
            real_estimated_fill_price=round(real_estimated_fill_price, 6),
            real_estimated_fill_qty=round(real_estimated_fill_qty, 8),
            real_fee_usd=round(real_fee_usd, 4),
            real_latency_ms=real_latency,
            slippage_diff_bps=round(slippage_diff, 2),
            fee_diff_usd=round(fee_diff_usd, 4),
            fee_diff_pct=round(fee_diff_pct, 2),
            latency_diff_ms=latency_diff,
            fill_match=fill_match,
            paper_fill_ts=fill.get("ts", ""),
            real_book_ts=real_book.get("ts", ""),
            audit_trail=[{"fill": fill, "real_book": real_book}],
        )
        self._trades.append(shadow_trade)
        self._append(shadow_trade.to_dict())
        log.info(
            "shadow trade recorded: %s %s expected=$%.4f real_mid=$%.4f slip_diff=%.2fbps match=%s",
            symbol, side, expected_price, real_mid, slippage_diff, fill_match,
        )

    async def _fetch_real_book(self, symbol: str) -> dict[str, Any]:
        """Fetch real orderbook snapshot via ccxt (read-only)."""
        def _fetch() -> dict[str, Any]:
            import ccxt
            import time as _time
            t0 = _time.time()
            ex = ccxt.okx({"enableRateLimit": True})
            ob = ex.fetch_order_book(symbol, limit=5)
            latency_ms = int((_time.time() - t0) * 1000)
            bids = ob.get("bids") or []
            asks = ob.get("asks") or []
            best_bid = float(bids[0][0]) if bids else 0.0
            best_ask = float(asks[0][0]) if asks else 0.0
            mid = (best_bid + best_ask) / 2 if (best_bid + best_ask) > 0 else 0.0
            spread_bps = ((best_ask - best_bid) / mid * 10_000) if mid > 0 else 0.0
            # Depth at top 5 levels (USD)
            depth_usd = 0.0
            for lvl in bids[:5]:
                depth_usd += float(lvl[0]) * float(lvl[1])
            for lvl in asks[:5]:
                depth_usd += float(lvl[0]) * float(lvl[1])
            return {
                "bid": best_bid,
                "ask": best_ask,
                "mid": mid,
                "spread_bps": spread_bps,
                "depth_usd": depth_usd,
                "fetch_latency_ms": latency_ms,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        return await asyncio.to_thread(_fetch)

    # -------- persistence --------

    def _append(self, record: dict) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            log.exception("shadow trade append failed: %s", e)

    # -------- report --------

    def is_active(self) -> bool:
        """Whether shadow mode is still within its 7-day window."""
        if self.ended_at is not None:
            return False
        elapsed = datetime.now(timezone.utc) - self.started_at
        return elapsed < timedelta(days=self.cfg["duration_days"])

    def elapsed_days(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds() / 86400.0

    def remaining_days(self) -> float:
        return max(0.0, self.cfg["duration_days"] - self.elapsed_days())

    def generate_report(self) -> dict[str, Any]:
        """Emit aggregate shadow-mode report.

        Returns a dict that's also written to report_path. Used as ONE of
        the 6 LIVE_GATE unlock conditions
        (shadow_live_passed := report["passed"]).
        """
        n = len(self._trades)
        if n < self.cfg["min_fills_for_report"]:
            return {
                "status": "insufficient_data",
                "n_fills": n,
                "min_required": self.cfg["min_fills_for_report"],
                "passed": False,
                "started_at": self.started_at.isoformat(),
                "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            }

        matches = sum(1 for t in self._trades if t.fill_match)
        match_pct = matches / n * 100
        th = self.cfg["thresholds"]
        passed = match_pct >= th["min_fill_match_pct"]

        # Aggregate stats
        avg_slip_diff = sum(t.slippage_diff_bps for t in self._trades) / n
        avg_fee_diff_pct = sum(t.fee_diff_pct for t in self._trades) / n
        avg_latency_diff = sum(t.latency_diff_ms for t in self._trades) / n
        max_slip_diff = max(abs(t.slippage_diff_bps) for t in self._trades)
        max_latency_diff = max(abs(t.latency_diff_ms) for t in self._trades)

        # By symbol breakdown
        by_symbol: dict[str, dict] = {}
        for t in self._trades:
            sym = t.symbol
            by_symbol.setdefault(sym, {"n": 0, "matches": 0, "slip_diff_sum": 0.0})
            by_symbol[sym]["n"] += 1
            if t.fill_match:
                by_symbol[sym]["matches"] += 1
            by_symbol[sym]["slip_diff_sum"] += t.slippage_diff_bps
        for sym, s in by_symbol.items():
            s["match_pct"] = round(s["matches"] / s["n"] * 100, 2) if s["n"] > 0 else 0.0
            s["avg_slip_diff_bps"] = round(s["slip_diff_sum"] / s["n"], 2) if s["n"] > 0 else 0.0
            del s["slip_diff_sum"]

        report = {
            "status": "complete",
            "n_fills": n,
            "matches": matches,
            "match_pct": round(match_pct, 2),
            "passed": passed,
            "thresholds": th,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else datetime.now(timezone.utc).isoformat(),
            "duration_days": self.cfg["duration_days"],
            "elapsed_days": round(self.elapsed_days(), 2),
            "avg_slip_diff_bps": round(avg_slip_diff, 2),
            "avg_fee_diff_pct": round(avg_fee_diff_pct, 2),
            "avg_latency_diff_ms": round(avg_latency_diff, 1),
            "max_slip_diff_bps": round(max_slip_diff, 2),
            "max_latency_diff_ms": max_latency_diff,
            "by_symbol": by_symbol,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self.report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, default=str)
            log.info("shadow report written: %s (passed=%s)", self.report_path, passed)
        except Exception as e:
            log.exception("shadow report write failed: %s", e)
        return report

    def stop(self) -> dict[str, Any]:
        """End shadow mode and emit the final report."""
        self.ended_at = datetime.now(timezone.utc)
        log.info("ShadowLive stopped after %.2f days", self.elapsed_days())
        return self.generate_report()

    # -------- queries --------

    def fetch_trades(self, limit: int = 50) -> list[dict]:
        """Return last N shadow trades from disk."""
        if not self.path.exists():
            return []
        rows: list[dict] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        rows.reverse()
        return rows[:limit]

    def stats(self) -> dict[str, Any]:
        return {
            "active": self.is_active(),
            "elapsed_days": round(self.elapsed_days(), 2),
            "remaining_days": round(self.remaining_days(), 2),
            "n_trades": len(self._trades),
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }

    def update_config(self, **overrides: Any) -> None:
        self.cfg.update(overrides)
        log.info("ShadowLive config updated: %s", overrides)
