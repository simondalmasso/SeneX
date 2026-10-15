"""Optional read-only Binance Spot public market-data API for SENEX.

Only GET requests to fixed public endpoints are implemented. This module has no
account credentials, signatures, order/cancel endpoints, or trade-routing code.
The dashboard requests this service only after an explicit operator click.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from threading import RLock
from time import monotonic
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE_URL = "https://api.binance.com"
KLINES_PATH = "/api/v3/klines"
SERVER_TIME_PATH = "/api/v3/time"
USER_AGENT = "SENEX-PUBLIC-MARKET-DATA/1.0"
CACHE_TTL_SECONDS = 60.0
MIN_FETCH_INTERVAL_SECONDS = 1.0
MAX_LIMIT = 100
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000,
}
DEFAULT_SYMBOL_ALLOWLIST = frozenset({"BTCUSDT", "ETHUSDT"})
DEFAULT_INTERVAL_ALLOWLIST = frozenset({"1h", "4h", "1d"})

class PublicMarketDataError(ValueError):
    def __init__(self, code: str, message: str, http_status: int = 502):
        super().__init__(message)
        self.code = code
        self.http_status = http_status

_cache: dict[tuple[str, str, int], tuple[float, dict[str, Any]]] = {}
_lock = RLock()
_last_cold_fetch_at = 0.0


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _decimal(value: object, field: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise PublicMarketDataError("BAD_MARKET_DATA", f"{field} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise PublicMarketDataError("BAD_MARKET_DATA", f"{field} must be a finite decimal") from None
    if not parsed.is_finite():
        raise PublicMarketDataError("BAD_MARKET_DATA", f"{field} must be finite")
    if positive and parsed <= 0:
        raise PublicMarketDataError("BAD_MARKET_DATA", f"{field} must be positive")
    if non_negative and parsed < 0:
        raise PublicMarketDataError("BAD_MARKET_DATA", f"{field} must be non-negative")
    return parsed


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise PublicMarketDataError("BAD_MARKET_DATA", f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        raise PublicMarketDataError("BAD_MARKET_DATA", f"{field} must be an integer") from None
    if isinstance(value, float) and value != result:
        raise PublicMarketDataError("BAD_MARKET_DATA", f"{field} must be an integer")
    if result < 0:
        raise PublicMarketDataError("BAD_MARKET_DATA", f"{field} must be non-negative")
    return result


def _normalize_params(symbol: object, interval: object, limit: object) -> tuple[str, str, int]:
    sym = str(symbol).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,20}", sym):
        raise PublicMarketDataError("INVALID_SYMBOL", "symbol must contain 3-20 ASCII letters/digits", 422)
    iv = str(interval).strip()
    if iv not in INTERVAL_MS:
        raise PublicMarketDataError("INVALID_INTERVAL", "unsupported interval", 422)
    if isinstance(limit, bool):
        raise PublicMarketDataError("INVALID_LIMIT", "limit must be an integer from 2 to 100", 422)
    try:
        lim = int(limit)
    except (ValueError, TypeError):
        raise PublicMarketDataError("INVALID_LIMIT", "limit must be an integer from 2 to 100", 422) from None
    if not 2 <= lim <= MAX_LIMIT:
        raise PublicMarketDataError("INVALID_LIMIT", "limit must be an integer from 2 to 100", 422)
    return sym, iv, lim


Transport = Callable[[str, float], tuple[int, bytes]]


def _http_get(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code in (418, 429):
            retry = exc.headers.get("Retry-After", "") if exc.headers else ""
            suffix = f"; retry after {retry}s" if retry else ""
            raise PublicMarketDataError("UPSTREAM_RATE_LIMIT", f"Binance returned HTTP {exc.code}{suffix}", 503) from None
        raise PublicMarketDataError("UPSTREAM_HTTP_ERROR", f"Binance public GET returned HTTP {exc.code}", 502) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise PublicMarketDataError("UPSTREAM_UNAVAILABLE", f"Binance public GET failed: {type(exc).__name__}", 502) from None
    if status != 200:
        raise PublicMarketDataError("UPSTREAM_HTTP_ERROR", f"Binance public GET returned HTTP {status}", 502)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise PublicMarketDataError("RESPONSE_TOO_LARGE", "Binance public response exceeded 2 MiB", 502)
    return status, raw


def _read_json(url: str, transport: Transport) -> tuple[Any, bytes]:
    status, raw = transport(url, 5.0)
    if status != 200:
        raise PublicMarketDataError("UPSTREAM_HTTP_ERROR", f"Binance public GET returned HTTP {status}", 502)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PublicMarketDataError("BAD_UPSTREAM_JSON", "Binance returned invalid JSON", 502) from None
    if isinstance(value, dict) and "code" in value and "msg" in value:
        raise PublicMarketDataError("BINANCE_API_ERROR", f"Binance error {value.get('code')}: {value.get('msg')}", 502)
    return value, raw


def _fetch_uncached(symbol: str, interval: str, limit: int, transport: Transport) -> dict[str, Any]:
    query = urlencode({"symbol": symbol, "interval": interval, "limit": limit, "timeZone": "0"})
    kline_url = f"{API_BASE_URL}{KLINES_PATH}?{query}"
    raw_rows, raw_bytes = _read_json(kline_url, transport)
    server_time_value, _ = _read_json(f"{API_BASE_URL}{SERVER_TIME_PATH}", transport)
    if not isinstance(server_time_value, dict) or "serverTime" not in server_time_value:
        raise PublicMarketDataError("BAD_SERVER_TIME", "Binance server-time response lacked serverTime", 502)
    server_time_ms = _integer(server_time_value["serverTime"], "serverTime")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise PublicMarketDataError("EMPTY_KLINES", "Binance returned no klines", 502)
    if len(raw_rows) > limit:
        raise PublicMarketDataError("BAD_MARKET_DATA", "Binance returned more klines than requested", 502)

    prepared = []
    previous_open = None
    step = INTERVAL_MS[interval]
    for index, row in enumerate(raw_rows):
        if not isinstance(row, list) or len(row) != 12:
            raise PublicMarketDataError("BAD_MARKET_DATA", f"kline row {index} must contain 12 fields", 502)
        open_ms = _integer(row[0], f"kline[{index}].openTime")
        close_ms = _integer(row[6], f"kline[{index}].closeTime")
        if close_ms < open_ms or close_ms >= open_ms + step:
            raise PublicMarketDataError("BAD_MARKET_DATA", f"kline row {index} has invalid interval timestamps", 502)
        if previous_open is not None and open_ms <= previous_open:
            raise PublicMarketDataError("BAD_MARKET_DATA", "kline open times are not strictly increasing", 502)
        open_ = _decimal(row[1], "open", positive=True)
        high = _decimal(row[2], "high", positive=True)
        low = _decimal(row[3], "low", positive=True)
        close = _decimal(row[4], "close", positive=True)
        volume = _decimal(row[5], "volume", non_negative=True)
        if high < max(open_, close) or low > min(open_, close) or high < low:
            raise PublicMarketDataError("BAD_MARKET_DATA", f"kline row {index} violates OHLC ordering", 502)
        prepared.append((open_ms, close_ms, {
            "open_time_utc": _iso_ms(open_ms),
            "close_time_utc": _iso_ms(close_ms),
            "open": str(open_), "high": str(high), "low": str(low),
            "close": str(close), "volume": str(volume),
        }))
        previous_open = open_ms

    closed = [(open_ms, item) for open_ms, close_ms, item in prepared if close_ms < server_time_ms]
    if len(closed) < 2:
        raise PublicMarketDataError("NO_CLOSED_KLINES", "fewer than two fully closed candles; no partial-bar fallback", 502)
    gaps = []
    for (prev_ms, prev), (next_ms, nxt) in zip(closed, closed[1:]):
        delta = next_ms - prev_ms
        if delta < step:
            raise PublicMarketDataError("BAD_MARKET_DATA", "kline intervals overlap", 502)
        if delta > step:
            gaps.append({
                "previous_open_time_utc": prev["open_time_utc"],
                "next_open_time_utc": nxt["open_time_utc"],
                "missing_intervals": delta // step - 1 if delta % step == 0 else None,
                "non_multiple_gap": delta % step != 0,
                "filled_or_interpolated": False,
            })
    candles = [item for _, item in closed]
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    canonical = json.dumps(candles, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {
        "schema_version": 1,
        "status": "ok",
        "mode": "READ_ONLY_PUBLIC_MARKET_DATA / PAPER_ONLY",
        "venue": "Binance Spot",
        "symbol": symbol, "interval": interval, "requested_limit": limit,
        "server_time_utc": _iso_ms(server_time_ms),
        "fetched_at_utc": _now_utc(),
        "source": {
            "endpoint": KLINES_PATH, "url": kline_url,
            "raw_response_sha256": raw_sha,
            "canonical_closed_candles_sha256": hashlib.sha256(canonical).hexdigest(),
            "raw_rows_returned": len(raw_rows),
            "closed_rows_returned": len(candles),
            "incomplete_rows_dropped": len(prepared) - len(candles),
            "gap_count": len(gaps), "gaps": gaps, "gaps_filled": False,
        },
        "latest_closed_candle": candles[-1],
        "candles": candles,
        "safety": {
            "api_key_used": False, "signed_requests_used": False,
            "account_endpoints_called": False, "order_endpoints_called": 0,
            "live_orders_submitted": 0, "testnet_orders_submitted": 0,
            "live_trading_enabled": False, "EDGE": "UNPROVEN",
        },
    }


def get_public_klines(
    symbol: object = "BTCUSDT", interval: object = "4h", limit: object = 24, *,
    transport: Optional[Transport] = None, monotonic_fn: Callable[[], float] = monotonic,
) -> dict[str, Any]:
    """Return cached public closed candles; new network pulls are globally throttled."""
    global _last_cold_fetch_at
    sym, iv, lim = _normalize_params(symbol, interval, limit)
    key = (sym, iv, lim)
    with _lock:
        now = monotonic_fn()
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            result = deepcopy(cached[1])
            result["cache_hit"] = True
            return result
        if _last_cold_fetch_at and now - _last_cold_fetch_at < MIN_FETCH_INTERVAL_SECONDS:
            raise PublicMarketDataError("LOCAL_RATE_LIMIT", "wait before requesting another uncached Binance snapshot", 429)
        _last_cold_fetch_at = now
        result = _fetch_uncached(sym, iv, lim, transport or _http_get)
        result["cache_hit"] = False
        _cache[key] = (now, deepcopy(result))
        if len(_cache) > 32:
            oldest_key = min(_cache, key=lambda k: _cache[k][0])
            if oldest_key != key:
                _cache.pop(oldest_key, None)
        return deepcopy(result)


def reset_cache_for_tests() -> None:
    global _last_cold_fetch_at
    with _lock:
        _cache.clear()
        _last_cold_fetch_at = 0.0
