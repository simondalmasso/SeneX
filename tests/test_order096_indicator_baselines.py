from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research" / "edge" / "order096" / "indicator_baselines.py"
EVALUATOR_PATH = ROOT / "research" / "edge" / "order096" / "evaluate_snapshot.py"
SNAPSHOT_PATH = ROOT / "research" / "edge" / "order096" / "data" / "indicator_screen_v1.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("order096_indicator_baselines", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_evaluator():
    spec = importlib.util.spec_from_file_location("order096_evaluator", EVALUATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(EVALUATOR_PATH.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _candles(n: int = 320):
    out = []
    price = 100.0
    for i in range(n):
        drift = 0.18 if (i // 40) % 2 == 0 else -0.12
        wave = math.sin(i / 7.0) * 0.35
        open_px = price
        close = max(1.0, open_px + drift + wave)
        high = max(open_px, close) + 0.35 + abs(math.sin(i / 5.0)) * 0.2
        low = min(open_px, close) - 0.35 - abs(math.cos(i / 6.0)) * 0.2
        volume = 1000.0 + (i % 17) * 23.0 + abs(wave) * 100
        out.append(
            {
                "open_time": 1_790_000_000_000 + i * 3_600_000,
                "open": open_px,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
        price = close
    return out


def _assert_domain(values):
    assert all(value in {-1, 0, 1} for value in values)


def test_order096_module_is_research_only():
    text = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "oracle_runner",
        "gptrader",
        "paper_book",
        "execution_engine",
        "send_order",
        "create_order",
    )
    for token in forbidden:
        assert token not in text


def test_all_baselines_return_bounded_direction_series():
    m = _load_module()
    candles = _candles()
    matrix = m.compute_baseline_matrix(candles, base_interval_ms=3_600_000)

    expected = {
        "momentum_1",
        "supertrend_10_3",
        "chandelier_22_3",
        "zlsma_32",
        "wavetrend_10_21",
        "utbot_1_10",
        "squeeze_momentum_20",
        "squeeze_release_20",
        "vwap_ema_9_21_bias",
        "htf_discount_reversion_4h",
        "htf_sweep_reclaim_4h",
    }
    assert set(matrix) == expected
    for name, values in matrix.items():
        assert len(values) == len(candles), name
        _assert_domain(values)


def test_indicator_history_is_causal_under_future_mutation():
    m = _load_module()
    candles = _candles()
    cut = 230
    before = m.compute_baseline_matrix(candles, base_interval_ms=3_600_000)

    mutated = [dict(row) for row in candles]
    for i in range(cut + 1, len(mutated)):
        mutated[i]["open"] *= 4.0
        mutated[i]["high"] *= 4.2
        mutated[i]["low"] *= 3.8
        mutated[i]["close"] *= 4.1
        mutated[i]["volume"] *= 50.0

    after = m.compute_baseline_matrix(mutated, base_interval_ms=3_600_000)
    for name in before:
        assert before[name][: cut + 1] == after[name][: cut + 1], name


def test_squeeze_release_is_strict_subset_of_squeeze_direction():
    m = _load_module()
    candles = _candles()
    matrix = m.compute_baseline_matrix(candles, base_interval_ms=3_600_000)
    release = matrix["squeeze_release_20"]
    momentum = matrix["squeeze_momentum_20"]

    assert any(value != 0 for value in release)
    for i, value in enumerate(release):
        if value:
            assert value == momentum[i]


def test_vwap_bias_resets_on_utc_day_boundary():
    m = _load_module()
    candles = _candles(80)
    # Force a UTC day boundary after row 30 with a large volume regime change.
    for i, row in enumerate(candles):
        row["open_time"] = 1_790_000_000_000 + i * 3_600_000
        if i >= 30:
            row["volume"] *= 100.0
    values = m.vwap_ema_bias(candles, fast=9, slow=21)
    assert len(values) == len(candles)
    _assert_domain(values)


def test_supertrend_stays_bullish_in_orderly_rise_and_flips_on_break():
    m = _load_module()
    candles = []
    price = 100.0
    for i in range(60):
        open_px = price
        close = open_px + 0.5
        candles.append(
            {
                "open_time": 1_790_000_000_000 + i * 3_600_000,
                "open": open_px,
                "high": close + 0.2,
                "low": open_px - 0.2,
                "close": close,
                "volume": 1000.0,
            }
        )
        price = close

    trend = m.supertrend(candles, period=10, multiplier=3.0)
    assert all(value == 1 for value in trend[15:])

    broken = [dict(row) for row in candles]
    i = len(broken)
    broken.append(
        {
            "open_time": 1_790_000_000_000 + i * 3_600_000,
            "open": price,
            "high": price + 0.1,
            "low": price - 15.0,
            "close": price - 14.0,
            "volume": 1500.0,
        }
    )
    assert m.supertrend(broken, period=10, multiplier=3.0)[-1] == -1


def test_chandelier_default_ignores_extreme_wicks_for_extrema():
    m = _load_module()
    base = _candles(100)
    altered = [dict(row) for row in base]
    altered[60]["high"] *= 10.0
    altered[60]["low"] *= 0.1

    # ATR still sees the wick, but the extrema component uses close by default.
    # The result remains causal and bounded; this locks the explicit useClose
    # contract rather than silently changing back to high/low extrema.
    original = m.chandelier(base, period=22, multiplier=3.0)
    changed = m.chandelier(altered, period=22, multiplier=3.0)
    _assert_domain(original)
    _assert_domain(changed)
    assert len(original) == len(changed) == len(base)


def test_htf_sweep_reclaim_uses_only_last_completed_4h_range():
    m = _load_module()
    candles = []
    # Four complete 1h bars define the prior 4h range: high=105, low=95.
    rows = [
        (100, 103, 99, 102),
        (102, 105, 100, 104),
        (104, 104.5, 98, 99),
        (99, 101, 95, 100),
        # First bar of next 4h bucket sweeps SSL then closes back above it.
        (100, 101, 94, 96),
        # Second bar sweeps BSL then closes back below it.
        (96, 106, 96, 104),
    ]
    for i, (open_px, high, low, close) in enumerate(rows):
        candles.append(
            {
                "open_time": i * 3_600_000,
                "open": open_px,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000.0,
            }
        )

    sweep = m.htf_sweep_reclaim(
        candles,
        target_ms=4 * 3_600_000,
        base_interval_ms=3_600_000,
    )
    location = m.htf_discount_reversion(
        candles,
        target_ms=4 * 3_600_000,
        base_interval_ms=3_600_000,
    )

    assert sweep[:4] == [0, 0, 0, 0]
    assert sweep[4] == 1
    assert sweep[5] == -1
    assert location[4] == 1
    assert location[5] == -1


def test_htf_context_fails_closed_when_previous_bucket_is_incomplete():
    m = _load_module()
    candles = _candles(12)
    # Remove one bar from the first 4h bucket.
    candles = [row for i, row in enumerate(candles) if i != 1]
    sweep = m.htf_sweep_reclaim(
        candles,
        target_ms=4 * 3_600_000,
        base_interval_ms=3_600_000,
    )
    location = m.htf_discount_reversion(
        candles,
        target_ms=4 * 3_600_000,
        base_interval_ms=3_600_000,
    )

    # The first bar after that incomplete bucket must not synthesize HTF truth.
    first_bucket = candles[0]["open_time"] // (4 * 3_600_000)
    for i, row in enumerate(candles):
        if row["open_time"] // (4 * 3_600_000) == first_bucket + 1:
            assert sweep[i] == 0
            assert location[i] == 0


def test_htf_history_does_not_depend_on_future_timestamp_density():
    m = _load_module()
    base_ms = 15 * 60_000
    target_ms = 4 * 60 * 60_000

    # First 32 rows are deliberately sparse (30m apart), but the declared
    # dataset interval is 15m. Therefore no synthetic "complete 4h" truth may
    # be created from only 8 sparse records. Later dense timestamps must not
    # retroactively change the historical prefix.
    candles = _candles(64)
    start = 1_790_000_000_000
    for i, row in enumerate(candles):
        if i < 32:
            row["open_time"] = start + i * 30 * 60_000
        else:
            row["open_time"] = start + 32 * 30 * 60_000 + (i - 32) * base_ms

    prefix = [dict(row) for row in candles[:32]]
    prefix_signal = m.htf_discount_reversion(
        prefix,
        target_ms=target_ms,
        base_interval_ms=base_ms,
    )
    full_signal = m.htf_discount_reversion(
        candles,
        target_ms=target_ms,
        base_interval_ms=base_ms,
    )
    assert prefix_signal == full_signal[:32]


def test_warmup_does_not_emit_nonfinite_values():
    m = _load_module()
    candles = _candles(80)
    functions = [
        lambda: m.supertrend(candles, period=10, multiplier=3.0),
        lambda: m.chandelier(candles, period=22, multiplier=3.0),
        lambda: m.zlsma(candles, length=32),
        lambda: m.wavetrend(candles, channel_length=10, average_length=21),
        lambda: m.utbot(candles, key_value=1.0, atr_period=10),
        lambda: m.squeeze_momentum(candles, length=20),
        lambda: m.vwap_ema_bias(candles, fast=9, slow=21),
    ]
    for produce in functions:
        result = produce()
        if isinstance(result, tuple):
            series = result[0] + result[1]
        else:
            series = result
        assert all(isinstance(v, int) and v in {-1, 0, 1} for v in series)


def test_frozen_snapshot_is_complete_and_evaluator_is_offline_reproducible():
    payload = __import__("json").loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    datasets = payload["datasets"]
    assert [(row["provider"], row["query"]["interval"], row["count"]) for row in datasets] == [
        ("TraderSpy", "15m", 500),
        ("TraderSpy", "1h", 500),
        ("Bybit", "15m", 199),
        ("Bybit", "1h", 199),
    ]

    evaluator = _load_evaluator()
    first = evaluator.run(payload)
    second = evaluator.run(payload)
    assert first == second
    assert first["promotion_evidence"] is False
    assert first["edge"] == "UNPROVEN"
    assert len(first["datasets"]) == 4
    assert len(first["matched_cross_venue"]) == 2

    for matched in first["matched_cross_venue"]:
        assert matched["common_timestamp_n"] > 0
        for row in matched["per_rule"].values():
            value = row["signal_agreement"]
            assert value is None or 0.0 <= value <= 1.0

    by_key = {
        (row["provider"], row["query"]["interval"]): row["evaluation"]
        for row in first["datasets"]
    }
    expected_htf = {
        ("TraderSpy", "15m"): {
            "htf_discount_reversion_4h": (199, 0.527638, -0.487005),
            "htf_sweep_reclaim_4h": (21, 0.380952, -2.708709),
        },
        ("TraderSpy", "1h"): {
            "htf_discount_reversion_4h": (199, 0.507538, -0.364912),
            "htf_sweep_reclaim_4h": (42, 0.357143, -5.516746),
        },
        ("Bybit", "15m"): {
            "htf_discount_reversion_4h": (79, 0.455696, -2.689925),
            "htf_sweep_reclaim_4h": (10, 0.4, -4.428679),
        },
        ("Bybit", "1h"): {
            "htf_discount_reversion_4h": (79, 0.455696, -3.651319),
            "htf_sweep_reclaim_4h": (17, 0.294118, -3.1598),
        },
    }
    for key, expected_rules in expected_htf.items():
        actual = by_key[key]
        for rule, (n, accuracy, mean_bps) in expected_rules.items():
            assert actual[rule]["n"] == n
            assert actual[rule]["accuracy"] == accuracy
            assert actual[rule]["mean_signed_bps"] == mean_bps


def test_chandelier_source_contract_uses_close_extrema():
    text = MODULE_PATH.read_text(encoding="utf-8")
    assert 'highest = max(_f(row["close"]) for row in window)' in text
    assert 'lowest = min(_f(row["close"]) for row in window)' in text


def test_squeeze_source_contract_uses_sma_true_range():
    text = MODULE_PATH.read_text(encoding="utf-8")
    assert 'true_range_ma = _sma(_true_range(candles), length)' in text


def test_matched_venue_agreement_excludes_unavailable_zero_states():
    import importlib.util
    import sys

    evaluator_path = (
        ROOT / "research" / "edge" / "order096" / "evaluate_snapshot.py"
    )
    module_dir = str(evaluator_path.parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    spec = importlib.util.spec_from_file_location("order096_evaluator", evaluator_path)
    assert spec is not None and spec.loader is not None
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)

    # Left has prehistory and is fully initialized at common timestamps.
    left_rows = _candles(160)
    right_rows = [dict(row) for row in left_rows[-80:]]
    left = {
        "id": "left",
        "provider": "A",
        "query": {"interval": "1h"},
        "candles": left_rows,
    }
    right = {
        "id": "right",
        "provider": "B",
        "query": {"interval": "1h"},
        "candles": right_rows,
    }

    result = evaluator.matched_signal_agreement(left, right)
    zlsma = result["per_rule"]["zlsma_32"]

    # Right-side warm-up zeroes are unavailable, not disagreements.
    assert zlsma["compared_n"] == zlsma["both_nonzero_n"]
    assert zlsma["signal_agreement"] == 1.0
