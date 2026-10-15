"""Narrow public-only Binance market-data route; no account/order capabilities."""
from __future__ import annotations

import os
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/binance", tags=["public-market-data"])
ALLOWED_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT"})
ALLOWED_INTERVALS = frozenset({"15m", "1h", "4h", "1d"})


def _enabled() -> bool:
    value = os.getenv("SENEX_BINANCE_PUBLIC_DATA_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def _metric_summary(metrics: dict) -> dict:
    keys = (
        "starting_cash", "ending_equity", "net_pnl", "return_pct",
        "max_drawdown_pct", "fills", "rejections", "expired_orders",
        "pending_orders", "time_in_market_pct", "total_fees",
        "total_spread_cost", "total_slippage_cost", "turnover",
    )
    return {key: metrics.get(key) for key in keys}


def _run_summary(item: dict) -> dict:
    base_metrics = item.get("base", {}).get("metrics", {})
    stress = {}
    for factor, result in (item.get("cost_stress") or {}).items():
        stress[factor] = _metric_summary(result.get("metrics") or {})
    return {
        "strategy_id": item.get("base", {}).get("strategy_id"),
        "base": _metric_summary(base_metrics),
        "cost_stress": stress,
    }


@router.get("/rea_paper_diagnostic")
def rea_paper_diagnostic(
    symbol: str = Query(default="BTCUSDT", min_length=3, max_length=20),
    interval: str = Query(default="4h", min_length=2, max_length=3),
    limit: int = Query(default=100, ge=2, le=100),
) -> dict:
    """Run the clean-room Grid/DCA/Combo engines over a short public-candle sample.

    This endpoint computes diagnostics only. It does not create orders, mutate
    the runtime portfolio, write the execution journal, or submit a signal.
    """
    public_data = public_klines(symbol=symbol, interval=interval, limit=limit)
    try:
        from research.rea_engines.contracts import Candle, CostProfile
        from research.rea_engines.evaluation import evaluate_strategies

        candles = tuple(
            Candle(
                timestamp=item["open_time_utc"],
                open=item["open"],
                high=item["high"],
                low=item["low"],
                close=item["close"],
                volume=item["volume"],
            )
            for item in public_data["candles"]
        )
        if len(candles) < 2:
            raise ValueError("at least two closed candles are required for paper evaluation")
        cost_profile = CostProfile(
            fee_bps="10",
            spread_bps="5",
            slippage_bps="5",
            profile_id="binance-public-paper-assumption-v1",
        )
        evaluation = evaluate_strategies(
            candles,
            public_data["source"]["canonical_closed_candles_sha256"],
            starting_cash="10000",
            max_inventory="0.02",
            max_fill_qty="0.02",
            costs=cost_profile,
            oos_start=candles[0].timestamp,
            oos_end=candles[-1].timestamp,
            include_benchmarks=True,
            cost_stress_factors=("1", "2", "3"),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "REA_PAPER_EVALUATION_UNAVAILABLE",
                "message": "The public candles were read, but the clean-room paper evaluator could not complete; no fallback metrics were substituted.",
                "reason_type": type(exc).__name__,
                "live_trading_enabled": False,
                "order_endpoints_called": 0,
            },
        ) from None

    return {
        "schema_version": 1,
        "status": "completed-paper-diagnostic",
        "mode": "PUBLIC_MARKET_DATA / PAPER_ONLY",
        "evaluation_kind": "Short recent-candle diagnostic; not a validated long-horizon OOS result and not a trade signal.",
        "venue": "Binance Spot",
        "symbol": public_data["symbol"],
        "interval": public_data["interval"],
        "source": {
            "endpoint": public_data["source"]["endpoint"],
            "raw_response_sha256": public_data["source"]["raw_response_sha256"],
            "canonical_closed_candles_sha256": public_data["source"]["canonical_closed_candles_sha256"],
            "first_closed_candle_utc": public_data["candles"][0]["open_time_utc"],
            "last_closed_candle_utc": public_data["candles"][-1]["open_time_utc"],
            "closed_rows": len(candles),
            "gap_count": public_data["source"]["gap_count"],
            "gaps_filled": False,
            "cache_hit": public_data.get("cache_hit", False),
        },
        "cost_model": {
            "profile_id": cost_profile.profile_id,
            "fee_bps": str(cost_profile.fee_bps),
            "spread_bps": str(cost_profile.spread_bps),
            "slippage_bps": str(cost_profile.slippage_bps),
            "source": "Explicit paper assumptions, not personalized account fees or calibrated fills.",
        },
        "starting_paper_cash_usdt": "10000",
        "max_inventory_qty": "0.02",
        "max_fill_qty": "0.02",
        "strategies": {
            name: _run_summary(result)
            for name, result in evaluation.get("strategies", {}).items()
        },
        "benchmarks": {
            name: _run_summary(result)
            for name, result in evaluation.get("benchmarks", {}).items()
        },
        "safety": {
            "api_key_used": False,
            "signed_requests_used": False,
            "account_endpoints_called": False,
            "order_endpoints_called": 0,
            "live_orders_submitted": 0,
            "testnet_orders_submitted": 0,
            "live_trading_enabled": False,
            "runtime_execution_engine_touched": False,
            "runtime_risk_kernel_touched": False,
            "runtime_trade_journal_touched": False,
            "EDGE": "UNPROVEN",
        },
        "limitations": [
            "A short recent sample is diagnostic only and cannot establish durable profitability.",
            "Grid/DCA/Combo are independent clean-room approximations, not copied commercial implementations.",
            "The replayed book is reset for every diagnostic and is not a persistent strategy process.",
            "No order-book depth, queue position, latency, actual partial fills, account fee tier or exchange trading filters are used.",
            "No signal from this diagnostic is routed to ExecutionEngine or Binance order APIs.",
        ],
    }


