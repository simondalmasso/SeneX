"""Causal, descriptive trend/volatility labels for local OHLCV research data.

Volatility thresholds are estimated only from rolling features ending on or
before the training cutoff. Out-of-sample labels use trailing candles only and
omit windows crossing missing intervals; no bars are imputed and no network
calls are made. These labels are not trading signals or evidence of a profitable edge.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import pstdev
from typing import Sequence


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty timezone-aware ISO-8601 string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("timestamp must be ISO-8601") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return result.astimezone(timezone.utc)


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("training volatility window has no valid observations")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def classify_market_regimes(
    candles: Sequence[dict[str, object]],
    *,
    train_end: str,
    lookback_bars: int = 180,
    volatility_quantile: float = 0.75,
    trend_threshold_pct: float = 10.0,
    bars_per_day: int = 6,
    interval_hours: int = 4,
) -> list[dict[str, object]]:
    """Return labels strictly after train_end; all features are trailing.

    A 180-bar lookback represents 30 days on 4-hour bars. Annualized realized
    volatility is population standard deviation of log returns scaled by
    sqrt(bars_per_day * 365). Its threshold is fitted from the training window
    only and held fixed throughout out-of-sample labeling.
    """
    if isinstance(lookback_bars, bool) or not isinstance(lookback_bars, int) or lookback_bars < 2:
        raise ValueError("lookback_bars must be an integer of at least 2")
    if not isinstance(bars_per_day, int) or isinstance(bars_per_day, bool) or bars_per_day < 1:
        raise ValueError("bars_per_day must be a positive integer")
    if not isinstance(interval_hours, int) or isinstance(interval_hours, bool) or interval_hours < 1:
        raise ValueError("interval_hours must be a positive integer")
    if not math.isfinite(volatility_quantile) or not 0 < volatility_quantile < 1:
        raise ValueError("volatility_quantile must be strictly between 0 and 1")
    if not math.isfinite(trend_threshold_pct) or trend_threshold_pct <= 0:
        raise ValueError("trend_threshold_pct must be positive and finite")
    if not candles:
        raise ValueError("at least one candle is required")
    cutoff = _parse_timestamp(train_end)
    times: list[datetime] = []
    closes: list[float] = []
    for index, candle in enumerate(candles):
        if not isinstance(candle, dict):
            raise ValueError(f"candle {index} must be a mapping")
        stamp = _parse_timestamp(candle.get("timestamp"))
        if times and stamp <= times[-1]:
            raise ValueError("candle timestamps must be strictly increasing")
        try:
            close = float(candle.get("close"))
        except (TypeError, ValueError):
            raise ValueError(f"candle {index} close must be numeric") from None
        if not math.isfinite(close) or close <= 0:
            raise ValueError(f"candle {index} close must be finite and positive")
        times.append(stamp)
        closes.append(close)

    interval = timedelta(hours=interval_hours)
    annualizer = math.sqrt(bars_per_day * 365)
    features: list[dict[str, object]] = []
    for i in range(lookback_bars, len(candles)):
        start = i - lookback_bars
        # A feature window is valid only if every bar has the declared interval.
        if any(times[j] - times[j - 1] != interval for j in range(start + 1, i + 1)):
            continue
        log_returns = [
            math.log(closes[j] / closes[j - 1])
            for j in range(i - lookback_bars + 1, i + 1)
        ]
        momentum_pct = (closes[i] / closes[start] - 1.0) * 100.0
        volatility_pct = pstdev(log_returns) * annualizer * 100.0
        features.append({
            "timestamp": times[i].isoformat(timespec="seconds").replace("+00:00", "Z"),
            "close": closes[i],
            "momentum_pct": momentum_pct,
            "realized_volatility_ann_pct": volatility_pct,
            "_time": times[i],
        })

    training_volatilities = [
        float(item["realized_volatility_ann_pct"])
        for item in features
        if item["_time"] <= cutoff
    ]
    if len(training_volatilities) < 2:
        raise ValueError(
            "training volatility requires at least two valid rolling observations at or before train_end"
        )
    volatility_threshold = _quantile(training_volatilities, volatility_quantile)
    output: list[dict[str, object]] = []
    for item in features:
        if item["_time"] <= cutoff:
            continue
        momentum_pct = float(item["momentum_pct"])
        volatility_pct = float(item["realized_volatility_ann_pct"])
        if momentum_pct >= trend_threshold_pct:
            trend = "UPTREND"
        elif momentum_pct <= -trend_threshold_pct:
            trend = "DOWNTREND"
        else:
            trend = "RANGE"
        volatility = "HIGH_VOL" if volatility_pct > volatility_threshold else "LOW_VOL"
        output.append({
            "timestamp": item["timestamp"],
            "close": format(float(item["close"]), ".12g"),
            "momentum_pct": format(momentum_pct, ".12g"),
            "realized_volatility_ann_pct": format(volatility_pct, ".12g"),
            "training_volatility_threshold_pct": format(volatility_threshold, ".12g"),
            "trend_regime": trend,
            "volatility_regime": volatility,
            "regime": f"{trend}_{volatility}",
        })
    if not output:
        raise ValueError("no valid out-of-sample feature windows after train_end")
    return output


def _load_close_csv(path: Path) -> tuple[list[dict[str, object]], str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("input CSV must be UTF-8 text") from None
    rows: list[dict[str, object]] = []
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames or not {"timestamp", "close"}.issubset(set(reader.fieldnames)):
        raise ValueError("CSV must contain timestamp and close columns")
    for row_num, row in enumerate(reader, start=2):
        if row.get("timestamp") is None or row.get("close") is None:
            raise ValueError(f"CSV row {row_num} is missing timestamp or close")
        rows.append({"timestamp": row["timestamp"], "close": row["close"]})
    return rows, digest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="local OHLCV CSV; no network access")
    parser.add_argument("--output", required=True, type=Path, help="new CSV output path")
    parser.add_argument("--symbol", required=True, help="asset label, e.g. BTCUSDT")
    parser.add_argument("--train-end", default="2021-12-31T23:59:59Z")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--volatility-quantile", type=float, default=0.75)
    parser.add_argument("--trend-threshold-pct", type=float, default=10.0)
    parser.add_argument("--interval-hours", type=int, default=4)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.input.is_file():
            raise ValueError(f"input CSV does not exist: {args.input}")
        if args.lookback_days < 1 or args.interval_hours < 1 or 24 % args.interval_hours != 0:
            raise ValueError("lookback-days must be positive and interval-hours must divide 24")
        if not args.output.parent.is_dir():
            raise ValueError(f"output directory must already exist: {args.output.parent}")
        if args.output.exists() and not args.overwrite:
            raise ValueError(f"output already exists; use --overwrite explicitly: {args.output}")
        candles, source_sha256 = _load_close_csv(args.input)
        rows = classify_market_regimes(
            candles,
            train_end=args.train_end,
            lookback_bars=args.lookback_days * (24 // args.interval_hours),
            volatility_quantile=args.volatility_quantile,
            trend_threshold_pct=args.trend_threshold_pct,
            bars_per_day=24 // args.interval_hours,
            interval_hours=args.interval_hours,
        )
        fields = [
            "symbol", "timestamp", "close", "momentum_pct",
            "realized_volatility_ann_pct", "training_volatility_threshold_pct",
            "trend_regime", "volatility_regime", "regime", "train_end",
            "lookback_days", "volatility_quantile", "trend_threshold_pct",
            "source_sha256",
        ]
        for row in rows:
            row.update({
                "symbol": args.symbol.strip(),
                "train_end": args.train_end,
                "lookback_days": args.lookback_days,
                "volatility_quantile": args.volatility_quantile,
                "trend_threshold_pct": args.trend_threshold_pct,
                "source_sha256": source_sha256,
            })
        with args.output.open("w" if args.overwrite else "x", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
        counts = Counter(str(row["regime"]) for row in rows)
        print(json.dumps({
            "status": "completed",
            "mode": "historical-paper-descriptive",
            "network_calls": 0,
            "live_orders_submitted": 0,
            "EDGE": "UNPROVEN",
            "symbol": args.symbol,
            "input": str(args.input),
            "source_sha256": source_sha256,
            "oos_rows": len(rows),
            "first_timestamp": rows[0]["timestamp"],
            "last_timestamp": rows[-1]["timestamp"],
            "regime_counts": dict(sorted(counts.items())),
            "output": str(args.output),
        }, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
