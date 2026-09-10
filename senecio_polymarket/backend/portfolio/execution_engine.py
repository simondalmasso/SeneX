"""
SENECIO ORACLE — ACT XXV: ExecutionEngine (priority 3)
======================================================

Manages the full order lifecycle: submit → partial fills → retry → cancel/
replace → fill complete → exit. Records every state transition to the
TradeJournal's execution audit log.

Responsibilities (per ACT-XXV spec):
  - order lifecycle       : NEW → SUBMITTED → PARTIAL_FILL → FILLED | CANCELED | REJECTED
  - partial fills         : track filled_qty vs ordered_qty; retry residual
  - slippage estimation   : model impact using book depth + order size
  - retry logic           : on transient failure (timeout, depth), resubmit
                            up to max_retries with revised price
  - cancel/replace        : replace a working order with a new price/qty
  - execution audit       : every state change recorded as audit event

Paper mode (default):
  - No real orders are sent. The engine SIMULATES orderbook interaction
    using a stochastic model parameterized by:
      * base_slippage_bps + rng_slippage_bps (entry slippage)
      * book_depth_pct (controls partial-fill ratio)
      * latency_ms_min..max (simulated ack latency)
  - The "real book" adapter (ShadowLive) records what would have happened
    against a real exchange snapshot, for comparison.

Live mode (locked under LIVE_GATE):
  - When LIVE_GATE.unlock() is called (all 6 conditions met), the engine
    switches to a real ccxt-based adapter. Until then, allow_live=False
    and all submit_live_order() calls raise RuntimeError.

Inputs:
  - TradeProposal (from PortfolioEngine, approved by RiskKernel)
  - RiskDecision (size_scale applied before submit)
  - Optional Orderbook snapshot (for realistic partial-fill modeling)

Outputs:
  - Fill events (one per partial fill, accumulated into a Position)
  - Exit events (when stop/target/time-stop hit)
  - Audit events (every state transition)
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

log = logging.getLogger("senecio.execution_engine")


# -------------------- config --------------------

DEFAULTS: dict[str, Any] = {
    # Capital mode
    "allow_live":                 False,   # LIVE_GATE-locked; never auto-flip
    "trade_mode":                 "PAPER",
    # Slippage model
    "base_slippage_bps":          2.0,
    "rng_slippage_bps":           4.0,
    "exit_slippage_bps":          3.0,
    # Latency model (ms)
    "latency_ms_min":             50,
    "latency_ms_max":             300,
    # Partial-fill model
    "min_fill_pct":               0.30,    # even with thin book, fill at least 30%
    "book_depth_assumed_usd":     5_000.0, # if no book snapshot, assume $5k depth
    # Retry
    "max_retries":                2,
    "retry_backoff_ms":           250,
    "retry_price_improvement_bps": 1.0,    # each retry improves limit price by 1 bps
    # Fees (maker/taker)
    "taker_fee_bps":              5.0,     # 0.05% taker (OKX spot)
    "maker_fee_bps":              2.0,     # 0.02% maker
    "use_taker_by_default":       True,    # we submit marketable limits → taker
    # Time-stop
    "default_time_stop_minutes":  60,
    # Position tracking
    "starting_cash":              10_000.0,
}


class OrderStatus(str, Enum):
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    REPLACED = "REPLACED"


class ExitReason(str, Enum):
    STOP = "STOP"
    TARGET = "TARGET"
    TIME_STOP = "TIME_STOP"
    MANUAL = "MANUAL"
    KILL_SWITCH = "KILL_SWITCH"


# -------------------- data classes --------------------

@dataclass
class Order:
    """Working order — evolves through lifecycle states."""
    order_id: str
    client_order_id: str
    symbol: str
    side: str                          # "BUY" | "SELL"
    direction: str                     # "LONG" | "SHORT" (for journaling)
    ordered_qty: float
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    limit_price: float = 0.0
    status: str = OrderStatus.NEW.value
    retries: int = 0
    created_at: str = ""
    last_update_at: str = ""
    proposal_id: Optional[str | int] = None
    audit_trail: list[dict] = field(default_factory=list)

    def remaining_qty(self) -> float:
        return max(0.0, self.ordered_qty - self.filled_qty)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Fill:
    """A single fill (one order may have multiple partial fills)."""
    fill_id: str
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    slippage_bps: float
    latency_ms: int
    fee_usd: float
    ts: str
    is_partial: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Position:
    """An open position accumulated from one or more fills."""
    position_id: str
    symbol: str
    direction: str                     # "LONG" | "SHORT"
    qty: float
    avg_entry_price: float
    entry_ts: str
    stop_price: float
    target_price: float
    time_stop_minutes: int = 60
    status: str = "OPEN"               # OPEN | CLOSED
    exit_price: float = 0.0
    exit_ts: str = ""
    exit_reason: str = ""
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    # MAE/MFE (Maximum Adverse/Favorable Excursion)
    mae_price: float = 0.0             # worst price against the position while open
    mfe_price: float = 0.0             # best price for the position while open
    # Source provenance
    proposal_id: Optional[str | int] = None
    order_ids: list[str] = field(default_factory=list)
    audit_trail: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -------------------- ExecutionEngine --------------------

class ExecutionEngine:
    """Order lifecycle manager with paper-mode simulation.

    Usage:
        engine = ExecutionEngine(config=DEFAULTS)
        # on proposal + risk decision:
        order = engine.submit(proposal, decision, last_price=1700.0)
        fills = engine.poll(order)  # simulates one round of fills
        # on each market tick:
        exits = engine.check_exits(tick_price=1710.0, tick_ts=now_iso, symbol="ETH/USDT")
    """

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self._rng = random.Random(7)
        self.orders: dict[str, Order] = {}              # order_id → Order
        self.positions: dict[str, Position] = {}        # symbol → Position (one open per sym)
        self.closed_positions: list[Position] = []
        self.cash: float = self.cfg["starting_cash"]
        self.starting_cash: float = self.cfg["starting_cash"]
        self.audit_log: list[dict] = []                  # every state transition
        self._audit_listener = None                      # optional callback for journal
        # ACT-XXVI: high-fidelity fill simulator (HftBacktest-style L2 walk +
        # queue position + Almgren-Chriss impact). Used when a BookSnapshot
        # is supplied to submit(); otherwise falls back to legacy stochastic.
        self.fill_simulator = None
        try:
            from .execution_fidelity import FillSimulator
            self.fill_simulator = FillSimulator(config=self.cfg)
        except Exception as e:
            log.warning("FillSimulator unavailable (degraded mode): %s", e)
        log.info(
            "ExecutionEngine init: mode=%s allow_live=%s cash=$%.0f fill_simulator=%s",
            self.cfg["trade_mode"], self.cfg["allow_live"], self.cash,
            "on" if self.fill_simulator else "off",
        )

    # -------- audit listener --------

    def set_audit_listener(self, callback) -> None:
        """Register a callback invoked on every state transition.

        The TradeJournal uses this to persist fills + exits.
        Signature: callback(event: dict) -> None
        """
        self._audit_listener = callback

    def _emit_audit(self, event: dict) -> None:
        event["ts"] = event.get("ts") or datetime.now(timezone.utc).isoformat()
        self.audit_log.append(event)
        if len(self.audit_log) > 1000:                   # cap memory
            self.audit_log = self.audit_log[-500:]
        if self._audit_listener:
            try:
                self._audit_listener(event)
            except Exception as e:
                log.warning("audit listener error: %s", e)

    # -------- order lifecycle --------

    async def submit(
        self,
        proposal: Any,
        decision: Any,
        last_price: float,
        book_depth_usd: Optional[float] = None,
        book_snapshot: Any = None,                # ACT-XXVI: BookSnapshot for L2 fill
        toxic_flow_score: Optional[float] = None, # ACT-XXVI: from MicrostructureIntelligence
    ) -> Order:
        """Submit a new order from an approved proposal.

        Args:
            proposal: TradeProposal (must have .to_dict())
            decision: RiskDecision (provides size_scale)
            last_price: current market mid-price
            book_depth_usd: optional orderbook depth for partial-fill modeling (legacy)
            book_snapshot: optional L2 BookSnapshot (ACT-XXVI high-fidelity path)
            toxic_flow_score: optional 0..1 toxic-flow indicator (adverse selection)
        """
        if self.cfg["allow_live"]:
            raise RuntimeError(
                "allow_live=True requires explicit LIVE_GATE unlock + adapter wiring"
            )

        p = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal)
        d = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)

        size_scale = float(d.get("size_scale", 1.0))
        ordered_qty = float(p.get("size_qty", 0)) * size_scale
        if ordered_qty <= 0:
            return self._make_rejected_order(p, "size_qty=0 after scale")

        side = "BUY" if p.get("direction") == "LONG" else "SELL"
        # Marketable limit: limit at last_price ± 5 bps (aggressive)
        slip_component = self._rng.uniform(
            self.cfg["base_slippage_bps"],
            self.cfg["base_slippage_bps"] + self.cfg["rng_slippage_bps"],
        )
        if side == "BUY":
            limit_price = last_price * (1 + slip_component / 10_000)
        else:
            limit_price = last_price * (1 - slip_component / 10_000)

        order = Order(
            order_id=self._new_id("ord"),
            client_order_id=self._new_id("cid"),
            symbol=p.get("symbol", ""),
            side=side,
            direction=p.get("direction", "LONG"),
            ordered_qty=round(ordered_qty, 8),
            filled_qty=0.0,
            avg_fill_price=0.0,
            limit_price=round(limit_price, 6),
            status=OrderStatus.NEW.value,
            created_at=datetime.now(timezone.utc).isoformat(),
            last_update_at=datetime.now(timezone.utc).isoformat(),
            proposal_id=p.get("prediction_id"),
            audit_trail=[{
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "NEW",
                "ordered_qty": ordered_qty,
                "limit_price": limit_price,
                "size_scale": size_scale,
            }],
        )
        self.orders[order.order_id] = order
        self._emit_audit({"event": "ORDER_NEW", "order": order.to_dict()})

        # Transition: NEW → SUBMITTED
        order.status = OrderStatus.SUBMITTED.value
        self._emit_audit({"event": "ORDER_SUBMITTED", "order_id": order.order_id})

        # Try to fill (with retry logic).
        # ACT-XXVI: prefer high-fidelity FillSimulator when a BookSnapshot is
        # provided; otherwise fall back to legacy stochastic.
        if self.fill_simulator is not None and book_snapshot is not None:
            await self._try_fill_high_fidelity(
                order=order,
                last_price=last_price,
                book_snapshot=book_snapshot,
                toxic_flow_score=toxic_flow_score,
            )
        else:
            await self._try_fill(order, last_price=last_price, book_depth_usd=book_depth_usd)
        return order

    async def _try_fill(
        self,
        order: Order,
        last_price: float,
        book_depth_usd: Optional[float] = None,
    ) -> None:
        """Attempt to fill an order with retry on partial fills."""
        for attempt in range(self.cfg["max_retries"] + 1):
            # Simulated latency
            latency_ms = self._rng.randint(
                self.cfg["latency_ms_min"], self.cfg["latency_ms_max"]
            )
            await asyncio.sleep(latency_ms / 1000.0)

            # Partial-fill modeling: ratio = min(1, book_depth / notional_remaining)
            remaining = order.remaining_qty()
            if remaining <= 0:
                break
            notional = remaining * last_price
            depth = book_depth_usd or self.cfg["book_depth_assumed_usd"]
            fill_pct = min(1.0, depth / notional) if notional > 0 else 0
            fill_pct = max(self.cfg["min_fill_pct"], fill_pct)
            # Add stochastic noise so retries actually help
            fill_pct *= self._rng.uniform(0.85, 1.0)
            fill_pct = min(1.0, fill_pct)
            fill_qty = remaining * fill_pct
            if fill_qty <= 0:
                continue

            # Slippage for this fill
            slip_bps = self._rng.uniform(
                self.cfg["base_slippage_bps"],
                self.cfg["base_slippage_bps"] + self.cfg["rng_slippage_bps"],
            )
            if order.side == "BUY":
                fill_price = last_price * (1 + slip_bps / 10_000)
            else:
                fill_price = last_price * (1 - slip_bps / 10_000)

            fee_bps = self.cfg["taker_fee_bps"] if self.cfg["use_taker_by_default"] else self.cfg["maker_fee_bps"]
            fee_usd = fill_qty * fill_price * fee_bps / 10_000

            # Update order's VWAP
            old_qty = order.filled_qty
            new_qty = old_qty + fill_qty
            order.avg_fill_price = (
                (order.avg_fill_price * old_qty + fill_price * fill_qty) / new_qty
                if new_qty > 0 else fill_price
            )
            order.filled_qty = new_qty
            fill = Fill(
                fill_id=self._new_id("fl"),
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                qty=round(fill_qty, 8),
                price=round(fill_price, 6),
                slippage_bps=round(slip_bps, 2),
                latency_ms=latency_ms,
                fee_usd=round(fee_usd, 4),
                ts=datetime.now(timezone.utc).isoformat(),
                is_partial=(order.remaining_qty() > 0),
            )
            self._emit_audit({
                "event": "FILL",
                "fill": fill.to_dict(),
                "order_id": order.order_id,
                "remaining_after": order.remaining_qty(),
            })

            if order.remaining_qty() <= 1e-9:
                order.status = OrderStatus.FILLED.value
                self._emit_audit({
                    "event": "ORDER_FILLED",
                    "order_id": order.order_id,
                    "avg_fill_price": order.avg_fill_price,
                })
                # Convert fills → Position
                self._open_or_add_position(order)
                return
            else:
                order.status = OrderStatus.PARTIAL_FILL.value
                order.retries = attempt + 1
                self._emit_audit({
                    "event": "ORDER_PARTIAL_FILL",
                    "order_id": order.order_id,
                    "filled_qty": order.filled_qty,
                    "remaining": order.remaining_qty(),
                    "attempt": attempt + 1,
                })
                # Backoff + price improvement before retry
                await asyncio.sleep(self.cfg["retry_backoff_ms"] / 1000.0)
                # Improve limit price by retry_price_improvement_bps toward side
                improvement = self.cfg["retry_price_improvement_bps"] / 10_000
                if order.side == "BUY":
                    order.limit_price *= (1 + improvement)
                else:
                    order.limit_price *= (1 - improvement)

        # Exhausted retries — cancel residual
        if order.remaining_qty() > 0 and order.status != OrderStatus.FILLED.value:
            await self.cancel(order.order_id, reason="max_retries_exhausted")
            # If we got at least one partial fill, open a smaller position
            if order.filled_qty > 0:
                self._open_or_add_position(order)

    async def _try_fill_high_fidelity(
        self,
        order: Order,
        last_price: float,
        book_snapshot: Any,
        toxic_flow_score: Optional[float] = None,
    ) -> None:
        """ACT-XXVI: high-fidelity fill path using FillSimulator (L2 walk + queue + impact).

        This is a single-shot simulator — no retry loop. The FillSimulator
        already accounts for adverse selection, market impact, and queue
        position. The resulting Fill is recorded via the same audit path
        as the legacy _try_fill so TradeJournal + ShadowLive see the same
        event shape.
        """
        if self.fill_simulator is None:
            # Degraded — fall back
            await self._try_fill(order, last_price=last_price)
            return

        notional_usd = order.remaining_qty() * last_price
        if notional_usd <= 0:
            return

        # Run the simulator (marketable limit → taker)
        estimate = self.fill_simulator.simulate_fill(
            side=order.side,
            notional_usd=notional_usd,
            book=book_snapshot,
            is_marketable=True,
            toxic_flow_score=toxic_flow_score,
        )

        # Simulated ack latency
        await asyncio.sleep(estimate.expected_latency_ms / 1000.0)

        if estimate.expected_qty <= 0:
            # No fill (queue position didn't reach front, or book was empty)
            order.status = OrderStatus.CANCELED.value
            order.last_update_at = datetime.now(timezone.utc).isoformat()
            self._emit_audit({
                "event": "ORDER_CANCELED",
                "order_id": order.order_id,
                "reason": "no_fill_from_simulator",
                "model": estimate.model,
                "queue_fill_prob": estimate.queue_fill_prob,
            })
            return

        # Apply the simulated fill
        fill_qty = estimate.expected_qty
        old_qty = order.filled_qty
        new_qty = old_qty + fill_qty
        order.avg_fill_price = (
            (order.avg_fill_price * old_qty + estimate.expected_vwap_price * fill_qty) / new_qty
            if new_qty > 0 else estimate.expected_vwap_price
        )
        order.filled_qty = new_qty

        fill = Fill(
            fill_id=self._new_id("fl"),
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            qty=round(fill_qty, 8),
            price=round(estimate.expected_vwap_price, 6),
            slippage_bps=estimate.expected_slippage_bps,
            latency_ms=estimate.expected_latency_ms,
            fee_usd=estimate.expected_fee_usd,
            ts=datetime.now(timezone.utc).isoformat(),
            is_partial=estimate.is_partial,
        )
        self._emit_audit({
            "event": "FILL",
            "fill": fill.to_dict(),
            "order_id": order.order_id,
            "remaining_after": order.remaining_qty(),
            "fidelity_model": estimate.model,
            "market_impact_bps": estimate.expected_market_impact_bps,
            "adverse_selection_mult": estimate.adverse_selection_mult,
            "queue_position": estimate.queue_position,
            "levels_consumed": estimate.levels_consumed,
            "book_present": estimate.book_present,
        })

        if order.remaining_qty() <= 1e-9:
            order.status = OrderStatus.FILLED.value
            self._emit_audit({
                "event": "ORDER_FILLED",
                "order_id": order.order_id,
                "avg_fill_price": order.avg_fill_price,
                "fidelity_model": estimate.model,
            })
            self._open_or_add_position(order)
        else:
            # Partial fill — cancel the residual (no retry in high-fidelity mode
            # because the simulator already accounts for what the book can give).
            order.status = OrderStatus.PARTIAL_FILL.value
            self._emit_audit({
                "event": "ORDER_PARTIAL_FILL",
                "order_id": order.order_id,
                "filled_qty": order.filled_qty,
                "remaining": order.remaining_qty(),
                "fidelity_model": estimate.model,
            })
            await self.cancel(order.order_id, reason="high_fidelity_partial_residual")
            if order.filled_qty > 0:
                self._open_or_add_position(order)

    async def cancel(self, order_id: str, reason: str = "") -> bool:
        """Cancel a working order."""
        order = self.orders.get(order_id)
        if not order or order.status in (OrderStatus.FILLED.value, OrderStatus.CANCELED.value):
            return False
        order.status = OrderStatus.CANCELED.value
        order.last_update_at = datetime.now(timezone.utc).isoformat()
        self._emit_audit({
            "event": "ORDER_CANCELED",
            "order_id": order_id,
            "reason": reason,
            "filled_qty": order.filled_qty,
            "remaining": order.remaining_qty(),
        })
        log.info("order %s canceled: %s (filled=%.6f remaining=%.6f)",
                 order_id, reason, order.filled_qty, order.remaining_qty())
        return True

    async def cancel_replace(
        self,
        order_id: str,
        new_limit_price: Optional[float] = None,
        new_qty: Optional[float] = None,
    ) -> Optional[Order]:
        """Cancel a working order and submit a replacement.

        Per ACT-XXV directive: "cancel/replace" — typically used when an order
        is not filling within an acceptable latency and we want to improve the
        price or adjust the size.
        """
        old = self.orders.get(order_id)
        if not old or old.status not in (OrderStatus.SUBMITTED.value, OrderStatus.PARTIAL_FILL.value):
            return None
        await self.cancel(order_id, reason="cancel_replace")
        # Build a synthetic proposal+decision for the replacement
        remaining = old.remaining_qty() if new_qty is None else new_qty
        # Use a stub proposal (no need to re-route through PortfolioEngine)
        from .portfolio_engine import TradeProposal
        from .risk_kernel import RiskDecision
        stub_proposal = TradeProposal(
            symbol=old.symbol,
            direction=old.direction,
            size_usd=remaining * (new_limit_price or old.limit_price),
            size_qty=remaining,
            entry_price=new_limit_price or old.limit_price,
            stop_price=0.0,             # not used by submit()
            target_price=0.0,
            risk_per_unit=0.0,
            risk_usd=0.0,
            confidence=1.0,             # already-approved; bypass confidence check
            ev=0.0,
            proposal_id=old.proposal_id,
            rationale="cancel_replace",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        stub_decision = RiskDecision(
            approved=True,
            reason="cancel_replace",
            size_scale=1.0,
            proposal_id=old.proposal_id,
        )
        new_order = await self.submit(
            stub_proposal, stub_decision,
            last_price=new_limit_price or old.limit_price,
        )
        self._emit_audit({
            "event": "ORDER_CANCEL_REPLACE",
            "old_order_id": order_id,
            "new_order_id": new_order.order_id,
        })
        return new_order

    # -------- position management --------

    def _open_or_add_position(self, order: Order) -> None:
        """Convert a filled (or partially-filled) order into a Position."""
        if order.filled_qty <= 0:
            return
        # One open position per symbol — if there's already an OPEN one, skip
        # (the PortfolioEngine's max_per_symbol=1 should prevent this, but
        # we double-check here for safety).
        existing = self.positions.get(order.symbol)
        if existing and existing.status == "OPEN":
            log.warning(
                "skipping position open for %s — already OPEN (qty=%.6f)",
                order.symbol, existing.qty,
            )
            return

        pos = Position(
            position_id=self._new_id("pos"),
            symbol=order.symbol,
            direction=order.direction,
            qty=order.filled_qty,
            avg_entry_price=order.avg_fill_price,
            entry_ts=datetime.now(timezone.utc).isoformat(),
            stop_price=0.0,                # populated by caller via set_stop_target
            target_price=0.0,
            time_stop_minutes=self.cfg["default_time_stop_minutes"],
            fees_paid=0.0,
            proposal_id=order.proposal_id,
            order_ids=[order.order_id],
            audit_trail=list(order.audit_trail),
        )
        self.positions[order.symbol] = pos
        self.cash -= pos.qty * pos.avg_entry_price
        self._emit_audit({
            "event": "POSITION_OPEN",
            "position": pos.to_dict(),
        })
        log.info(
            "position OPEN: %s %s qty=%.6f entry=$%.4f cash=$%.2f",
            pos.symbol, pos.direction, pos.qty, pos.avg_entry_price, self.cash,
        )

    def set_stop_target(
        self,
        symbol: str,
        stop_price: float,
        target_price: float,
        time_stop_minutes: Optional[int] = None,
    ) -> bool:
        """Attach stop/target to an open position (called by the wiring layer)."""
        pos = self.positions.get(symbol)
        if not pos or pos.status != "OPEN":
            return False
        pos.stop_price = float(stop_price)
        pos.target_price = float(target_price)
        if time_stop_minutes is not None:
            pos.time_stop_minutes = int(time_stop_minutes)
        pos.mae_price = pos.avg_entry_price  # initialize to entry
        pos.mfe_price = pos.avg_entry_price
        self._emit_audit({
            "event": "POSITION_STOP_TARGET_SET",
            "position_id": pos.position_id,
            "stop": stop_price,
            "target": target_price,
            "time_stop_minutes": pos.time_stop_minutes,
        })
        return True

    def check_exits(
        self,
        symbol: str,
        tick_price: float,
        tick_ts: str,
        kill_switch_active: bool = False,
    ) -> list[dict]:
        """Check open positions against current tick; emit exits on trigger.

        Returns a list of exit-event dicts (one per closed position).
        """
        pos = self.positions.get(symbol)
        if not pos or pos.status != "OPEN":
            return []

        # Update MAE/MFE
        if pos.direction == "LONG":
            pos.mae_price = min(pos.mae_price or tick_price, tick_price)
            pos.mfe_price = max(pos.mfe_price or tick_price, tick_price)
        else:  # SHORT
            pos.mae_price = max(pos.mae_price or tick_price, tick_price)
            pos.mfe_price = min(pos.mfe_price or tick_price, tick_price)

        reason = None
        if kill_switch_active:
            reason = ExitReason.KILL_SWITCH
        elif pos.direction == "LONG":
            if tick_price <= pos.stop_price and pos.stop_price > 0:
                reason = ExitReason.STOP
            elif tick_price >= pos.target_price and pos.target_price > 0:
                reason = ExitReason.TARGET
        else:  # SHORT
            if tick_price >= pos.stop_price and pos.stop_price > 0:
                reason = ExitReason.STOP
            elif tick_price <= pos.target_price and pos.target_price > 0:
                reason = ExitReason.TARGET

        if reason is None:
            # Time-stop check
            try:
                entry_dt = datetime.fromisoformat(pos.entry_ts.replace("Z", "+00:00"))
                now_dt = datetime.fromisoformat(tick_ts.replace("Z", "+00:00"))
                if (now_dt - entry_dt).total_seconds() / 60 >= pos.time_stop_minutes:
                    reason = ExitReason.TIME_STOP
            except Exception:
                pass

        if reason is None:
            return []

        # Close the position
        exit_slip = self._rng.uniform(0, self.cfg["exit_slippage_bps"])
        if pos.direction == "LONG":
            # Selling → exit price below tick
            exit_price = tick_price * (1 - exit_slip / 10_000)
            realized_gross = (exit_price - pos.avg_entry_price) * pos.qty
        else:
            # Buying back → exit price above tick
            exit_price = tick_price * (1 + exit_slip / 10_000)
            realized_gross = (pos.avg_entry_price - exit_price) * pos.qty

        exit_fee = pos.qty * exit_price * self.cfg["taker_fee_bps"] / 10_000
        realized_pnl = realized_gross - exit_fee - pos.fees_paid
        pos.exit_price = exit_price
        pos.exit_ts = tick_ts
        pos.exit_reason = reason.value
        pos.status = "CLOSED"
        pos.realized_pnl = round(realized_pnl, 2)
        pos.fees_paid = round(pos.fees_paid + exit_fee, 4)

        # Cash adjustment: position returned to cash
        if pos.direction == "LONG":
            self.cash += pos.qty * exit_price
        else:
            # SHORT: we sold at entry, bought back at exit
            # entry: cash += qty * entry ; exit: cash -= qty * exit
            # Net effect on cash = qty * (entry - exit) - fees
            self.cash += pos.qty * (pos.avg_entry_price - exit_price)

        exit_event = {
            "event": "POSITION_EXIT",
            "position": pos.to_dict(),
            "exit_price": exit_price,
            "exit_reason": reason.value,
            "realized_pnl": pos.realized_pnl,
            "fees_paid": pos.fees_paid,
            "cash_after": round(self.cash, 2),
        }
        self._emit_audit(exit_event)
        self.closed_positions.append(pos)
        del self.positions[symbol]
        log.info(
            "position EXIT: %s %s reason=%s entry=$%.4f exit=$%.4f pnl=$%.2f cash=$%.2f",
            pos.symbol, pos.direction, reason.value, pos.avg_entry_price,
            exit_price, pos.realized_pnl, self.cash,
        )
        return [exit_event]

    # -------- introspection --------

    def get_open_positions(self) -> list[dict]:
        return [p.to_dict() for p in self.positions.values() if p.status == "OPEN"]

    def get_recent_exits(self, limit: int = 20) -> list[dict]:
        return [p.to_dict() for p in self.closed_positions[-limit:]]

    def get_audit_log(self, limit: int = 50) -> list[dict]:
        return list(self.audit_log[-limit:])

    def equity(self, last_prices: dict[str, float]) -> float:
        """Mark-to-market equity = cash + Σ(qty * last_price * sign)."""
        eq = self.cash
        for sym, pos in self.positions.items():
            if pos.status != "OPEN":
                continue
            last = last_prices.get(sym, pos.avg_entry_price)
            if pos.direction == "LONG":
                eq += pos.qty * last
            else:
                eq += pos.qty * (2 * pos.avg_entry_price - last)  # SHORT MTM
        return round(eq, 2)

    def stats(self) -> dict[str, Any]:
        return {
            "trade_mode": self.cfg["trade_mode"],
            "allow_live": self.cfg["allow_live"],
            "cash": round(self.cash, 2),
            "starting_cash": self.starting_cash,
            "open_positions": len(self.positions),
            "closed_positions": len(self.closed_positions),
            "total_orders": len(self.orders),
            "audit_log_size": len(self.audit_log),
            "fill_simulator": "on" if self.fill_simulator else "off",
        }

    def update_config(self, **overrides: Any) -> None:
        self.cfg.update(overrides)
        log.info("ExecutionEngine config updated: %s", overrides)

    # -------- live mode (LIVE_GATE-locked) --------

    def enable_live_mode(self, unlocked_by: str = "LIVE_GATE") -> None:
        """Switch to live order routing.

        Per ACT-XXV LIVE_GATE: only callable when all 6 unlock conditions
        are met. The LIVE_GATE evaluator (in main.py wiring layer) is the
        only caller; it sets this after verifying the gate.
        """
        self.cfg["allow_live"] = True
        self.cfg["trade_mode"] = "LIVE"
        log.warning("LIVE MODE ENABLED by %s — real orders will be placed", unlocked_by)
        self._emit_audit({
            "event": "LIVE_MODE_ENABLED",
            "unlocked_by": unlocked_by,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    # -------- helpers --------

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def _make_rejected_order(self, proposal_dict: dict, reason: str) -> Order:
        order = Order(
            order_id=self._new_id("ord"),
            client_order_id=self._new_id("cid"),
            symbol=proposal_dict.get("symbol", ""),
            side="BUY",
            direction=proposal_dict.get("direction", "LONG"),
            ordered_qty=0.0,
            status=OrderStatus.REJECTED.value,
            created_at=datetime.now(timezone.utc).isoformat(),
            last_update_at=datetime.now(timezone.utc).isoformat(),
            proposal_id=proposal_dict.get("prediction_id"),
            audit_trail=[{"event": "REJECTED", "reason": reason}],
        )
        self.orders[order.order_id] = order
        self._emit_audit({"event": "ORDER_REJECTED", "order_id": order.order_id, "reason": reason})
        return order
