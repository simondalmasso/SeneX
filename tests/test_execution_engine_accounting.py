"""Regression tests for real ACT-XXV ExecutionEngine cash/PnL accounting."""
from __future__ import annotations
import asyncio
import unittest
from senecio_polymarket.backend.portfolio.execution_engine import ExecutionEngine

class ExecutionEngineAccountingPortTests(unittest.TestCase):
    def _engine(self):
        e = ExecutionEngine(config={
            "base_slippage_bps": 0.0, "rng_slippage_bps": 0.0,
            "exit_slippage_bps": 0.0, "taker_fee_bps": 100.0,
            "latency_ms_min": 0, "latency_ms_max": 0,
            "retry_backoff_ms": 0, "max_retries": 0,
            "starting_cash": 10_000.0,
        })
        e._rng.uniform = lambda a, b: b
        e._rng.randint = lambda a, b: 0
        return e

    def _open(self, e, direction):
        order = asyncio.run(e.submit(
            {"symbol": "BTCUSDT", "direction": direction,
             "size_qty": 1.0, "prediction_id": "pred-test"},
            {"size_scale": 1.0}, last_price=100.0,
            book_depth_usd=1_000_000.0,
        ))
        self.assertEqual(order.status, "FILLED")
        e.set_stop_target("BTCUSDT", 95.0 if direction == "LONG" else 105.0,
                          105.0 if direction == "LONG" else 95.0)
        return order, e.positions["BTCUSDT"]

    def test_long_close_reconciles_cash(self):
        e = self._engine(); start = e.starting_cash
        order, _ = self._open(e, "LONG")
        self.assertGreater(order.total_fees, 0.0)
        e.check_exits("BTCUSDT", 110.0, "2026-09-12T04:00:00+00:00")
        realized = e.closed_positions[-1].realized_pnl
        self.assertAlmostEqual(e.cash, start + realized, places=6)

    def test_short_close_reconciles_cash(self):
        e = self._engine(); start = e.starting_cash
        order, _ = self._open(e, "SHORT")
        self.assertGreater(order.total_fees, 0.0)
        e.check_exits("BTCUSDT", 90.0, "2026-09-12T04:00:00+00:00")
        realized = e.closed_positions[-1].realized_pnl
        self.assertAlmostEqual(e.cash, start + realized, places=6)

    def test_short_mark_at_entry_is_start_minus_entry_fee(self):
        e = self._engine(); start = e.starting_cash
        order, pos = self._open(e, "SHORT")
        self.assertAlmostEqual(pos.fees_paid, order.total_fees, places=8)
        self.assertAlmostEqual(e.equity({"BTCUSDT": pos.avg_entry_price}),
                               start - order.total_fees, places=6)

    def test_order_declares_total_fees(self):
        e = self._engine(); order, _ = self._open(e, "LONG")
        self.assertGreater(order.total_fees, 0.0)

if __name__ == "__main__":
    unittest.main()


class TradeJournalAccountingTests(unittest.TestCase):
    def test_net_pnl_is_not_fee_subtracted_twice(self):
        import json
        import tempfile
        from pathlib import Path
        from senecio_polymarket.backend.portfolio.trade_journal import TradeJournal

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "trades.jsonl"
            path.write_text(json.dumps({
                "realized_pnl_usd": 8.0,
                "total_fees_usd": 2.0,
                "holding_time_s": 60,
                "mae_bps": 0.0,
                "mfe_bps": 0.0,
            }) + "\n", encoding="utf-8")
            stats = TradeJournal(path=str(path)).stats()
        self.assertEqual(stats["total_pnl_usd"], 8.0)
        self.assertEqual(stats["total_fees_usd"], 2.0)
        self.assertEqual(stats["net_pnl_usd"], 8.0)
