from __future__ import annotations

from datetime import datetime, timezone


def _utc(value: str) -> datetime:
    dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_exact_clock_hour(value: str) -> bool:
    dt=_utc(value)
    return dt.minute==0 and dt.second==0 and dt.microsecond==0


def polymarket_hourly_label(open_price: float, close_price: float) -> str:
    return "UP" if float(close_price) >= float(open_price) else "DOWN"


def hyp008_design() -> dict:
    return {
        "hypothesis_id":"HYP-008",
        "name":"SENEX-POLYMARKET-SHADOW",
        "status":"DESIGNED_NOT_RUN",
        "purpose":"Evaluate frozen SENEX-derived signals at exact clock-hour boundaries against the exact Polymarket BTC Up/Down Hourly contract.",
        "signal_capture_time":"EXACT_CLOCK_HOUR_BOUNDARY",
        "signal_source":"SENEX_DERIVED_READ_ONLY_NO_CORE_MUTATION",
        "polymarket_series_id":"10114",
        "asset":"BTC/USDT",
        "source":"BINANCE_BTCUSDT",
        "candle":"FINALIZED_1H",
        "start_semantics":"BINANCE_1H_CANDLE_OPEN_AT_EXACT_CLOCK_HOUR",
        "end_semantics":"SAME_CANDLE_FINAL_CLOSE_EXACTLY_ONE_HOUR_LATER",
        "label":"UP_IF_CLOSE_GTE_OPEN_ELSE_DOWN",
        "tie_semantics":"UP_ON_EQUAL",
        "timestamps":"IDENTICAL_UTC_INSTANTS_TO_POLYMARKET_EVENT_START_END",
        "feature_time_rule":"ONLY_INFORMATION_AVAILABLE_AT_BOUNDARY",
        "polymarket_signal_injection":"DISABLED_TO_AVOID_TARGET_CONTAMINATION",
        "collector":"FUTURE_SEPARATE_SHADOW_COLLECTOR_NOT_PROMOTED",
        "core_mutations":0,
        "production_writeback":False,
        "trade_authority":"NONE",
        "edge_status":"UNPROVEN",
        "brier_logloss_allowed":False,
    }
