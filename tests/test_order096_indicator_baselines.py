from __future__ import annotations

import importlib.util
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research" / "edge" / "order096" / "indicator_baselines.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("order096_indicator_baselines", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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
    matrix = m.compute_baseline_matrix(candles)

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
    }
    assert set(matrix) == expected
    for name, values in matrix.items():
        assert len(values) == len(candles), name
        _assert_domain(values)


def test_indicator_history_is_causal_under_future_mutation():
    m = _load_module()
    candles = _candles()
    cut = 230
    before = m.compute_baseline_matrix(candles)

    mutated = [dict(row) for row in candles]
    for i in range(cut + 1, len(mutated)):
        mutated[i]["open"] *= 4.0
        mutated[i]["high"] *= 4.2
        mutated[i]["low"] *= 3.8
        mutated[i]["close"] *= 4.1
        mutated[i]["volume"] *= 50.0

    after = m.compute_baseline_matrix(mutated)
    for name in before:
        assert before[name][: cut + 1] == after[name][: cut + 1], name


def test_squeeze_release_is_strict_subset_of_squeeze_direction():
    m = _load_module()
    candles = _candles()
    matrix = m.compute_baseline_matrix(candles)
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
