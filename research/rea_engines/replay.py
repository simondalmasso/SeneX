"""Deterministic historical event replay for the isolated REA lab."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields, replace
from decimal import Decimal
from typing import Any, Optional, Protocol, Sequence

from .accounting import Ledger
from .contracts import (
    Candle, CancelIntent, CostProfile, Fill, OrderIntent, PendingOrder,
    PortfolioSnapshot, Rejection, as_decimal,
)


ZERO = Decimal("0")
BPS_DENOMINATOR = Decimal("10000")
FILL_POLICY_ID = "ohlcv-next-open-resting-limit-v1"
DEFAULT_ASSUMPTIONS = (
    "Market intents submitted after a closed candle are eligible no earlier than the next candle open.",
    "Limit orders are eligible only on candles after submission and require the candle range to touch the limit.",
    "OHLCV does not reveal queue position or intrabar price path; fills are deterministic model assumptions.",
    "Limit fills do not receive an additional spread or slippage surcharge beyond the limit-or-better price.",
    "Results are historical-paper research only; they do not establish a profitable edge.",
)


class ReplayError(ValueError):
    """Raised when replay inputs or strategy behavior violate the contract."""


@dataclass(frozen=True)
class ReplayConfig:
    starting_cash: Decimal
    max_inventory: Decimal
    max_fill_qty: Decimal
    costs: CostProfile = CostProfile()
    default_order_ttl: Optional[int] = None
    config_id: str = "rea-replay-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "starting_cash", as_decimal(self.starting_cash, "starting_cash", positive=True))
        object.__setattr__(self, "max_inventory", as_decimal(self.max_inventory, "max_inventory", positive=True))
        object.__setattr__(self, "max_fill_qty", as_decimal(self.max_fill_qty, "max_fill_qty", positive=True))
        if not isinstance(self.costs, CostProfile):
            raise ReplayError("costs must be a CostProfile")
        if self.default_order_ttl is not None:
            if isinstance(self.default_order_ttl, bool) or not isinstance(self.default_order_ttl, int):
                raise ReplayError("default_order_ttl must be a positive integer or None")
            if self.default_order_ttl <= 0:
                raise ReplayError("default_order_ttl must be a positive integer or None")
        if not isinstance(self.config_id, str) or not self.config_id.strip():
            raise ReplayError("config_id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "starting_cash": str(self.starting_cash),
            "max_inventory": str(self.max_inventory),
            "max_fill_qty": str(self.max_fill_qty),
            "default_order_ttl": self.default_order_ttl,
            "costs": {
                "profile_id": self.costs.profile_id,
                "fee_bps": str(self.costs.fee_bps),
                "spread_bps": str(self.costs.spread_bps),
                "slippage_bps": str(self.costs.slippage_bps),
            },
        }


@dataclass(frozen=True)
class RunReport:
    strategy_id: str
    source_sha256: str
    source_rows: int
    timestamp_start: str
    timestamp_end: str
    config: ReplayConfig
    fills: tuple[Fill, ...]
    rejections: tuple[Rejection, ...]
    expired_orders: tuple[dict[str, str], ...]
    cancelled_orders: tuple[dict[str, str], ...]
    pending_orders: tuple[PendingOrder, ...]
    equity_curve: tuple[dict[str, str], ...]
    metrics: dict[str, str]
    fill_policy_id: str = FILL_POLICY_ID
    assumptions: tuple[str, ...] = DEFAULT_ASSUMPTIONS
    mode: str = "historical-paper"
    network_calls: int = 0
    live_orders_submitted: int = 0
    edge: str = "UNPROVEN"
    report_sha256: str = ""

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "status": "completed",
            "strategy_id": self.strategy_id,
            "mode": self.mode,
            "source": {
                "sha256": self.source_sha256,
                "rows": self.source_rows,
                "timestamp_start": self.timestamp_start,
                "timestamp_end": self.timestamp_end,
            },
            "config": self.config.to_dict(),
            "fill_policy_id": self.fill_policy_id,
            "assumptions": list(self.assumptions),
            "fills": [_dataclass_to_json(fill) for fill in self.fills],
            "rejections": [_dataclass_to_json(item) for item in self.rejections],
            "expired_orders": list(self.expired_orders),
            "cancelled_orders": list(self.cancelled_orders),
            "pending_orders": [_dataclass_to_json(item) for item in self.pending_orders],
            "equity_curve": list(self.equity_curve),
            "metrics": dict(self.metrics),
            "safety": {
                "network_calls": self.network_calls,
                "live_orders_submitted": self.live_orders_submitted,
                "EDGE": self.edge,
            },
        }
        if include_digest:
            payload["report_sha256"] = self.report_sha256
        return payload


class Strategy(Protocol):
    strategy_id: str

    def on_candle(
        self, candle: Candle, portfolio_snapshot: PortfolioSnapshot,
        open_orders: Sequence[PendingOrder],
    ) -> Sequence[OrderIntent | CancelIntent]:
        ...


def _dataclass_to_json(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields(value):
        item = getattr(value, field.name)
        if isinstance(item, Decimal):
            result[field.name] = str(item)
        else:
            result[field.name] = item
    return result


def canonical_json(payload: Any) -> str:
    def normalize(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return normalize(value.to_dict())
        if hasattr(value, "__dataclass_fields__"):
            return normalize(asdict(value))
        if isinstance(value, dict):
            return {str(key): normalize(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [normalize(item) for item in value]
        return value
    return json.dumps(normalize(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _candle_payload(candle: Candle) -> dict[str, str]:
    return {
        "timestamp": candle.timestamp,
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
    }


class Replay:
    """Run a causal, deterministic replay over already-loaded local candles."""

    def run(
        self, candles: Sequence[Candle], strategy: Strategy, config: ReplayConfig,
        source_sha256: Optional[str] = None,
    ) -> RunReport:
        bars = tuple(candles)
        self._validate_inputs(bars, strategy, config)
        if source_sha256 is None:
            source_digest = canonical_sha256([_candle_payload(bar) for bar in bars])
        else:
            source_digest = str(source_sha256).lower()
            if len(source_digest) != 64 or any(ch not in "0123456789abcdef" for ch in source_digest):
                raise ReplayError("source_sha256 must be a 64-character hexadecimal digest")

        ledger = Ledger(config.starting_cash, config.max_inventory, config.max_fill_qty)
        pending: list[PendingOrder] = []
        fills: list[Fill] = []
        rejections: list[Rejection] = []
        expired_orders: list[dict[str, str]] = []
        cancelled_orders: list[dict[str, str]] = []
        equity_curve: list[dict[str, str]] = []
        order_sequence = 0
        peak_equity = config.starting_cash
        max_drawdown_pct = ZERO
        bars_in_market = 0
        strategy_id = str(getattr(strategy, "strategy_id", type(strategy).__name__))

        for index, candle in enumerate(bars):
            still_pending: list[PendingOrder] = []
            for order in pending:
                if order.submitted_index >= index:
                    still_pending.append(order)
                    continue

                fill_price: Optional[Decimal] = None
                if order.order_type == "market":
                    fill_price = candle.open
                elif order.side == "buy" and candle.low <= order.limit_price:
                    fill_price = candle.open if candle.open < order.limit_price else order.limit_price
                elif order.side == "sell" and candle.high >= order.limit_price:
                    fill_price = candle.open if candle.open > order.limit_price else order.limit_price

                if fill_price is not None:
                    try:
                        fill = self._execute_fill(order, candle, fill_price, ledger, config.costs)
                    except ValueError as exc:
                        rejections.append(Rejection(
                            order_id=order.order_id, timestamp=candle.timestamp,
                            reason=str(exc), side=order.side, quantity=order.quantity, tag=order.tag,
                        ))
                    else:
                        fills.append(fill)
                    continue

                waited = index - order.submitted_index
                updated = replace(order, bars_waited=waited)
                if order.expires_after_bars is not None and waited >= order.expires_after_bars:
                    expired_orders.append({
                        "order_id": order.order_id,
                        "timestamp": candle.timestamp,
                        "reason": "ttl_elapsed_without_fill",
                        "bars_waited": str(waited),
                        "tag": order.tag,
                    })
                else:
                    still_pending.append(updated)

            pending = still_pending
            snapshot = ledger.snapshot(candle.close)
            peak_equity = max(peak_equity, snapshot.equity)
            drawdown_pct = ((peak_equity - snapshot.equity) / peak_equity * Decimal("100")) if peak_equity > ZERO else ZERO
            max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)
            if snapshot.inventory_qty > ZERO:
                bars_in_market += 1
            equity_curve.append({
                "timestamp": candle.timestamp,
                "close": str(candle.close),
                "cash": str(snapshot.cash),
                "inventory_qty": str(snapshot.inventory_qty),
                "equity": str(snapshot.equity),
                "drawdown_pct": str(drawdown_pct),
            })

            try:
                intents = strategy.on_candle(candle, snapshot, tuple(pending))
            except Exception as exc:
                raise ReplayError(f"strategy failed at {candle.timestamp}: {type(exc).__name__}: {exc}") from exc
            if intents is None:
                intents = ()
            try:
                intents = tuple(intents)
            except TypeError:
                raise ReplayError("strategy must return a sequence of OrderIntent objects") from None
            for intent in intents:
                if isinstance(intent, CancelIntent):
                    match = next((item for item in pending if item.order_id == intent.order_id), None)
                    if match is None:
                        rejections.append(Rejection(
                            order_id=intent.order_id, timestamp=candle.timestamp,
                            reason="cancel_target_not_open", side="cancel",
                            quantity=ZERO, tag=intent.tag,
                        ))
                    else:
                        pending = [item for item in pending if item.order_id != intent.order_id]
                        cancelled_orders.append({
                            "order_id": intent.order_id,
                            "timestamp": candle.timestamp,
                            "reason": "strategy_cancelled",
                            "tag": intent.tag or match.tag,
                        })
                    continue
                if not isinstance(intent, OrderIntent):
                    raise ReplayError("strategy returned a value other than OrderIntent or CancelIntent")
                order_sequence += 1
                ttl = intent.expires_after_bars
                if ttl is None:
                    ttl = config.default_order_ttl
                pending.append(PendingOrder(
                    order_id=f"{strategy_id}-{order_sequence:06d}",
                    side=intent.side,
                    order_type=intent.order_type,
                    quantity=intent.quantity,
                    limit_price=intent.limit_price,
                    submitted_index=index,
                    submitted_timestamp=candle.timestamp,
                    expires_after_bars=ttl,
                    tag=intent.tag,
                ))

        ending = ledger.snapshot(bars[-1].close)
        net_pnl = ending.equity - config.starting_cash
        return_pct = net_pnl / config.starting_cash * Decimal("100")
        exposure_pct = Decimal(bars_in_market) / Decimal(len(bars)) * Decimal("100")
        metrics = {
            "starting_cash": str(config.starting_cash),
            "ending_equity": str(ending.equity),
            "net_pnl": str(net_pnl),
            "return_pct": str(return_pct),
            "max_drawdown_pct": str(max_drawdown_pct),
            "cash": str(ending.cash),
            "inventory_qty": str(ending.inventory_qty),
            "average_entry_price": str(ending.average_entry_price),
            "realized_pnl": str(ending.realized_pnl),
            "unrealized_pnl": str(ending.unrealized_pnl),
            "total_fees": str(ending.total_fees),
            "total_spread_cost": str(ending.total_spread_cost),
            "total_slippage_cost": str(ending.total_slippage_cost),
            "turnover": str(ending.turnover),
            "fills": str(len(fills)),
            "rejections": str(len(rejections)),
            "expired_orders": str(len(expired_orders)),
            "pending_orders": str(len(pending)),
            "bars_in_market": str(bars_in_market),
            "time_in_market_pct": str(exposure_pct),
        }
        report = RunReport(
            strategy_id=strategy_id,
            source_sha256=source_digest,
            source_rows=len(bars),
            timestamp_start=bars[0].timestamp,
            timestamp_end=bars[-1].timestamp,
            config=config,
            fills=tuple(fills),
            rejections=tuple(rejections),
            expired_orders=tuple(expired_orders),
            cancelled_orders=tuple(cancelled_orders),
            pending_orders=tuple(pending),
            equity_curve=tuple(equity_curve),
            metrics=metrics,
        )
        digest = canonical_sha256(report.to_dict(include_digest=False))
        return replace(report, report_sha256=digest)

    @staticmethod
    def _validate_inputs(candles: tuple[Candle, ...], strategy: Strategy, config: ReplayConfig) -> None:
        if not candles:
            raise ReplayError("at least one candle is required")
        if not isinstance(config, ReplayConfig):
            raise ReplayError("config must be a ReplayConfig")
        if not callable(getattr(strategy, "on_candle", None)):
            raise ReplayError("strategy must implement on_candle")
        previous = None
        for index, candle in enumerate(candles):
            if not isinstance(candle, Candle):
                raise ReplayError(f"row {index} is not a Candle")
            if previous is not None and candle.timestamp <= previous:
                raise ReplayError("candle timestamps must be strictly increasing")
            previous = candle.timestamp

    @staticmethod
    def _execute_fill(
        order: PendingOrder, candle: Candle, price: Decimal, ledger: Ledger, costs: CostProfile,
    ) -> Fill:
        quantity = order.quantity
        notional = quantity * price
        fee = notional * costs.fee_bps / BPS_DENOMINATOR
        if order.order_type == "market":
            spread_cost = notional * costs.spread_bps / BPS_DENOMINATOR / Decimal("2")
            slippage_cost = notional * costs.slippage_bps / BPS_DENOMINATOR
        else:
            spread_cost = ZERO
            slippage_cost = ZERO
        if order.side == "buy":
            operation = ledger.buy(
                quantity=quantity, price=price, fee=fee,
                spread_cost=spread_cost, slippage_cost=slippage_cost,
            )
        else:
            operation = ledger.sell(
                quantity=quantity, price=price, fee=fee,
                spread_cost=spread_cost, slippage_cost=slippage_cost,
            )
        return Fill(
            order_id=order.order_id,
            timestamp=candle.timestamp,
            side=order.side,
            order_type=order.order_type,
            quantity=operation.quantity,
            price=operation.price,
            notional=operation.notional,
            fee=operation.fee,
            spread_cost=operation.spread_cost,
            slippage_cost=operation.slippage_cost,
            realized_pnl=operation.realized_pnl,
            tag=order.tag,
        )
