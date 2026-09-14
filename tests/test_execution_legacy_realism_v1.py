from __future__ import annotations

import asyncio
import math
import unittest

from senecio_polymarket.backend.portfolio.execution_engine import ExecutionEngine


def _proposal() -> dict:
    return {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "size_qty": 10.0,
        "risk_usd": 25.0,
        "prediction_id": 1,
    }


def _decision() -> dict:
    return {"approved": True, "size_scale": 1.0, "proposal_id": 1}


def _engine() -> ExecutionEngine:
    return ExecutionEngine({
        "latency_ms_min": 0,
        "latency_ms_max": 0,
        "retry_backoff_ms": 0,
        "max_retries": 0,
        "min_fill_pct": 0.30,
        "book_depth_assumed_usd": 5_000.0,
    })

class LegacyExecutionRealismTests(unittest.TestCase):
    def test_missing_depth_does_not_invent_liquidity(self) -> None:
        engine = _engine()
        order = asyncio.run(engine.submit(
            _proposal(), _decision(), last_price=100.0, book_depth_usd=None
        ))
        self.assertEqual(order.filled_qty, 0.0)
        self.assertEqual(order.status, "CANCELED")

    def test_zero_depth_does_not_fill(self) -> None:
        engine = _engine()
        order = asyncio.run(engine.submit(
            _proposal(), _decision(), last_price=100.0, book_depth_usd=0.0
        ))
        self.assertEqual(order.filled_qty, 0.0)
        self.assertEqual(order.status, "CANCELED")

    def test_thin_depth_has_no_forced_minimum_fill(self) -> None:
        engine = _engine()
        order = asyncio.run(engine.submit(
            _proposal(), _decision(), last_price=100.0, book_depth_usd=1.0
        ))
        self.assertGreater(order.filled_qty, 0.0)
        self.assertLess(order.filled_qty, order.ordered_qty * 0.01)
        self.assertNotAlmostEqual(order.filled_qty, order.ordered_qty * 0.30, places=3)

    def test_invalid_reference_prices_rejected_before_simulation(self) -> None:
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(last_price=bad):
                engine = _engine()
                order = asyncio.run(engine.submit(
                    _proposal(), _decision(), last_price=bad, book_depth_usd=1000.0
                ))
                self.assertEqual(order.status, "REJECTED")
                self.assertEqual(order.filled_qty, 0.0)
                self.assertTrue(any(
                    item.get("event") == "REJECTED" for item in order.audit_trail
                ))


if __name__ == "__main__":
    unittest.main()
