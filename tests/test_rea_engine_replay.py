"""TDD tests for deterministic REA candle-event replay."""
import importlib
import unittest
from decimal import Decimal

from research.rea_engines.contracts import Candle, CostProfile, OrderIntent


class SequenceStrategy:
    strategy_id = "test-sequence"

    def __init__(self, intents_by_index):
        self.intents_by_index = intents_by_index
        self.index = 0

    def on_candle(self, candle, portfolio_snapshot, open_orders):
        intents = self.intents_by_index.get(self.index, [])
        self.index += 1
        return intents


class ReplayApiTests(unittest.TestCase):
    def test_replay_public_api_is_exposed(self):
        replay = importlib.import_module("research.rea_engines.replay")
        for name in ("Replay", "ReplayConfig", "RunReport", "ReplayError"):
            with self.subTest(api=name):
                self.assertTrue(hasattr(replay, name), f"missing replay API: {name}")

    def _candle(self, hour, open_, high, low, close, volume="10"):
        return Candle(
            timestamp=f"2026-01-01T{hour:02d}:00:00Z",
            open=open_, high=high, low=low, close=close, volume=volume,
        )

    def _config(self, ttl=None):
        from research.rea_engines.replay import ReplayConfig
        try:
            return ReplayConfig(
                starting_cash="1000",
                max_inventory="2",
                max_fill_qty="1",
                costs=CostProfile(fee_bps="10", spread_bps="5", slippage_bps="5"),
                default_order_ttl=ttl,
            )
        except TypeError as exc:
            self.fail(f"ReplayConfig must accept explicit replay limits: {exc}")

    def _run(self, candles, intents_by_index, ttl=None):
        from research.rea_engines.replay import Replay
        try:
            return Replay().run(candles, SequenceStrategy(intents_by_index), self._config(ttl))
        except NotImplementedError as exc:
            self.fail(f"Replay.run is not implemented: {exc}")

    def test_market_order_fills_next_open_and_attributes_costs_once(self):
        candles = [
            self._candle(0, "100", "101", "99", "100"),
            self._candle(1, "110", "112", "109", "111"),
            self._candle(2, "115", "116", "114", "115"),
        ]
        report = self._run(candles, {0: [OrderIntent("buy", "market", "1")]})
        self.assertEqual(len(report.fills), 1)
        fill = report.fills[0]
        self.assertEqual(fill.timestamp, candles[1].timestamp)
        self.assertEqual(fill.price, Decimal("110"))
        self.assertEqual(fill.fee, Decimal("0.110"))
        self.assertEqual(fill.spread_cost, Decimal("0.0275"))
        self.assertEqual(fill.slippage_cost, Decimal("0.055"))
        self.assertEqual(report.mode, "historical-paper")
        self.assertEqual(report.network_calls, 0)
        self.assertEqual(report.live_orders_submitted, 0)
        self.assertEqual(report.edge, "UNPROVEN")

    def test_market_order_submitted_on_last_bar_is_not_filled_same_bar(self):
        candles = [self._candle(0, "100", "101", "99", "100")]
        report = self._run(candles, {0: [OrderIntent("buy", "market", "1")]})
        self.assertEqual(report.fills, ())
        self.assertEqual(len(report.pending_orders), 1)

    def test_limit_order_persists_then_fills_at_favorable_gap_open(self):
        candles = [
            self._candle(0, "100", "101", "98", "100"),
            self._candle(1, "97", "99", "96", "97"),
            self._candle(2, "94", "96", "93", "95"),
        ]
        report = self._run(candles, {0: [OrderIntent("buy", "limit", "1", limit_price="95")]})
        self.assertEqual(len(report.fills), 1)
        fill = report.fills[0]
        self.assertEqual(fill.timestamp, candles[2].timestamp)
        self.assertEqual(fill.price, Decimal("94"))
        self.assertEqual(fill.spread_cost, Decimal("0"))
        self.assertEqual(fill.slippage_cost, Decimal("0"))
        self.assertEqual(report.pending_orders, ())

    def test_limit_order_expires_after_configured_number_of_eligible_bars(self):
        candles = [
            self._candle(0, "100", "101", "98", "100"),
            self._candle(1, "99", "100", "98", "99"),
        ]
        report = self._run(
            candles,
            {0: [OrderIntent("buy", "limit", "1", limit_price="95", expires_after_bars=1)]},
        )
        self.assertEqual(report.fills, ())
        self.assertEqual(report.pending_orders, ())
        self.assertEqual(len(report.expired_orders), 1)

    def test_limit_sell_can_fill_at_better_open_after_inventory_exists(self):
        candles = [
            self._candle(0, "100", "101", "99", "100"),
            self._candle(1, "100", "101", "99", "100"),
            self._candle(2, "106", "108", "105", "107"),
        ]
        report = self._run(candles, {
            0: [OrderIntent("buy", "market", "1")],
            1: [OrderIntent("sell", "limit", "1", limit_price="105")],
        })
        self.assertEqual([fill.side for fill in report.fills], ["buy", "sell"])
        self.assertEqual(report.fills[1].price, Decimal("106"))
        self.assertEqual(report.metrics["inventory_qty"], "0")

    def test_empty_and_non_monotonic_candle_sequences_fail_closed(self):
        replay_module = importlib.import_module("research.rea_engines.replay")
        replay = replay_module.Replay()
        config = self._config()
        with self.assertRaises(replay_module.ReplayError):
            replay.run([], SequenceStrategy({}), config)
        first = self._candle(1, "100", "101", "99", "100")
        second = self._candle(1, "100", "101", "99", "100")
        with self.assertRaises(replay_module.ReplayError):
            replay.run([first, second], SequenceStrategy({}), config)

    def test_strategy_can_cancel_a_resting_order_explicitly(self):
        contracts = importlib.import_module("research.rea_engines.contracts")
        cancel_type = getattr(contracts, "CancelIntent", None)
        self.assertIsNotNone(cancel_type, "missing explicit CancelIntent contract")
        if cancel_type is None:
            return

        class CancelStrategy:
            strategy_id = "cancel-test"

            def __init__(self):
                self.index = 0

            def on_candle(self, candle, portfolio_snapshot, open_orders):
                if self.index == 0:
                    result = [OrderIntent("buy", "limit", "1", limit_price="95")]
                elif self.index == 1 and open_orders:
                    result = [cancel_type(order_id=open_orders[0].order_id, tag="cancel-flat")]
                else:
                    result = []
                self.index += 1
                return result

        candles = [
            self._candle(0, "100", "101", "98", "100"),
            self._candle(1, "99", "100", "98", "99"),
            self._candle(2, "98", "100", "97", "99"),
        ]
        from research.rea_engines.replay import Replay
        try:
            report = Replay().run(candles, CancelStrategy(), self._config())
        except Exception as exc:
            self.fail(f"Replay must process CancelIntent without stopping: {type(exc).__name__}: {exc}")
        self.assertEqual(report.pending_orders, ())
        self.assertEqual(len(report.cancelled_orders), 1)
        self.assertEqual(report.cancelled_orders[0]["reason"], "strategy_cancelled")

    def test_sell_without_inventory_is_rejected_without_shorting(self):
        candles = [
            self._candle(0, "100", "101", "99", "100"),
            self._candle(1, "100", "101", "99", "100"),
        ]
        report = self._run(candles, {0: [OrderIntent("sell", "market", "1")]})
        self.assertEqual(report.fills, ())
        self.assertEqual(len(report.rejections), 1)
        self.assertIn("short position", report.rejections[0].reason)
        self.assertEqual(report.metrics["inventory_qty"], "0")

    def test_report_digest_is_stable_for_identical_inputs(self):
        candles = [
            self._candle(0, "100", "101", "99", "100"),
            self._candle(1, "101", "103", "100", "102"),
        ]
        first = self._run(candles, {0: [OrderIntent("buy", "market", "1")]})
        second = self._run(candles, {0: [OrderIntent("buy", "market", "1")]})
        self.assertEqual(first.report_sha256, second.report_sha256)


if __name__ == "__main__":
    unittest.main()
