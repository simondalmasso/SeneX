"""Clean-room spot strategy approximations for historical paper replay.

These are parameterized research models, not recovered proprietary engine code.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from .contracts import (
    Candle, CancelIntent, OrderIntent, PendingOrder, PortfolioSnapshot, as_decimal,
)


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class GridConfig:
    lower_price: Decimal
    upper_price: Decimal
    levels: int
    quote_per_order: Decimal
    take_profit_pct: Decimal = Decimal("0.01")
    config_id: str = "grid-cleanroom-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "lower_price", as_decimal(self.lower_price, "lower_price", positive=True))
        object.__setattr__(self, "upper_price", as_decimal(self.upper_price, "upper_price", positive=True))
        object.__setattr__(self, "quote_per_order", as_decimal(self.quote_per_order, "quote_per_order", positive=True))
        object.__setattr__(self, "take_profit_pct", as_decimal(self.take_profit_pct, "take_profit_pct", positive=True))
        if self.upper_price <= self.lower_price:
            raise ValueError("upper_price must be greater than lower_price")
        if isinstance(self.levels, bool) or not isinstance(self.levels, int) or self.levels < 2:
            raise ValueError("levels must be an integer >= 2")
        if not isinstance(self.config_id, str) or not self.config_id.strip():
            raise ValueError("config_id must be a non-empty string")


class GridStrategy:
    strategy_id = "grid-cleanroom-v1"

    def __init__(self, config: GridConfig) -> None:
        if not isinstance(config, GridConfig):
            raise ValueError("config must be a GridConfig")
        self.config = config

    def on_candle(
        self, candle: Candle, portfolio_snapshot: PortfolioSnapshot,
        open_orders: Sequence[PendingOrder],
    ) -> list[OrderIntent]:
        if open_orders:
            return []
        quantity = portfolio_snapshot.inventory_qty
        if quantity > ZERO:
            target = portfolio_snapshot.average_entry_price * (ONE + self.config.take_profit_pct)
            return [OrderIntent(
                side="sell", order_type="limit", quantity=quantity,
                limit_price=target, tag="grid-take-profit",
            )]
        step = (self.config.upper_price - self.config.lower_price) / Decimal(self.config.levels - 1)
        if candle.close <= self.config.lower_price:
            return []
        raw_index = int((candle.close - self.config.lower_price) / step)
        index = min(self.config.levels - 1, max(0, raw_index))
        price = self.config.lower_price + step * Decimal(index)
        if price >= candle.close:
            index -= 1
            price = self.config.lower_price + step * Decimal(index)
        if index < 0 or price < self.config.lower_price or price > self.config.upper_price:
            return []
        quantity = self.config.quote_per_order / price
        return [OrderIntent(
            side="buy", order_type="limit", quantity=quantity,
            limit_price=price, tag="grid-entry",
        )]


@dataclass(frozen=True)
class DCAConfig:
    initial_quote: Decimal
    dca_quote: Decimal
    adverse_step_pct: Decimal
    take_profit_pct: Decimal
    max_adds: int
    config_id: str = "dca-cleanroom-v1"

    def __post_init__(self) -> None:
        for name in ("initial_quote", "dca_quote", "adverse_step_pct", "take_profit_pct"):
            object.__setattr__(self, name, as_decimal(getattr(self, name), name, positive=True))
        if self.adverse_step_pct >= ONE:
            raise ValueError("adverse_step_pct must be a fraction less than 1")
        if isinstance(self.max_adds, bool) or not isinstance(self.max_adds, int) or self.max_adds < 0:
            raise ValueError("max_adds must be a non-negative integer")
        if not isinstance(self.config_id, str) or not self.config_id.strip():
            raise ValueError("config_id must be a non-empty string")


class DCAStrategy:
    strategy_id = "dca-cleanroom-v1"

    def __init__(self, config: DCAConfig) -> None:
        if not isinstance(config, DCAConfig):
            raise ValueError("config must be a DCAConfig")
        self.config = config
        self._last_inventory_qty = ZERO
        self._add_count = 0

    @property
    def add_count(self) -> int:
        return self._add_count

    def on_candle(
        self, candle: Candle, portfolio_snapshot: PortfolioSnapshot,
        open_orders: Sequence[PendingOrder],
    ) -> list[OrderIntent | CancelIntent]:
        quantity = portfolio_snapshot.inventory_qty
        if quantity <= ZERO:
            self._last_inventory_qty = ZERO
            self._add_count = 0
            if open_orders:
                return []
            return [OrderIntent(
                side="buy", order_type="market",
                quantity=self.config.initial_quote / candle.close,
                tag="dca-initial",
            )]

        if self._last_inventory_qty <= ZERO:
            self._add_count = 0
        elif quantity > self._last_inventory_qty:
            self._add_count += 1
        self._last_inventory_qty = quantity

        pending_buys = [order for order in open_orders if order.side == "buy"]
        pending_sells = [order for order in open_orders if order.side == "sell"]
        take_profit = portfolio_snapshot.average_entry_price * (ONE + self.config.take_profit_pct)

        if candle.close >= take_profit:
            cancels = [CancelIntent(order_id=order.order_id, tag="dca-take-profit-priority") for order in pending_buys]
            if pending_sells:
                return cancels
            return cancels + [OrderIntent(
                side="sell", order_type="limit", quantity=quantity,
                limit_price=take_profit, tag="dca-take-profit",
            )]

        next_add_price = portfolio_snapshot.average_entry_price * (
            ONE - self.config.adverse_step_pct * Decimal(self._add_count + 1)
        )
        if self._add_count < self.config.max_adds and candle.close <= next_add_price:
            if pending_buys:
                return []
            cancels = [CancelIntent(order_id=order.order_id, tag="dca-adverse-add-priority") for order in pending_sells]
            add_intent = OrderIntent(
                side="buy", order_type="limit",
                quantity=self.config.dca_quote / next_add_price,
                limit_price=next_add_price,
                tag=f"dca-add-{self._add_count + 1}",
            )
            return cancels + [add_intent]

        if pending_buys or pending_sells:
            return []
        return [OrderIntent(
            side="sell", order_type="limit", quantity=quantity,
            limit_price=take_profit, tag="dca-take-profit",
        )]


class ComboStrategy:
    strategy_id = "combo-cleanroom-v1"

    def __init__(self, *, grid_config: GridConfig, dca_config: DCAConfig) -> None:
        if not isinstance(grid_config, GridConfig):
            raise ValueError("grid_config must be a GridConfig")
        if not isinstance(dca_config, DCAConfig):
            raise ValueError("dca_config must be a DCAConfig")
        self.grid = GridStrategy(grid_config)
        self.dca = DCAStrategy(dca_config)

    def on_candle(
        self, candle: Candle, portfolio_snapshot: PortfolioSnapshot,
        open_orders: Sequence[PendingOrder],
    ) -> list[OrderIntent | CancelIntent]:
        if portfolio_snapshot.inventory_qty <= ZERO:
            return self.grid.on_candle(candle, portfolio_snapshot, open_orders)
        return self.dca.on_candle(candle, portfolio_snapshot, open_orders)
