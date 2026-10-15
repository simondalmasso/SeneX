"""TDD tests for the isolated REA engine accounting contracts."""
import importlib
import unittest
from decimal import Decimal


class ReaEngineAccountingApiTests(unittest.TestCase):
    def test_contracts_and_ledger_api_are_exposed(self):
        contracts = importlib.import_module("research.rea_engines.contracts")
        accounting = importlib.import_module("research.rea_engines.accounting")
        for name in ("Candle", "OrderIntent", "CostProfile", "PortfolioSnapshot"):
            with self.subTest(contract=name):
                self.assertTrue(hasattr(contracts, name), f"missing contract: {name}")
        self.assertTrue(hasattr(accounting, "Ledger"), "missing Decimal Ledger")

    def _candle(self, **kwargs):
        from research.rea_engines.contracts import Candle
        try:
            return Candle(**kwargs)
        except TypeError as exc:
            self.fail(f"Candle must accept and validate its public fields: {exc}")

    def _ledger(self, **kwargs):
        from research.rea_engines.accounting import Ledger
        try:
            return Ledger(**kwargs)
        except TypeError as exc:
            self.fail(f"Ledger must accept explicit cash and inventory caps: {exc}")

    def test_ledger_reconciles_cash_realized_and_unrealized_pnl(self):
        ledger = self._ledger(starting_cash="1000", max_inventory="5", max_fill_qty="2")
        ledger.buy(quantity="1", price="100", fee="1")
        ledger.buy(quantity="1", price="80", fee="0.8")
        snapshot = ledger.snapshot("100")
        self.assertEqual(snapshot.cash, Decimal("818.2"))
        self.assertEqual(snapshot.inventory_qty, Decimal("2"))
        self.assertEqual(snapshot.average_entry_price, Decimal("90.9"))
        self.assertEqual(snapshot.equity, Decimal("1018.2"))
        self.assertEqual(snapshot.total_fees, Decimal("1.8"))

        result = ledger.sell(quantity="1", price="100", fee="1")
        self.assertEqual(result.realized_pnl, Decimal("8.1"))
        snapshot = ledger.snapshot("100")
        self.assertEqual(snapshot.cash, Decimal("917.2"))
        self.assertEqual(snapshot.inventory_qty, Decimal("1"))
        self.assertEqual(snapshot.realized_pnl + snapshot.unrealized_pnl,
                         snapshot.equity - Decimal("1000"))

    def test_ledger_rejects_overspend_inventory_and_fill_cap(self):
        capped = self._ledger(starting_cash="1000", max_inventory="2", max_fill_qty="1")
        with self.assertRaises(ValueError):
            capped.buy(quantity="1.1", price="10", fee="0")
        capped.buy(quantity="1", price="100", fee="0")
        capped.buy(quantity="1", price="100", fee="0")
        with self.assertRaises(ValueError):
            capped.buy(quantity="1", price="100", fee="0")
        with self.assertRaises(ValueError):
            capped.sell(quantity="1.1", price="110", fee="0")

        no_short = self._ledger(starting_cash="1000", max_inventory="2", max_fill_qty="2")
        no_short.buy(quantity="1", price="100", fee="0")
        with self.assertRaises(ValueError):
            no_short.sell(quantity="2", price="110", fee="0")

        no_overspend = self._ledger(starting_cash="1000", max_inventory="5", max_fill_qty="2")
        with self.assertRaises(ValueError):
            no_overspend.buy(quantity="2", price="600", fee="0")

    def test_candle_normalizes_prices_to_decimal_and_validates_ohlc(self):
        candle = self._candle(
            timestamp="2026-01-01T00:00:00Z",
            open="100", high="110", low="90", close="105", volume="4.5",
        )
        self.assertEqual(candle.open, Decimal("100"))
        self.assertEqual(candle.high, Decimal("110"))
        self.assertEqual(candle.volume, Decimal("4.5"))
        with self.assertRaises(ValueError):
            self._candle(
                timestamp="2026-01-01T04:00:00Z",
                open="100", high="99", low="90", close="105", volume="4.5",
            )
        with self.assertRaises(ValueError):
            self._candle(
                timestamp="2026-01-01T04:00:00Z",
                open="100", high="110", low="90", close="105", volume="-1",
            )


if __name__ == "__main__":
    unittest.main()
