from datetime import datetime, timedelta, timezone
import importlib
import math
import unittest

try:
    regime_module = importlib.import_module("research.rea_engines.tools.classify_market_regimes")
except ModuleNotFoundError:
    regime_module = None


def require_classifier():
    if regime_module is None or not callable(getattr(regime_module, "classify_market_regimes", None)):
        raise AssertionError("market-regime classifier API must exist")
    return regime_module.classify_market_regimes


def make_candles(closes, start=None, interval_hours=4):
    start = start or datetime(2020, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "timestamp": (start + timedelta(hours=interval_hours * i)).isoformat().replace("+00:00", "Z"),
            "close": str(close),
        }
        for i, close in enumerate(closes)
    ]


class MarketRegimeContractTests(unittest.TestCase):
    def test_emits_only_out_of_sample_rows_with_trend_and_volatility_labels(self):
        classify = require_classifier()
        closes = [100 + i * 0.4 for i in range(36)]
        candles = make_candles(closes)
        train_end = candles[23]["timestamp"]
        rows = classify(
            candles,
            train_end=train_end,
            lookback_bars=4,
            volatility_quantile=0.75,
            trend_threshold_pct=1.0,
            bars_per_day=6,
            interval_hours=4,
        )
        self.assertTrue(rows)
        self.assertTrue(all(row["timestamp"] > train_end for row in rows))
        self.assertTrue(all(row["trend_regime"] in {"UPTREND", "DOWNTREND", "RANGE"} for row in rows))
        self.assertTrue(all(row["volatility_regime"] in {"HIGH_VOL", "LOW_VOL"} for row in rows))
        self.assertTrue(all(row["regime"] == row["trend_regime"] + "_" + row["volatility_regime"] for row in rows))
        self.assertEqual(rows[-1]["trend_regime"], "UPTREND")

    def test_future_candle_changes_do_not_rewrite_prior_regime_features(self):
        classify = require_classifier()
        closes = [100 + math.sin(i / 2) for i in range(50)]
        candles = make_candles(closes)
        train_end = candles[29]["timestamp"]
        before = classify(candles, train_end=train_end, lookback_bars=5, interval_hours=4)
        changed = list(candles)
        changed_start = datetime.fromisoformat(candles[40]["timestamp"].replace("Z", "+00:00"))
        changed[40:] = make_candles([5000 + i * 100 for i in range(10)], start=changed_start)
        after = classify(changed, train_end=train_end, lookback_bars=5, interval_hours=4)
        by_time_before = {row["timestamp"]: row for row in before}
        by_time_after = {row["timestamp"]: row for row in after}
        for timestamp in by_time_before:
            if timestamp < candles[40]["timestamp"]:
                self.assertEqual(by_time_before[timestamp], by_time_after[timestamp])

    def test_rejects_non_chronological_candles_and_insufficient_training_volatility(self):
        classify = require_classifier()
        candles = make_candles([100, 101, 102, 103, 104, 105, 106, 107])
        bad = list(candles)
        bad[3] = dict(bad[2])
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            classify(bad, train_end=candles[5]["timestamp"], lookback_bars=2)
        with self.assertRaisesRegex(ValueError, "training volatility"):
            classify(candles, train_end=candles[1]["timestamp"], lookback_bars=2)

    def test_ignores_windows_crossing_a_missing_interval_instead_of_inventing_bars(self):
        classify = require_classifier()
        candles = make_candles([100 + i for i in range(24)])
        for i in range(10, len(candles)):
            parsed = datetime.fromisoformat(candles[i]["timestamp"].replace("Z", "+00:00")) + timedelta(hours=4)
            candles[i]["timestamp"] = parsed.isoformat().replace("+00:00", "Z")
        train_end = candles[7]["timestamp"]
        rows = classify(candles, train_end=train_end, lookback_bars=3, interval_hours=4)
        timestamps = {row["timestamp"] for row in rows}
        self.assertIn(candles[8]["timestamp"], timestamps)
        self.assertNotIn(candles[10]["timestamp"], timestamps)
        self.assertNotIn(candles[11]["timestamp"], timestamps)
        self.assertNotIn(candles[12]["timestamp"], timestamps)
        self.assertIn(candles[13]["timestamp"], timestamps)


if __name__ == "__main__":
    unittest.main()
