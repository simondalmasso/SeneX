"""TDD tests for the REA Grid, DCA, and Combo strategy contracts."""
import importlib
import unittest
from decimal import Decimal

from research.rea_engines.contracts import Candle, CancelIntent, CostProfile, OrderIntent, PendingOrder, PortfolioSnapshot


class StrategyApiTests(unittest.TestCase):
    def test_strategy_public_api_is_exposed(self):
        module = importlib.import_module("research.rea_engines.strategies")
        for name in ("GridConfig", "GridStrategy", "DCAConfig", "DCAStrategy", "ComboStrategy"):
            with self.subTest(api=name):
                self.assertTrue(hasattr(module, name), f"missing strategy API: {name}")

    def _candle(self, hour, close="100"):
        close = Decimal(close)
        return Candle(
            timestamp=f"2026-01-01T{hour:02d}:00:00Z",
            open=close, high=close + Decimal("2"), low=close - Decimal("2"),
            close=close, volume="100",
        )

    def _snapshot(self, cash="1000", qty="0", avg="0", equity="1000"):
        return PortfolioSnapshot(
            cash=Decimal(cash), inventory_qty=Decimal(qty),
            average_entry_price=Decimal(avg), realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"), equity=Decimal(equity),
            total_fees=Decimal("0"), total_spread_cost=Decimal("0"),
            total_slippage_cost=Decimal("0"), turnover=Decimal("0"),
        )

    def _grid_config(self):
        from research.rea_engines.strategies import GridConfig
        try:
            return GridConfig(
                lower_price="100", upper_price="200", levels=5,
                quote_per_order="15", take_profit_pct="0.01",
            )
        except TypeError as exc:
            self.fail(f"GridConfig must expose fixed, explicit parameters: {exc}")

    def _dca_config(self, max_adds=3):
        from research.rea_engines.strategies import DCAConfig
        try:
            return DCAConfig(
                initial_quote="10", dca_quote="10", adverse_step_pct="0.05",
                take_profit_pct="0.03", max_adds=max_adds,
            )
        except TypeError as exc:
            self.fail(f"DCAConfig must expose fixed, explicit parameters: {exc}")

    def test_grid_places_nearest_below_close_limit_and_respects_pending_order(self):
        from research.rea_engines.strategies import GridStrategy
        try:
            strategy = GridStrategy(self._grid_config())
        except TypeError as exc:
            self.fail(f"GridStrategy must accept GridConfig: {exc}")
        candle = self._candle(0, "155")
        intents = strategy.on_candle(candle, self._snapshot(), ())
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].side, "buy")
        self.assertEqual(intents[0].order_type, "limit")
        self.assertEqual(intents[0].limit_price, Decimal("150"))
        self.assertEqual(intents[0].quantity, Decimal("0.1"))

        pending = PendingOrder(
            order_id="grid-000001", side="buy", order_type="limit",
            quantity=Decimal("0.1"), limit_price=Decimal("150"),
            submitted_index=0, submitted_timestamp=candle.timestamp,
            expires_after_bars=None,
        )
        self.assertEqual(strategy.on_candle(candle, self._snapshot(), (pending,)), [])

    def test_grid_posts_take_profit_limit_while_holding_inventory(self):
        from research.rea_engines.strategies import GridStrategy
        strategy = GridStrategy(self._grid_config())
        intents = strategy.on_candle(self._candle(0, "110"), self._snapshot(qty="0.2", avg="100", equity="1022"), ())
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].side, "sell")
        self.assertEqual(intents[0].order_type, "limit")
        self.assertEqual(intents[0].quantity, Decimal("0.2"))
        self.assertEqual(intents[0].limit_price, Decimal("101"))

    def test_dca_enters_flat_with_fixed_quote_market_order(self):
        from research.rea_engines.strategies import DCAStrategy
        strategy = DCAStrategy(self._dca_config())
        intents = strategy.on_candle(self._candle(0, "100"), self._snapshot(), ())
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].side, "buy")
        self.assertEqual(intents[0].order_type, "market")
        self.assertEqual(intents[0].quantity, Decimal("0.1"))
        self.assertEqual(intents[0].tag, "dca-initial")

    def test_dca_cancels_take_profit_before_adverse_add_and_respects_add_cap(self):
        from research.rea_engines.strategies import DCAStrategy
        strategy = DCAStrategy(self._dca_config(max_adds=1))
        first = strategy.on_candle(self._candle(0, "100"), self._snapshot(qty="1", avg="100"), ())
        self.assertEqual(first[0].side, "sell")
        self.assertEqual(first[0].limit_price, Decimal("103"))
        tp = PendingOrder(
            order_id="dca-000001", side="sell", order_type="limit",
            quantity=Decimal("1"), limit_price=Decimal("103"),
            submitted_index=0, submitted_timestamp=self._candle(0).timestamp,
            expires_after_bars=None, tag="dca-take-profit",
        )
        adverse = strategy.on_candle(self._candle(1, "94"), self._snapshot(qty="1", avg="100"), (tp,))
        self.assertIsInstance(adverse[0], CancelIntent)
        self.assertEqual(adverse[0].order_id, tp.order_id)
        self.assertIsInstance(adverse[1], OrderIntent)
        self.assertEqual(adverse[1].side, "buy")
        self.assertEqual(adverse[1].limit_price, Decimal("95"))
        self.assertEqual(adverse[1].tag, "dca-add-1")

        # A larger inventory snapshot represents a filled add; max_adds=1 prevents another add.
        managed = strategy.on_candle(self._candle(2, "90"), self._snapshot(qty="1.1", avg="99"), ())
        self.assertEqual(len(managed), 1)
        self.assertEqual(managed[0].side, "sell")
        self.assertEqual(managed[0].tag, "dca-take-profit")

    def test_strategy_configs_reject_invalid_grid_range_and_dca_step(self):
        from research.rea_engines.strategies import DCAConfig, GridConfig
        with self.assertRaises(ValueError):
            GridConfig(
                lower_price="100", upper_price="100", levels=5,
                quote_per_order="10", take_profit_pct="0.01",
            )
        with self.assertRaises(ValueError):
            DCAConfig(
                initial_quote="10", dca_quote="10", adverse_step_pct="1",
                take_profit_pct="0.03", max_adds=2,
            )

    def test_combo_uses_grid_when_flat_and_dca_when_holding(self):
        from research.rea_engines.strategies import ComboStrategy
        try:
            combo = ComboStrategy(grid_config=self._grid_config(), dca_config=self._dca_config())
        except TypeError as exc:
            self.fail(f"ComboStrategy must accept explicit Grid and DCA configs: {exc}")
        flat = combo.on_candle(self._candle(0, "155"), self._snapshot(), ())
        self.assertEqual(flat[0].side, "buy")
        self.assertEqual(flat[0].order_type, "limit")
        holding = combo.on_candle(self._candle(1, "94"), self._snapshot(qty="1", avg="100"), ())
        self.assertEqual(holding[0].side, "buy")
        self.assertEqual(holding[0].tag, "dca-add-1")


if __name__ == "__main__":
    unittest.main()
