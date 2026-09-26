"""PAPER execution realism tests (takeover section 21).

Covers fees on both legs, conservative partial fills (no optimistic floor),
SHORT handling, hypothetical bankroll accounting, cash constraints, and
drawdown math in the upgraded ExecutionSimulator.
"""
from __future__ import annotations

import asyncio
import math
import unittest

from senecio_polymarket.backend.execution_simulator import ExecutionSimulator
from senecio_polymarket.backend.liquidity import Orderbook, BookSide
from senecio_polymarket.backend.models import MarketTick, Signal


def _tick(price: float, symbol: str = "BTCUSDT") -> MarketTick:
    return MarketTick(symbol=symbol, payload={"price": price, "volume": 10.0})


def _signal(action: str, sizing_usd: float, symbol: str = "BTCUSDT") -> Signal:
    return Signal(symbol=symbol, payload={"action": action, "sizing_usd": sizing_usd, "confidence": 0.6})


def _book(depth_usd: float) -> Orderbook:
    # symmetric book: each side holds depth_usd/2 notional at price ~100
    level_qty = (depth_usd / 2) / 100.0
    return Orderbook(
        bids=BookSide(levels=[(99.9, level_qty)]),
        asks=BookSide(levels=[(100.1, level_qty)]),
    )


class PaperExecutionRealismTests(unittest.TestCase):
    def test_fees_charged_on_both_legs_and_reduce_pnl(self) -> None:
        sim = ExecutionSimulator(paper_bankroll_usd=10_000.0, taker_fee_bps=10.0)
        fill = asyncio.run(sim.execute(_signal("LONG", 1_000.0), _tick(100.0)))
        payload = fill.payload
        self.assertEqual(payload["status"], "FILLED")
        self.assertGreater(payload["fee_usd"], 0)
        expected_entry_fee = 1_000.0 * 10.0 / 10_000.0
        self.assertAlmostEqual(payload["fee_usd"], expected_entry_fee, places=2)
        self.assertTrue(payload["hypothetical"])
        # exit at target (104% for LONG)
        exits = asyncio.run(sim.monitor_exits(_tick(105.0)))
        self.assertEqual(len(exits), 1)
        exit_payload = exits[0].payload
        self.assertGreater(exit_payload["fee_usd"], 0)
        # gross pnl minus BOTH legs' fees == realized pnl
        self.assertAlmostEqual(
            exit_payload["gross_pnl"] - exit_payload["fee_usd"] - expected_entry_fee,
            exit_payload["realized_pnl"],
            places=1,
        )
        # cash accounting: entry fee + exit fee deducted
        state = sim.risk_state()
        self.assertAlmostEqual(
            state["fees_paid"], expected_entry_fee + exit_payload["fee_usd"], places=1,
        )

    def test_thin_book_yields_partial_fill_not_floor_inflation(self) -> None:
        sim = ExecutionSimulator()
        # sizing 10_000 USD vs book with only 1_000 USD depth -> 10% fill
        fill = asyncio.run(
            sim.execute(_signal("LONG", 10_000.0), _tick(100.0), book=_book(1_000.0))
        )
        self.assertEqual(fill.payload["status"], "PARTIAL_FILL")
        self.assertLess(fill.payload["fill_pct"], 0.11)
        self.assertGreater(fill.payload["fill_pct"], 0.09)
        self.assertEqual(fill.payload["depth_source"], "book_observed")

    def test_zero_depth_is_missed_fill_not_inflated(self) -> None:
        sim = ExecutionSimulator()
        empty = Orderbook(bids=BookSide(levels=[]), asks=BookSide(levels=[]))
        fill = asyncio.run(
            sim.execute(_signal("LONG", 5_000.0), _tick(100.0), book=empty)
        )
        self.assertEqual(fill.payload["status"], "MISSED")
        self.assertEqual(fill.payload["reason"], "insufficient_depth")
        self.assertEqual(fill.payload["depth_source"], "book_observed")

    def test_no_book_uses_labeled_assumed_depth(self) -> None:
        sim = ExecutionSimulator(assumed_depth_usd=2_000.0)
        fill = asyncio.run(sim.execute(_signal("LONG", 10_000.0), _tick(100.0)))
        self.assertEqual(fill.payload["depth_source"], "assumed")
        self.assertAlmostEqual(fill.payload["fill_pct"], 0.2, places=2)

    def test_short_position_pnl_sign_and_exit(self) -> None:
        sim = ExecutionSimulator()
        fill = asyncio.run(sim.execute(_signal("SHORT", 1_000.0), _tick(100.0)))
        self.assertEqual(fill.payload["direction"], "SHORT")
        self.assertEqual(fill.payload["side"], "SELL")
        # price falls 5% -> SHORT target hit (4%)
        exits = asyncio.run(sim.monitor_exits(_tick(94.0)))
        self.assertEqual(len(exits), 1)
        self.assertGreater(exits[0].payload["realized_pnl"], 0)

    def test_short_stop_closes_on_adverse_move(self) -> None:
        sim = ExecutionSimulator()
        asyncio.run(sim.execute(_signal("SHORT", 1_000.0), _tick(100.0)))
        exits = asyncio.run(sim.monitor_exits(_tick(103.0)))
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0].payload["reason"], "STOP")
        self.assertLess(exits[0].payload["realized_pnl"], 0)

    def test_order_notional_cannot_exceed_paper_cash(self) -> None:
        sim = ExecutionSimulator(paper_bankroll_usd=500.0)
        fill = asyncio.run(sim.execute(_signal("LONG", 10_000.0), _tick(100.0)))
        self.assertEqual(fill.payload["status"], "REJECTED")
        self.assertEqual(fill.payload["reason"], "insufficient_paper_cash")

    def test_hold_and_watch_actions_skip_execution(self) -> None:
        sim = ExecutionSimulator()
        for action in ("HOLD", "WATCH", "EXIT", "FLAT"):
            fill = asyncio.run(sim.execute(_signal(action, 1_000.0), _tick(100.0)))
            self.assertEqual(fill.payload["status"], "SKIPPED", action)

    def test_drawdown_tracks_peak_equity(self) -> None:
        sim = ExecutionSimulator(paper_bankroll_usd=10_000.0)
        # open a position and let the mark fall below entry
        asyncio.run(sim.execute(_signal("LONG", 5_000.0), _tick(100.0)))
        state = sim.risk_state(last_prices={"BTCUSDT": 95.0})
        self.assertGreater(state["drawdown_pct"], 0.0)
        self.assertLess(state["equity"], state["bankroll"]["starting_usd"])
        self.assertAlmostEqual(
            state["return_pct"],
            (state["equity"] - 10_000.0) / 10_000.0 * 100,
            places=2,
        )
        self.assertTrue(state["hypothetical"])

    def test_risk_state_marks_unrealized_at_observed_price(self) -> None:
        sim = ExecutionSimulator()
        asyncio.run(sim.execute(_signal("LONG", 1_000.0), _tick(100.0)))
        state = sim.risk_state(last_prices={"BTCUSDT": 101.0})
        self.assertGreater(state["unrealized_pnl"], 0)

    def test_no_lookahead_fill_price_reflects_entry_slippage_only(self) -> None:
        sim = ExecutionSimulator(base_slippage_bps=2.0, rng_slippage_bps=0.0)
        fill = asyncio.run(sim.execute(_signal("LONG", 1_000.0), _tick(100.0)))
        # deterministic slippage of exactly 2 bps on entry
        self.assertAlmostEqual(fill.payload["fill_price"], 100.0 * 1.0002, places=4)

    def test_pnl_reconciliation_cash_plus_positions(self) -> None:
        sim = ExecutionSimulator(paper_bankroll_usd=10_000.0)
        asyncio.run(sim.execute(_signal("LONG", 1_000.0), _tick(100.0)))
        asyncio.run(sim.monitor_exits(_tick(105.0)))
        state = sim.risk_state()
        # after close, equity == cash; realized pnl == equity - starting
        self.assertAlmostEqual(state["equity"], state["cash"], places=2)
        self.assertAlmostEqual(
            state["realized_pnl"], state["cash"] - 10_000.0, places=1
        )
        self.assertGreater(state["fees_paid"], 0)


if __name__ == "__main__":
    unittest.main()
