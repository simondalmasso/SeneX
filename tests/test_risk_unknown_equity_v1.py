from __future__ import annotations

import unittest

from senecio_polymarket.backend.portfolio.coordinator import PortfolioCoordinator
from senecio_polymarket.backend.portfolio.risk_kernel import RiskKernel


class RiskUnknownEquityTests(unittest.TestCase):
    def test_record_pnl_with_unknown_equity_preserves_pnl_and_trips_kill(self) -> None:
        kernel = RiskKernel({"starting_equity_usd": 10_000.0})
        before_equity = kernel.state.current_equity
        kernel.record_pnl(pnl_usd=-25.0, equity=None)
        self.assertEqual(kernel.state.daily_pnl_usd, -25.0)
        self.assertEqual(kernel.state.current_equity, before_equity)
        self.assertTrue(kernel.state.kill_switch_active)
        self.assertIn("equity", kernel.state.kill_switch_reason.lower())


class _Engine:
    def check_exits(self, **_kwargs):
        return [{"realized_pnl": -25.0, "position": {"direction": "LONG"}}]

    def equity(self, _last_prices):
        raise ValueError("MISSING_MARKET_PRICE:ETHUSDT")


class _Kernel:
    class State:
        kill_switch_active = False

    def __init__(self):
        self.state = self.State()
        self.calls = []

    def record_pnl(self, pnl_usd, equity):
        self.calls.append((pnl_usd, equity))


class CoordinatorUnknownEquityTests(unittest.TestCase):
    def test_on_tick_continues_and_passes_unknown_equity_to_kernel(self) -> None:
        coord = object.__new__(PortfolioCoordinator)
        coord._started = True
        coord._last_prices = {}
        coord.execution_engine = _Engine()
        coord.risk_kernel = _Kernel()
        coord.meta_labeler = None
        exits = coord.on_tick("BTCUSDT", 100.0, "2026-09-14T04:00:00+00:00")
        self.assertEqual(len(exits), 1)
        self.assertEqual(coord.risk_kernel.calls, [(-25.0, None)])


if __name__ == "__main__":
    unittest.main()