@router.get("/public_klines")
def public_klines(
    symbol: str = Query(default="BTCUSDT", min_length=3, max_length=20),
    interval: str = Query(default="4h", min_length=2, max_length=3),
    limit: int = Query(default=24, ge=2, le=100),
) -> dict:
    """Manually requested public closed OHLCV only; never emits a trade signal."""
    if not _enabled():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "BINANCE_PUBLIC_DATA_DISABLED",
                "message": "Read-only Binance market data is disabled by operator configuration.",
                "live_trading_enabled": False,
                "order_endpoints_called": 0,
            },
        )
    normalized_symbol = str(symbol).strip().upper()
    normalized_interval = str(interval).strip()
    if normalized_symbol not in ALLOWED_SYMBOLS:
        raise HTTPException(
            status_code=422,
            detail={"code": "SYMBOL_NOT_ALLOWED", "allowed_symbols": sorted(ALLOWED_SYMBOLS)},
        )
    if normalized_interval not in ALLOWED_INTERVALS:
        raise HTTPException(
            status_code=422,
            detail={"code": "INTERVAL_NOT_ALLOWED", "allowed_intervals": sorted(ALLOWED_INTERVALS)},
        )
    try:
        from .binance_public_market_data import get_public_klines
        payload = get_public_klines(normalized_symbol, normalized_interval, limit)
    except Exception as exc:
        # Known upstream/rate-limit/validation exceptions carry a safe status/code.
        error_type = type(exc).__name__
        if error_type == "PublicMarketDataError":
            status = int(getattr(exc, "http_status", 502))
            code = str(getattr(exc, "code", "PUBLIC_MARKET_DATA_ERROR"))
            message = str(exc)
            raise HTTPException(
                status_code=status,
                detail={
                    "code": code,
                    "message": message,
                    "mode": "READ_ONLY_PUBLIC_MARKET_DATA",
                    "live_trading_enabled": False,
                    "order_endpoints_called": 0,
                },
            ) from None
        raise HTTPException(
            status_code=502,
            detail={
                "code": "PUBLIC_MARKET_DATA_UNAVAILABLE",
                "message": "Public market-data snapshot failed; no stale values were substituted.",
                "live_trading_enabled": False,
                "order_endpoints_called": 0,
            },
        ) from None
    if payload.get("status") != "ok" or payload.get("mode") != "READ_ONLY_PUBLIC_MARKET_DATA / PAPER_ONLY":
        raise HTTPException(
            status_code=502,
            detail={
                "code": "PUBLIC_MARKET_DATA_CONTRACT_MISMATCH",
                "message": "The public market-data payload did not satisfy the read-only contract.",
                "live_trading_enabled": False,
                "order_endpoints_called": 0,
            },
        )
    safety = payload.get("safety", {})
    if (
        safety.get("api_key_used") is not False
        or safety.get("signed_requests_used") is not False
        or safety.get("account_endpoints_called") is not False
        or safety.get("order_endpoints_called") != 0
        or safety.get("live_orders_submitted") != 0
        or safety.get("testnet_orders_submitted") != 0
        or safety.get("live_trading_enabled") is not False
    ):
        raise HTTPException(
            status_code=502,
            detail={
                "code": "PUBLIC_MARKET_DATA_SAFETY_CONTRACT_FAILED",
                "message": "Safety contract failed; data has been withheld.",
                "live_trading_enabled": False,
                "order_endpoints_called": 0,
            },
        )
    return payload
