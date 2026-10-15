"""Cross-module accounting parity for the isolated REA lab (paper-only)."""
import unittest
from decimal import Decimal

from research.rea_engines.accounting import Ledger
from research.rea_engines.execution_resilience import FillEvent, PaperState, apply_fill, register_order

SOURCE_SHA = "a" * 64
CONFIG_SHA = "b" * 64


def _event(fill_id, order_id, side, quantity, price, fee, spread, slippage, seq, hour):
    return FillEvent(
        fill_id=fill_id,
        order_id=order_id,
        side=side,
        quantity=quantity,
        price=price,
        fee=fee,
        spread_cost=spread,
        slippage_cost=slippage,
        timestamp=f"2026-10-14T{hour:02d}:00:00Z",
        event_seq=seq,
    )


class AccountingResilienceParityTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(starting_cash="1000", max_inventory="5", max_fill_qty="5")
        self.state = PaperState.initial(
            starting_cash="1000", source_sha256=SOURCE_SHA, config_sha256=CONFIG_SHA
        )

    def _assert_parity(self):
        fields = (
            "cash", "inventory_qty", "inventory_cost_basis", "realized_pnl",
            "total_fees", "total_spread_cost", "total_slippage_cost", "turnover",
        )
        for name in fields:
            with self.subTest(field=name):
                self.assertEqual(getattr(self.state, name), getattr(self.ledger, name))

    def _apply(self, event):
        if event.side == "buy":
            self.ledger.buy(
                quantity=event.quantity, price=event.price, fee=event.fee,
                spread_cost=event.spread_cost, slippage_cost=event.slippage_cost,
            )
        else:
            self.ledger.sell(
                quantity=event.quantity, price=event.price, fee=event.fee,
                spread_cost=event.spread_cost, slippage_cost=event.slippage_cost,
            )
        self.state, result = apply_fill(self.state, event)
        self.assertTrue(result.applied)
        self.assertFalse(result.duplicate)
        self._assert_parity()
        return event

    def test_partial_buy_and_sell_stream_matches_ledger_after_every_fill(self):
        self.state = register_order(
            self.state, order_id="BUY-1", client_order_id="CLIENT-BUY-1", side="buy", quantity="1"
        )
        self._apply(_event("F-1", "BUY-1", "buy", "0.4", "100", "0.4", "0.1", "0.2", 1, 1))
        self._apply(_event("F-2", "BUY-1", "buy", "0.6", "90", "0.54", "0.09", "0.18", 2, 2))

        self.state = register_order(
            self.state, order_id="SELL-1", client_order_id="CLIENT-SELL-1", side="sell", quantity="1"
        )
        self._apply(_event("F-3", "SELL-1", "sell", "0.4", "120", "0.48", "0.12", "0.24", 3, 3))
        self._apply(_event("F-4", "SELL-1", "sell", "0.6", "110", "0.66", "0.11", "0.22", 4, 4))

        self.assertEqual(self.state.inventory_qty, Decimal("0"))
        self.assertEqual(self.state.inventory_cost_basis, Decimal("0"))
        self.assertEqual(self.state.cash, Decimal("1016.66"))
        self.assertEqual(self.state.realized_pnl, Decimal("16.66"))
        self.assertEqual(self.ledger.snapshot("110").realized_pnl, self.state.realized_pnl)

    def test_duplicate_fill_is_noop_and_preserves_cross_module_parity(self):
        self.state = register_order(
            self.state, order_id="BUY-1", client_order_id="CLIENT-BUY-1", side="buy", quantity="1"
        )
        event = self._apply(_event("F-1", "BUY-1", "buy", "0.5", "100", "0.5", "0.1", "0.2", 1, 1))
        before = self.state
        self.state, result = apply_fill(self.state, event)
        self.assertFalse(result.applied)
        self.assertTrue(result.duplicate)
        self.assertEqual(self.state, before)
        self._assert_parity()


if __name__ == "__main__":
    unittest.main()