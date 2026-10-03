"""ORDER096 research-only deterministic technical baselines.

These implementations are independent mathematical reimplementations of common
indicator concepts. They are not copied TradingView/Pine sources, are not wired
into SENEX prediction or GPTrader paths, and exist only to measure redundancy
and incremental information under frozen offline experiments.

All functions are causal: value[t] depends only on candles[:t+1].
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import sqrt
from typing import Iterable


SignalSeries = list[int]


def _f(value) -> float:
    return float(value)


def _close(candles: list[dict]) -> list[float]:
    return [_f(row["close"]) for row in candles]


def _sma(values: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        raise ValueError("period must be positive")
    window: list[float | None] = []
    total = 0.0
    valid = 0
    for i, value in enumerate(values):
        window.append(value)
        if value is not None:
            total += value
            valid += 1
        if len(window) > period:
            old = window.pop(0)
            if old is not None:
                total -= old
                valid -= 1
        if len(window) == period and valid == period:
            out[i] = total / period
    return out


def _ema(values: list[float | None], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    alpha = 2.0 / (period + 1.0)
    out: list[float | None] = [None] * len(values)
    state: float | None = None
    for i, value in enumerate(values):
        if value is None:
            continue
        state = value if state is None else alpha * value + (1.0 - alpha) * state
        out[i] = state
    return out


def _true_range(candles: list[dict]) -> list[float]:
    out: list[float] = []
    for i, row in enumerate(candles):
        high = _f(row["high"])
        low = _f(row["low"])
        if i == 0:
            out.append(high - low)
            continue
        prev_close = _f(candles[i - 1]["close"])
        out.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return out


def _atr(candles: list[dict], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    tr = _true_range(candles)

    out: list[float | None] = [None] * len(candles)
    state: float | None = None
    for i, value in enumerate(tr):
        if i == period - 1:
            state = sum(tr[:period]) / period
            out[i] = state
        elif i >= period and state is not None:
            state = (state * (period - 1) + value) / period
            out[i] = state
    return out


def _linreg(values: list[float | None], period: int) -> list[float | None]:
    if period <= 1:
        raise ValueError("period must be > 1")
    out: list[float | None] = [None] * len(values)
    sx = period * (period - 1) / 2.0
    sxx = (period - 1) * period * (2 * period - 1) / 6.0
    den = period * sxx - sx * sx
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        if any(value is None for value in window):
            continue
        ys = [float(value) for value in window if value is not None]
        sy = sum(ys)
        sxy = sum(j * value for j, value in enumerate(ys))
        slope = (period * sxy - sx * sy) / den
        intercept = (sy - slope * sx) / period
        out[i] = intercept + slope * (period - 1)
    return out


def momentum_1(candles: list[dict]) -> SignalSeries:
    out = [0] * len(candles)
    for i in range(1, len(candles)):
        delta = _f(candles[i]["close"]) - _f(candles[i - 1]["close"])
        out[i] = 1 if delta > 0 else -1 if delta < 0 else 0
    return out


def supertrend(
    candles: list[dict],
    *,
    period: int = 10,
    multiplier: float = 3.0,
) -> SignalSeries:
    """KivancOzbilgic-style SuperTrend state with default RMA ATR.

    The flip uses the previous trailing bands, matching the public Pine
    mechanics. The lower band is bullish; the upper band is bearish.
    """
    atr = _atr(candles, period)
    out = [0] * len(candles)
    prev_up: float | None = None
    prev_dn: float | None = None
    trend = 1

    for i, row in enumerate(candles):
        atr_i = atr[i]
        if atr_i is None:
            continue
        close = _f(row["close"])
        src = (_f(row["high"]) + _f(row["low"])) / 2.0
        basic_up = src - multiplier * atr_i
        basic_dn = src + multiplier * atr_i

        if prev_up is None or prev_dn is None:
            up = basic_up
            dn = basic_dn
            trend = 1
        else:
            prev_close = _f(candles[i - 1]["close"])
            up = max(basic_up, prev_up) if prev_close > prev_up else basic_up
            dn = min(basic_dn, prev_dn) if prev_close < prev_dn else basic_dn

            if trend == -1 and close > prev_dn:
                trend = 1
            elif trend == 1 and close < prev_up:
                trend = -1

        out[i] = trend
        prev_up = up
        prev_dn = dn
    return out

def chandelier(
    candles: list[dict],
    *,
    period: int = 22,
    multiplier: float = 3.0,
) -> SignalSeries:
    """Chandelier long/short trailing-state baseline."""
    atr = _atr(candles, period)
    out = [0] * len(candles)
    state = 1
    prev_long: float | None = None
    prev_short: float | None = None

    for i in range(period - 1, len(candles)):
        atr_i = atr[i]
        if atr_i is None:
            continue
        window = candles[i - period + 1 : i + 1]
        # everget default: useClose=true for extrema.
        highest = max(_f(row["close"]) for row in window)
        lowest = min(_f(row["close"]) for row in window)
        long_exit = highest - multiplier * atr_i
        short_exit = lowest + multiplier * atr_i
        prev_close = _f(candles[i - 1]["close"]) if i else _f(candles[i]["close"])

        if prev_long is not None and prev_close > prev_long:
            long_exit = max(long_exit, prev_long)
        if prev_short is not None and prev_close < prev_short:
            short_exit = min(short_exit, prev_short)

        close = _f(candles[i]["close"])
        if prev_short is not None and close > prev_short:
            state = 1
        elif prev_long is not None and close < prev_long:
            state = -1

        out[i] = state
        prev_long = long_exit
        prev_short = short_exit
    return out


def zlsma(candles: list[dict], *, length: int = 32) -> SignalSeries:
    close = _close(candles)
    first = _linreg([float(v) for v in close], length)
    second = _linreg(first, length)
    out = [0] * len(candles)
    for i in range(len(candles)):
        if first[i] is None or second[i] is None:
            continue
        zero_lag = first[i] + (first[i] - second[i])
        delta = close[i] - zero_lag
        out[i] = 1 if delta > 0 else -1 if delta < 0 else 0
    return out


def wavetrend(
    candles: list[dict],
    *,
    channel_length: int = 10,
    average_length: int = 21,
) -> SignalSeries:
    ap = [
        (_f(row["high"]) + _f(row["low"]) + _f(row["close"])) / 3.0
        for row in candles
    ]
    esa = _ema(ap, channel_length)
    deviation: list[float | None] = []
    for value, mean in zip(ap, esa):
        deviation.append(None if mean is None else abs(value - mean))
    d = _ema(deviation, channel_length)
    ci: list[float | None] = []
    for value, mean, dev in zip(ap, esa, d):
        if mean is None or dev is None or dev == 0:
            ci.append(None)
        else:
            ci.append((value - mean) / (0.015 * dev))
    wt1 = _ema(ci, average_length)
    wt2 = _sma(wt1, 4)
    out = [0] * len(candles)
    for i, (fast, slow) in enumerate(zip(wt1, wt2)):
        if fast is None or slow is None:
            continue
        delta = fast - slow
        out[i] = 1 if delta > 0 else -1 if delta < 0 else 0
    return out


def utbot(
    candles: list[dict],
    *,
    key_value: float = 1.0,
    atr_period: int = 10,
) -> SignalSeries:
    """UT-Bot-family persistent position state from the public ATR stop logic.

    This is the persistent position state, not a fabricated signal on every bar.
    A direction changes only when price crosses the previous ATR trailing stop.
    """
    atr = _atr(candles, atr_period)
    out = [0] * len(candles)
    stop: float | None = None
    position = 0

    for i, row in enumerate(candles):
        atr_i = atr[i]
        if atr_i is None:
            continue
        src = _f(row["close"])
        loss = key_value * atr_i

        if stop is None:
            stop = src - loss
            out[i] = position
            continue

        prev_src = _f(candles[i - 1]["close"])
        prev_stop = stop
        if src > prev_stop and prev_src > prev_stop:
            stop = max(prev_stop, src - loss)
        elif src < prev_stop and prev_src < prev_stop:
            stop = min(prev_stop, src + loss)
        else:
            stop = src - loss if src > prev_stop else src + loss

        if prev_src < prev_stop and src > prev_stop:
            position = 1
        elif prev_src > prev_stop and src < prev_stop:
            position = -1
        out[i] = position

    return out

def squeeze_momentum(
    candles: list[dict],
    *,
    length: int = 20,
    bb_multiplier: float = 2.0,
    kc_multiplier: float = 1.5,
) -> tuple[SignalSeries, SignalSeries]:
    """LazyBear-style squeeze concept: BB-vs-KC state + linreg momentum.

    Returns (momentum_direction, release_direction).  Release is emitted only
    on a transition from squeeze-on to squeeze-off.
    """
    close = _close(candles)
    basis = _sma(close, length)
    true_range_ma = _sma(_true_range(candles), length)
    raw: list[float | None] = [None] * len(candles)

    for i in range(length - 1, len(candles)):
        window = candles[i - length + 1 : i + 1]
        highest = max(_f(row["high"]) for row in window)
        lowest = min(_f(row["low"]) for row in window)
        mean = basis[i]
        if mean is None:
            continue
        center = ((highest + lowest) / 2.0 + mean) / 2.0
        raw[i] = close[i] - center

    momentum_value = _linreg(raw, length)
    direction = [0] * len(candles)
    release = [0] * len(candles)
    prev_squeeze: bool | None = None

    for i in range(length - 1, len(candles)):
        mean = basis[i]
        range_i = true_range_ma[i]
        if mean is None or range_i is None:
            continue
        window = close[i - length + 1 : i + 1]
        variance = sum((value - mean) ** 2 for value in window) / length
        sd = sqrt(variance)
        upper_bb = mean + bb_multiplier * sd
        lower_bb = mean - bb_multiplier * sd
        upper_kc = mean + kc_multiplier * range_i
        lower_kc = mean - kc_multiplier * range_i
        squeeze_on = lower_bb > lower_kc and upper_bb < upper_kc

        value = momentum_value[i]
        if value is not None:
            direction[i] = 1 if value > 0 else -1 if value < 0 else 0
        if prev_squeeze is True and not squeeze_on:
            release[i] = direction[i]
        prev_squeeze = squeeze_on

    return direction, release


def _utc_day(timestamp_ms: int | float) -> str:
    return datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc).date().isoformat()


def vwap_ema_bias(
    candles: list[dict],
    *,
    fast: int = 9,
    slow: int = 21,
) -> SignalSeries:
    """UTC-session VWAP + EMA fast/slow directional bias.

    Crypto is 24/7, so the UTC day reset is explicit rather than implicit.
    This baseline deliberately tests the directional bias only, not the
    pullback/reclaim trigger.
    """
    close = _close(candles)
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    out = [0] * len(candles)
    current_day: str | None = None
    cumulative_pv = 0.0
    cumulative_volume = 0.0

    for i, row in enumerate(candles):
        day = _utc_day(row["open_time"])
        if day != current_day:
            current_day = day
            cumulative_pv = 0.0
            cumulative_volume = 0.0

        volume = max(0.0, _f(row["volume"]))
        typical = (_f(row["high"]) + _f(row["low"]) + _f(row["close"])) / 3.0
        cumulative_pv += typical * volume
        cumulative_volume += volume
        if cumulative_volume <= 0 or ema_fast[i] is None or ema_slow[i] is None:
            continue

        vwap = cumulative_pv / cumulative_volume
        close_i = close[i]
        if close_i > vwap and ema_fast[i] > ema_slow[i]:
            out[i] = 1
        elif close_i < vwap and ema_fast[i] < ema_slow[i]:
            out[i] = -1
    return out


def _previous_completed_htf_ranges(
    candles: list[dict],
    *,
    target_ms: int,
    base_interval_ms: int,
) -> list[tuple[float, float] | None]:
    """Return the prior fully completed HTF high/low available at each row.

    This deliberately uses only the immediately preceding fixed UTC bucket and
    fails closed when that bucket is missing one or more expected lower-timeframe
    bars. It is a minimal, reproducible proxy for HTF liquidity/range concepts.
    """
    if target_ms <= 0:
        raise ValueError("target_ms must be positive")
    if base_interval_ms <= 0:
        raise ValueError("base_interval_ms must be positive")
    out: list[tuple[float, float] | None] = [None] * len(candles)
    if len(candles) < 2:
        return out

    times = [int(row["open_time"]) for row in candles]
    if target_ms % base_interval_ms != 0:
        return out
    expected = target_ms // base_interval_ms
    if expected <= 0:
        return out

    buckets: dict[int, list[dict]] = {}
    for row in candles:
        ts = int(row["open_time"])
        bucket = (ts // target_ms) * target_ms
        buckets.setdefault(bucket, []).append(row)

    completed: dict[int, tuple[float, float]] = {}
    for bucket, rows in buckets.items():
        if len(rows) != expected:
            continue
        ordered = sorted(rows, key=lambda row: int(row["open_time"]))
        if any(
            int(ordered[i]["open_time"]) - int(ordered[i - 1]["open_time"]) != base_interval_ms
            for i in range(1, len(ordered))
        ):
            continue
        completed[bucket] = (
            max(_f(row["high"]) for row in ordered),
            min(_f(row["low"]) for row in ordered),
        )

    for i, ts in enumerate(times):
        current_bucket = (ts // target_ms) * target_ms
        out[i] = completed.get(current_bucket - target_ms)
    return out


def htf_discount_reversion(
    candles: list[dict],
    *,
    target_ms: int = 4 * 60 * 60 * 1000,
    base_interval_ms: int,
) -> SignalSeries:
    """Mean-reversion baseline from the prior completed HTF range midpoint.

    Below midpoint => +1 (discount / long hypothesis).
    Above midpoint => -1 (premium / short hypothesis).
    Missing/incomplete prior HTF range => 0.
    """
    ranges = _previous_completed_htf_ranges(
        candles,
        target_ms=target_ms,
        base_interval_ms=base_interval_ms,
    )
    out = [0] * len(candles)
    for i, prior in enumerate(ranges):
        if prior is None:
            continue
        high, low = prior
        midpoint = (high + low) / 2.0
        close = _f(candles[i]["close"])
        out[i] = 1 if close < midpoint else -1 if close > midpoint else 0
    return out


def htf_sweep_reclaim(
    candles: list[dict],
    *,
    target_ms: int = 4 * 60 * 60 * 1000,
    base_interval_ms: int,
) -> SignalSeries:
    """Sparse reversal baseline from sweeps of the prior completed HTF range.

    Long: current low trades below prior HTF low, then closes back above it.
    Short: current high trades above prior HTF high, then closes back below it.
    Bars sweeping both sides are ambiguous and fail closed to 0.
    """
    ranges = _previous_completed_htf_ranges(
        candles,
        target_ms=target_ms,
        base_interval_ms=base_interval_ms,
    )
    out = [0] * len(candles)
    for i, prior in enumerate(ranges):
        if prior is None:
            continue
        prior_high, prior_low = prior
        high = _f(candles[i]["high"])
        low = _f(candles[i]["low"])
        close = _f(candles[i]["close"])
        swept_low = low < prior_low and close > prior_low
        swept_high = high > prior_high and close < prior_high
        if swept_low and not swept_high:
            out[i] = 1
        elif swept_high and not swept_low:
            out[i] = -1
    return out


def compute_baseline_matrix(
    candles: Iterable[dict],
    *,
    base_interval_ms: int,
) -> dict[str, SignalSeries]:
    rows = [dict(row) for row in candles]
    squeeze_direction, squeeze_release = squeeze_momentum(rows, length=20)
    return {
        "momentum_1": momentum_1(rows),
        "supertrend_10_3": supertrend(rows, period=10, multiplier=3.0),
        "chandelier_22_3": chandelier(rows, period=22, multiplier=3.0),
        "zlsma_32": zlsma(rows, length=32),
        "wavetrend_10_21": wavetrend(rows, channel_length=10, average_length=21),
        "utbot_1_10": utbot(rows, key_value=1.0, atr_period=10),
        "squeeze_momentum_20": squeeze_direction,
        "squeeze_release_20": squeeze_release,
        "vwap_ema_9_21_bias": vwap_ema_bias(rows, fast=9, slow=21),
        "htf_discount_reversion_4h": htf_discount_reversion(
            rows,
            base_interval_ms=base_interval_ms,
        ),
        "htf_sweep_reclaim_4h": htf_sweep_reclaim(
            rows,
            base_interval_ms=base_interval_ms,
        ),
    }
