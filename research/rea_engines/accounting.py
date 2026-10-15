"""Cash-backed Decimal accounting for the offline REA replay lab."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from .contracts import PortfolioSnapshot, as_decimal


ZERO = Decimal("0")


@dataclass(frozen=True)
class LedgerOperation:
    quantity: Decimal
    price: Decimal
    notional: Decimal
    fee: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    allocated_cost_basis: Decimal = ZERO
    realized_pnl: Decimal = ZERO

    @property
    def total_costs(self) -> Decimal:
        return self.fee + self.spread_cost + self.slippage_cost


class Ledger:
    """Long-only cash ledger with all-in inventory cost basis.

    Entry costs are capitalized into inventory basis and allocated pro-rata as
    units are sold. Sale costs reduce proceeds. This makes
    realized_pnl + unrealized_pnl reconcile to equity - starting_cash without
    deducting fees, spread, or slippage twice.
    """

    def __init__(self, starting_cash: object, max_inventory: object, max_fill_qty: object) -> None:
        self.starting_cash = as_decimal(starting_cash, "starting_cash", positive=True)
        self.max_inventory = as_decimal(max_inventory, "max_inventory", positive=True)
        self.max_fill_qty = as_decimal(max_fill_qty, "max_fill_qty", positive=True)
        self.cash = self.starting_cash
        self.inventory_qty = ZERO
        self.inventory_cost_basis = ZERO
        self.realized_pnl = ZERO
        self.total_fees = ZERO
        self.total_spread_cost = ZERO
        self.total_slippage_cost = ZERO
        self.turnover = ZERO

    def _operation_values(
        self, quantity: object, price: object, fee: object,
        spread_cost: object, slippage_cost: object,
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
        qty = as_decimal(quantity, "quantity", positive=True)
        px = as_decimal(price, "price", positive=True)
        fee_value = as_decimal(fee, "fee", non_negative=True)
        spread_value = as_decimal(spread_cost, "spread_cost", non_negative=True)
        slip_value = as_decimal(slippage_cost, "slippage_cost", non_negative=True)
        if qty > self.max_fill_qty:
            raise ValueError("quantity exceeds max_fill_qty")
        return qty, px, qty * px, fee_value, spread_value, slip_value

    def buy(
        self, *, quantity: object, price: object, fee: object = ZERO,
        spread_cost: object = ZERO, slippage_cost: object = ZERO,
    ) -> LedgerOperation:
        qty, px, notional, fee_value, spread_value, slip_value = self._operation_values(
            quantity, price, fee, spread_cost, slippage_cost
        )
        if self.inventory_qty + qty > self.max_inventory:
            raise ValueError("buy would exceed max_inventory")
        total_costs = fee_value + spread_value + slip_value
        debit = notional + total_costs
        if debit > self.cash:
            raise ValueError("insufficient cash for buy including modeled costs")
        self.cash -= debit
        self.inventory_qty += qty
        self.inventory_cost_basis += debit
        self.total_fees += fee_value
        self.total_spread_cost += spread_value
        self.total_slippage_cost += slip_value
        self.turnover += notional
        return LedgerOperation(
            quantity=qty, price=px, notional=notional, fee=fee_value,
            spread_cost=spread_value, slippage_cost=slip_value,
        )

    def sell(
        self, *, quantity: object, price: object, fee: object = ZERO,
        spread_cost: object = ZERO, slippage_cost: object = ZERO,
    ) -> LedgerOperation:
        qty, px, notional, fee_value, spread_value, slip_value = self._operation_values(
            quantity, price, fee, spread_cost, slippage_cost
        )
        if qty > self.inventory_qty:
            raise ValueError("sell would create a short position")
        total_costs = fee_value + spread_value + slip_value
        if total_costs > notional:
            raise ValueError("sale costs exceed gross proceeds")
        net_proceeds = notional - total_costs
        if self.cash + net_proceeds < ZERO:
            raise ValueError("sale would create negative cash")
        if qty == self.inventory_qty:
            allocated_basis = self.inventory_cost_basis
        else:
            allocated_basis = self.inventory_cost_basis * qty / self.inventory_qty
        realized = net_proceeds - allocated_basis
        self.cash += net_proceeds
        self.inventory_qty -= qty
        self.inventory_cost_basis -= allocated_basis
        if self.inventory_qty == ZERO:
            self.inventory_cost_basis = ZERO
        self.realized_pnl += realized
        self.total_fees += fee_value
        self.total_spread_cost += spread_value
        self.total_slippage_cost += slip_value
        self.turnover += notional
        return LedgerOperation(
            quantity=qty, price=px, notional=notional, fee=fee_value,
            spread_cost=spread_value, slippage_cost=slip_value,
            allocated_cost_basis=allocated_basis, realized_pnl=realized,
        )

    @property
    def average_entry_price(self) -> Decimal:
        if self.inventory_qty == ZERO:
            return ZERO
        return self.inventory_cost_basis / self.inventory_qty

    def equity(self, mark_price: object) -> Decimal:
        mark = as_decimal(mark_price, "mark_price", positive=True)
        return self.cash + self.inventory_qty * mark

    def unrealized_pnl(self, mark_price: object) -> Decimal:
        mark = as_decimal(mark_price, "mark_price", positive=True)
        return self.inventory_qty * mark - self.inventory_cost_basis

    def snapshot(self, mark_price: object) -> PortfolioSnapshot:
        mark = as_decimal(mark_price, "mark_price", positive=True)
        return PortfolioSnapshot(
            cash=self.cash,
            inventory_qty=self.inventory_qty,
            average_entry_price=self.average_entry_price,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl(mark),
            equity=self.equity(mark),
            total_fees=self.total_fees,
            total_spread_cost=self.total_spread_cost,
            total_slippage_cost=self.total_slippage_cost,
            turnover=self.turnover,
        )

    def metrics_snapshot(self, mark_price: object) -> dict[str, str]:
        snapshot = self.snapshot(mark_price)
        return {
            "starting_cash": str(self.starting_cash),
            "cash": str(snapshot.cash),
            "inventory_qty": str(snapshot.inventory_qty),
            "average_entry_price": str(snapshot.average_entry_price),
            "equity": str(snapshot.equity),
            "realized_pnl": str(snapshot.realized_pnl),
            "unrealized_pnl": str(snapshot.unrealized_pnl),
            "net_pnl": str(snapshot.equity - self.starting_cash),
            "total_fees": str(snapshot.total_fees),
            "total_spread_cost": str(snapshot.total_spread_cost),
            "total_slippage_cost": str(snapshot.total_slippage_cost),
            "turnover": str(snapshot.turnover),
        }
