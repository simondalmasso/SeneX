from __future__ import annotations

import unittest
from unittest import mock

from senecio_polymarket.backend import paper_view
from senecio_polymarket.backend.portfolio.execution_engine import ExecutionEngine, Position


def _engine_with_open_long() -> ExecutionEngine:
    engine = ExecutionEngine({"starting_cash": 10_000.0})
    engine.positions["BTCUSDT"] = Position(
        position_id="p1",
        symbol="BTCUSDT",
        direction="LONG",
        qty=1.0,
        avg_entry_price=100.0,
        entry_ts="2026-09-14T00:00:00+00:00",
        stop_price=95.0,
        target_price=105.0,
    )
    engine.cash = 9_900.0
    return engine


class EquityUnknownTests(unittest.TestCase):
    def test_equity_state_reports_missing_market_price(self) -> None:
        state = _engine_with_open_long().equity_state({})
        self.assertEqual(state["status"], "UNKNOWN")
        self.assertIsNone(state["equity"])
        self.assertEqual(state["missing_price_symbols"], ["BTCUSDT"])
    def test_equity_raises_instead_of_marking_at_entry(self) -> None:
        engine = _engine_with_open_long()
        with self.assertRaisesRegex(ValueError, "MISSING_MARKET_PRICE:BTCUSDT"):
            engine.equity({})

    def test_equity_state_known_with_fresh_price(self) -> None:
        state = _engine_with_open_long().equity_state({"BTCUSDT": 110.0})
        self.assertEqual(state["status"], "OK")
        self.assertEqual(state["equity"], 10010.0)
        self.assertEqual(state["missing_price_symbols"], [])

    def test_paper_view_exposes_unknown_equity_reason(self) -> None:
        engine = _engine_with_open_long()

        class Coord:
            execution_engine = engine
            def get_state(self): return {"status": "OK"}
            def get_recent_trades(self, limit=20): return []
            def get_analytics(self): return {}
            def get_shadow_report(self): return {}

        with mock.patch.object(paper_view, "_get_coordinator", return_value=Coord()):
            payload = paper_view.paper_state(last_prices={})
        self.assertIsNone(payload["equity"])
        self.assertEqual(payload["equity_status"]["status"], "UNKNOWN")
        self.assertEqual(payload["equity_status"]["missing_price_symbols"], ["BTCUSDT"])


if __name__ == "__main__":
    unittest.main()

from senecio_polymarket.backend.portfolio.portfolio_engine import PortfolioEngine


class PortfolioStateUnknownTests(unittest.TestCase):
    def _position(self) -> dict:
        return _engine_with_open_long().positions["BTCUSDT"].to_dict()

    def test_recompute_state_marks_equity_unknown_without_price(self) -> None:
        state = PortfolioEngine().recompute_state(
            open_positions={"BTCUSDT": self._position()},
            cash=9_900.0,
            starting_equity=10_000.0,
            last_prices={},
        )
        self.assertIsNone(state.equity)
        self.assertEqual(state.equity_status, "UNKNOWN")
        self.assertEqual(state.missing_price_symbols, ["BTCUSDT"])

    def test_recompute_state_equity_ok_with_fresh_price(self) -> None:
        state = PortfolioEngine().recompute_state(
            open_positions={"BTCUSDT": self._position()},
            cash=9_900.0,
            starting_equity=10_000.0,
            last_prices={"BTCUSDT": 110.0},
        )
        self.assertEqual(state.equity_status, "OK")
        self.assertEqual(state.equity, 10_010.0)
        self.assertEqual(state.missing_price_symbols, [])
    def test_build_proposal_fails_closed_when_equity_unknown(self) -> None:
        engine = PortfolioEngine()
        state = engine.recompute_state(
            open_positions={"BTCUSDT": self._position()},
            cash=9_900.0,
            starting_equity=10_000.0,
            last_prices={},
        )
        proposal = engine.build_proposal(
            {
                "id": "x",
                "symbol": "ETHUSDT",
                "prediction": "LONG",
                "price_now": 100.0,
                "confidence": 0.9,
                "ev": 0.01,
            },
            state=state,
            win_rate_by_direction={"LONG": 0.60},
        )
        self.assertIsNone(proposal)
