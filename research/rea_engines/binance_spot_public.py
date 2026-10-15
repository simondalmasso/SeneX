"""Read-only Binance Spot public-kline intake for isolated REA paper research.

This module deliberately has no API-key, signed-request, WebSocket, or order-writing
implementation. It obtains closed OHLCV bars only and can run the existing REA
historical-paper evaluators over an explicitly bounded snapshot.
"""
from __future__ import annotations
import argparse, csv, hashlib, io, json, os, re, sys, tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .contracts import Candle, CostProfile

API_BASE_URL = "https://api.binance.com"
KLINES_PATH = "/api/v3/klines"
SERVER_TIME_PATH = "/api/v3/time"
DOCS_URL = "https://developers.binance.com/en/docs/products/spot/rest-api/market"
USER_AGENT = "SENEX-REA-PAPER-PUBLIC-DATA/1.0"
MAX_LIMIT = 1000
INTERVAL_MS: dict[str, Optional[int]] = {
    "1s": 1_000, "1m": 60_000, "3m": 180_000, "5m": 300_000,
    "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
    "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
    "3d": 259_200_000, "1w": 604_800_000, "1M": None,
}
LIVE_TRADING_ENABLED = False
API_KEY_USED = False
ORDER_ENDPOINT_CALLS = 0


class BinancePublicDataError(RuntimeError):
    """The public API response or its market data violated the local contract."""


@dataclass(frozen=True)
class CandleGap:
    previous_open_time: str
    next_open_time: str
    missing_intervals: Optional[int]
    non_multiple_gap: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_open_time": self.previous_open_time,
            "next_open_time": self.next_open_time,
            "missing_intervals": self.missing_intervals,
            "non_multiple_gap": self.non_multiple_gap,
            "filled_or_interpolated": False,
        }


@dataclass(frozen=True)
class BinanceSpotSnapshot:
    symbol: str
    interval: str
    requested_limit: int
    server_time_ms: int
    captured_at_utc: str
    source_url: str
    raw_response_sha256: str
    canonical_candles_sha256: str
    raw_response_bytes: bytes
    raw_rows_returned: int
    incomplete_candles_dropped: int
    candles: tuple[Candle, ...]
    gaps: tuple[CandleGap, ...]

    def manifest(self) -> dict[str, Any]:
        server_time = datetime.fromtimestamp(
            self.server_time_ms / 1000, timezone.utc
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return {
            "schema_version": 1,
            "status": "verified-public-market-data-snapshot",
            "mode": "PUBLIC_MARKET_DATA_ONLY / PAPER_ONLY",
            "venue": "Binance Spot",
            "symbol": self.symbol,
            "interval": self.interval,
            "request": {
                "url": self.source_url,
                "requested_limit": self.requested_limit,
                "raw_rows_returned": self.raw_rows_returned,
                "closed_rows_used": len(self.candles),
                "incomplete_candles_dropped": self.incomplete_candles_dropped,
            },
            "server_time_utc": server_time,
            "captured_at_utc": self.captured_at_utc,
            "first_closed_candle_utc": self.candles[0].timestamp,
            "last_closed_candle_utc": self.candles[-1].timestamp,
            "source_response_sha256": self.raw_response_sha256,
            "canonical_candles_sha256": self.canonical_candles_sha256,
            "gap_count": len(self.gaps),
            "gaps": [gap.to_dict() for gap in self.gaps],
            "gap_policy": "REPORT_ONLY; NO SYNTHETIC OR INTERPOLATED CANDLES",
            "source_docs": DOCS_URL,
            "security": {
                "public_endpoint_only": True,
                "api_key_used": False,
                "signed_requests_used": False,
                "account_endpoints_called": False,
                "order_endpoints_called": 0,
                "live_trading_enabled": False,
                "testnet_orders_submitted": 0,
                "live_orders_submitted": 0,
            },
            "limitations": [
                "OHLCV-only input; not order-book depth or queue position.",
                "Public market data does not authenticate an account or reconcile balances/orders.",
                "Snapshot is a research input, not a trading instruction or evidence of profitability.",
            ],
        }


Transport = Callable[[str, float], tuple[int, bytes]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_symbol(symbol: object) -> str:
    value = str(symbol).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,20}", value):
        raise BinancePublicDataError("symbol must contain 3-20 ASCII letters/digits")
    return value


def _validate_interval(interval: object) -> str:
    value = str(interval).strip()
    if value not in INTERVAL_MS:
        raise BinancePublicDataError("unsupported Binance Spot interval")
    return value


def _validate_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise BinancePublicDataError(f"limit must be an integer between 1 and {MAX_LIMIT}")
    return limit


def _validate_timeout(timeout: object) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not 1 <= float(timeout) <= 30:
        raise BinancePublicDataError("timeout must be between 1 and 30 seconds")
    return float(timeout)


def _timestamp_ms(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise BinancePublicDataError(f"{field} must be an integer millisecond timestamp")
    try:
        parsed = int(value)
    except (ValueError, TypeError, OverflowError):
        raise BinancePublicDataError(f"{field} must be an integer millisecond timestamp") from None
    if isinstance(value, float) and value != parsed:
        raise BinancePublicDataError(f"{field} must be an integer millisecond timestamp")
    if parsed < 0:
        raise BinancePublicDataError(f"{field} must be non-negative")
    return parsed


def parse_iso_timestamp_ms(value: Optional[str], field: str = "timestamp") -> Optional[int]:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise BinancePublicDataError(f"{field} must be ISO-8601 with an explicit timezone") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BinancePublicDataError(f"{field} must include a timezone")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _default_transport(url: str, timeout: float) -> tuple[int, bytes]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            payload = response.read(20 * 1024 * 1024 + 1)
    except HTTPError as exc:
        retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
        suffix = f"; Retry-After={retry_after}s" if retry_after else ""
        raise BinancePublicDataError(f"Binance public GET returned HTTP {exc.code}{suffix}") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise BinancePublicDataError(f"Binance public GET failed: {type(exc).__name__}") from None
    if len(payload) > 20 * 1024 * 1024:
        raise BinancePublicDataError("Binance public response exceeded the 20 MiB safety cap")
    if status != 200:
        raise BinancePublicDataError(f"Binance public GET returned HTTP {status}")
    return status, payload


def _get_json(url: str, timeout: float, transport: Transport) -> tuple[Any, bytes]:
    status, raw = transport(url, timeout)
    if status != 200:
        raise BinancePublicDataError(f"public GET returned HTTP {status}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BinancePublicDataError("Binance public response was not valid UTF-8 JSON") from None
    if isinstance(payload, dict) and "code" in payload and "msg" in payload:
        raise BinancePublicDataError(f"Binance API error {payload.get('code')}: {payload.get('msg')}")
    return payload, raw


def parse_klines_response(
    raw_response: bytes, *, symbol: str, interval: str, limit: int, server_time_ms: int,
    source_url: Optional[str] = None, captured_at_utc: Optional[str] = None,
    start_time_ms: Optional[int] = None, end_time_ms: Optional[int] = None,
) -> BinanceSpotSnapshot:
    """Parse a raw public klines JSON body. Missing bars are never invented."""
    normalized_symbol = _validate_symbol(symbol)
    normalized_interval = _validate_interval(interval)
    requested_limit = _validate_limit(limit)
    server_ms = _timestamp_ms(server_time_ms, "server_time_ms")
    start_ms = _timestamp_ms(start_time_ms, "start_time_ms") if start_time_ms is not None else None
    end_ms = _timestamp_ms(end_time_ms, "end_time_ms") if end_time_ms is not None else None
    if start_ms is not None and end_ms is not None and end_ms < start_ms:
        raise BinancePublicDataError("end_time_ms must not be earlier than start_time_ms")
    try:
        rows = json.loads(raw_response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BinancePublicDataError("klines payload was not valid UTF-8 JSON") from None
    if not isinstance(rows, list):
        raise BinancePublicDataError("klines response must be a JSON array")
    if not rows:
        raise BinancePublicDataError("Binance returned zero kline rows")
    if len(rows) > requested_limit:
        raise BinancePublicDataError("Binance returned more rows than the requested limit")

    all_open_ms: list[int] = []
    prepared: list[tuple[int, int, Candle]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != 12:
            raise BinancePublicDataError(f"kline row {index} must contain exactly 12 fields")
        open_ms = _timestamp_ms(row[0], f"kline[{index}].open_time")
        close_ms = _timestamp_ms(row[6], f"kline[{index}].close_time")
        if close_ms < open_ms:
            raise BinancePublicDataError(f"kline row {index} closes before it opens")
        if all_open_ms and open_ms <= all_open_ms[-1]:
            raise BinancePublicDataError("kline open times must be strictly increasing and unique")
        if start_ms is not None and open_ms < start_ms:
            raise BinancePublicDataError(f"kline row {index} precedes startTime")
        if end_ms is not None and open_ms > end_ms:
            raise BinancePublicDataError(f"kline row {index} follows endTime")
        step = INTERVAL_MS[normalized_interval]
        if step is not None and close_ms >= open_ms + step:
            raise BinancePublicDataError(f"kline row {index} close time exceeds its interval")
        try:
            open_ts = datetime.fromtimestamp(open_ms / 1000, timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")
            candle = Candle(timestamp=open_ts, open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5])
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            raise BinancePublicDataError(
                f"kline row {index} violates the OHLCV contract: {type(exc).__name__}: {exc}"
            ) from None
        all_open_ms.append(open_ms)
        prepared.append((open_ms, close_ms, candle))

    closed = [(open_ms, candle) for open_ms, close_ms, candle in prepared if close_ms < server_ms]
    dropped = len(prepared) - len(closed)
    if not closed:
        raise BinancePublicDataError("no fully closed candles in response; refusing partial-bar evaluation")

    step = INTERVAL_MS[normalized_interval]
    gaps: list[CandleGap] = []
    if step is not None:
        for (prev_ms, prev_candle), (next_ms, next_candle) in zip(closed, closed[1:]):
            delta = next_ms - prev_ms
            if delta < step:
                raise BinancePublicDataError("kline intervals overlap or are shorter than requested interval")
            if delta > step:
                multiple = delta % step == 0
                gaps.append(CandleGap(
                    previous_open_time=prev_candle.timestamp,
                    next_open_time=next_candle.timestamp,
                    missing_intervals=(delta // step - 1) if multiple else None,
                    non_multiple_gap=not multiple,
                ))

    candles = tuple(candle for _, candle in closed)
    canonical = [{
        "timestamp": c.timestamp, "open": str(c.open), "high": str(c.high),
        "low": str(c.low), "close": str(c.close), "volume": str(c.volume),
    } for c in candles]
    canonical_bytes = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return BinanceSpotSnapshot(
        symbol=normalized_symbol, interval=normalized_interval, requested_limit=requested_limit,
        server_time_ms=server_ms, captured_at_utc=captured_at_utc or _utc_now(),
        source_url=source_url or f"{API_BASE_URL}{KLINES_PATH}",
        raw_response_sha256=hashlib.sha256(raw_response).hexdigest(),
        canonical_candles_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        raw_response_bytes=raw_response, raw_rows_returned=len(rows),
        incomplete_candles_dropped=dropped, candles=candles, gaps=tuple(gaps),
    )


def fetch_public_klines(
    symbol: str = "BTCUSDT", interval: str = "4h", limit: int = 200, *,
    start_time_ms: Optional[int] = None, end_time_ms: Optional[int] = None,
    timeout: float = 10.0, transport: Optional[Transport] = None,
) -> BinanceSpotSnapshot:
    """Fetch public Spot klines and server time. No keys or order APIs are used."""
    normalized_symbol = _validate_symbol(symbol)
    normalized_interval = _validate_interval(interval)
    requested_limit = _validate_limit(limit)
    timeout_s = _validate_timeout(timeout)
    start_ms = _timestamp_ms(start_time_ms, "start_time_ms") if start_time_ms is not None else None
    end_ms = _timestamp_ms(end_time_ms, "end_time_ms") if end_time_ms is not None else None
    if start_ms is not None and end_ms is not None and end_ms < start_ms:
        raise BinancePublicDataError("end_time_ms must not be earlier than start_time_ms")
    getter = transport or _default_transport
    params: dict[str, str] = {
        "symbol": normalized_symbol, "interval": normalized_interval,
        "limit": str(requested_limit), "timeZone": "0",
    }
    if start_ms is not None: params["startTime"] = str(start_ms)
    if end_ms is not None: params["endTime"] = str(end_ms)
    kline_url = f"{API_BASE_URL}{KLINES_PATH}?{urlencode(params)}"
    _, raw_bytes = _get_json(kline_url, timeout_s, getter)
    time_url = f"{API_BASE_URL}{SERVER_TIME_PATH}"
    server_payload, _ = _get_json(time_url, timeout_s, getter)
    if not isinstance(server_payload, dict) or "serverTime" not in server_payload:
        raise BinancePublicDataError("server-time response lacks serverTime")
    server_ms = _timestamp_ms(server_payload["serverTime"], "serverTime")
    return parse_klines_response(
        raw_bytes, symbol=normalized_symbol, interval=normalized_interval, limit=requested_limit,
        server_time_ms=server_ms, source_url=kline_url, start_time_ms=start_ms, end_time_ms=end_ms,
    )


def evaluate_snapshot_paper(
    snapshot: BinanceSpotSnapshot, *, starting_cash: object = "10000",
    max_inventory: object = "0.02", max_fill_qty: object = "0.02",
    costs: Optional[CostProfile] = None, cost_stress_factors: tuple[object, ...] = ("1", "2", "3"),
) -> dict[str, Any]:
    """Run clean-room REA replay on this snapshot; the ledger resets for the sample."""
    if not isinstance(snapshot, BinanceSpotSnapshot):
        raise BinancePublicDataError("snapshot must be a BinanceSpotSnapshot")
    if len(snapshot.candles) < 2:
        raise BinancePublicDataError("at least two closed candles are required for paper evaluation")
    from .evaluation import evaluate_strategies
    cost_profile = costs or CostProfile(
        fee_bps="10", spread_bps="5", slippage_bps="5",
        profile_id="binance-public-paper-assumption-v1",
    )
    evaluation = evaluate_strategies(
        snapshot.candles, snapshot.canonical_candles_sha256,
        starting_cash=starting_cash, max_inventory=max_inventory, max_fill_qty=max_fill_qty,
        costs=cost_profile, oos_start=snapshot.candles[0].timestamp,
        oos_end=snapshot.candles[-1].timestamp, include_benchmarks=True,
        cost_stress_factors=cost_stress_factors,
    )
    return {
        "schema_version": 1,
        "status": "completed-paper-diagnostic",
        "evaluation_kind": "recent-public-candle-sample; not a validated long-horizon OOS result",
        "mode": "PAPER_ONLY", "venue": "Binance Spot public market data",
        "symbol": snapshot.symbol, "interval": snapshot.interval,
        "source": {
            "raw_response_sha256": snapshot.raw_response_sha256,
            "canonical_candles_sha256": snapshot.canonical_candles_sha256,
            "closed_rows": len(snapshot.candles),
            "first_closed_candle_utc": snapshot.candles[0].timestamp,
            "last_closed_candle_utc": snapshot.candles[-1].timestamp,
            "gap_count": len(snapshot.gaps), "gaps_not_interpolated": True,
        },
        "cost_model": {
            "profile_id": cost_profile.profile_id,
            "fee_bps": str(cost_profile.fee_bps), "spread_bps": str(cost_profile.spread_bps),
            "slippage_bps": str(cost_profile.slippage_bps), "is_account_fee_quote": False,
            "note": "Paper assumptions; not personalized Binance account fees or calibrated execution cost.",
        },
        "rea_evaluation": evaluation,
        "safety": {
            "EDGE": "UNPROVEN", "api_key_used": False, "account_endpoints_called": False,
            "order_endpoints_called": 0, "live_orders_submitted": 0,
            "testnet_orders_submitted": 0, "live_trading_enabled": False,
            "runtime_integration": "NOT_CONNECTED",
        },
        "limitations": [
            "The starting paper ledger is reset for this snapshot; this is not a persistent live-paper trading process.",
            "The short sample is diagnostic only, not sufficient evidence of robust profitability.",
            "No book depth, tick/lot filters, account fee tier, queue position, latency or partial execution is inferred from klines.",
            "No order, cancel, account, private or testnet endpoint is called.",
        ],
    }


def _csv_bytes(snapshot: BinanceSpotSnapshot) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=["timestamp", "open", "high", "low", "close", "volume"], lineterminator="\n")
    writer.writeheader()
    for c in snapshot.candles:
        writer.writerow({
            "timestamp": c.timestamp, "open": str(c.open), "high": str(c.high),
            "low": str(c.low), "close": str(c.close), "volume": str(c.volume),
        })
    return stream.getvalue().encode("utf-8")


def write_research_bundle(
    snapshot: BinanceSpotSnapshot, output_csv: str | Path,
    paper_report: Optional[dict[str, Any]] = None, *, overwrite: bool = False,
) -> dict[str, str]:
    """Persist OHLCV CSV, exact raw response, manifest and optional paper diagnostic."""
    output = Path(output_csv).expanduser()
    if output.suffix.lower() != ".csv":
        raise BinancePublicDataError("output path must end in .csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_path = output.with_suffix(".raw.json")
    manifest_path = output.with_suffix(".manifest.json")
    paper_path = output.with_suffix(".paper.json") if paper_report is not None else None
    targets = [output, raw_path, manifest_path] + ([paper_path] if paper_path else [])
    if not overwrite:
        existing = [str(p) for p in targets if p.exists()]
        if existing:
            raise BinancePublicDataError("refusing to overwrite existing research artifact(s): " + ", ".join(existing))
    csv_payload = _csv_bytes(snapshot)
    raw_payload = snapshot.raw_response_bytes
    paper_payload = (json.dumps(paper_report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8") if paper_report is not None else None
    manifest = snapshot.manifest()
    manifest["artifacts"] = {
        "ohlcv_csv": {"file": output.name, "sha256": hashlib.sha256(csv_payload).hexdigest(), "bytes": len(csv_payload)},
        "raw_klines_response": {"file": raw_path.name, "sha256": snapshot.raw_response_sha256, "bytes": len(raw_payload)},
    }
    if paper_payload is not None and paper_path is not None:
        manifest["artifacts"]["paper_diagnostic"] = {
            "file": paper_path.name, "sha256": hashlib.sha256(paper_payload).hexdigest(), "bytes": len(paper_payload),
        }
    manifest_payload = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    payloads = [(output, csv_payload), (raw_path, raw_payload), (manifest_path, manifest_payload)]
    if paper_path is not None and paper_payload is not None: payloads.append((paper_path, paper_payload))
    temps: list[Path] = []
    try:
        for target, payload in payloads:
            fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(output.parent))
            temp = Path(temp_name); temps.append(temp)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        if not overwrite:
            existing = [str(p) for p in targets if p.exists()]
            if existing:
                raise BinancePublicDataError("refusing to overwrite existing research artifact(s): " + ", ".join(existing))
        for (target, _), temp in zip(payloads, temps):
            os.replace(temp, target)
    finally:
        for temp in temps:
            try: temp.unlink(missing_ok=True)
            except OSError: pass
    files = [("ohlcv_csv", output), ("raw_response", raw_path), ("manifest", manifest_path)]
    if paper_path: files.append(("paper_report", paper_path))
    return {key: str(path.resolve()) for key, path in files}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rea-binance-public-paper",
        description="Fetch closed Binance Spot OHLCV bars and save a local PAPER-only REA diagnostic bundle.",
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--start-time", help="Optional ISO-8601 timestamp with timezone, e.g. 2026-10-01T00:00:00Z")
    parser.add_argument("--end-time", help="Optional ISO-8601 timestamp with timezone")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--starting-cash", default="10000")
    parser.add_argument("--max-inventory", default="0.02")
    parser.add_argument("--max-fill-qty", default="0.02")
    parser.add_argument("--fee-bps", default="10")
    parser.add_argument("--spread-bps", default="5")
    parser.add_argument("--slippage-bps", default="5")
    parser.add_argument("--output", required=True, help="New local CSV path; siblings store raw JSON, manifest and paper report")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-paper-evaluation", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        start_ms = parse_iso_timestamp_ms(args.start_time, "start-time")
        end_ms = parse_iso_timestamp_ms(args.end_time, "end-time")
        output = Path(args.output).expanduser()
        if not args.overwrite:
            candidates = [output, output.with_suffix(".raw.json"), output.with_suffix(".manifest.json"), output.with_suffix(".paper.json")]
            if any(p.exists() for p in candidates):
                raise BinancePublicDataError("an output artifact already exists; choose a new path or pass --overwrite")
        snapshot = fetch_public_klines(
            args.symbol, args.interval, args.limit, start_time_ms=start_ms,
            end_time_ms=end_ms, timeout=args.timeout,
        )
        paper_report = None if args.skip_paper_evaluation else evaluate_snapshot_paper(
            snapshot, starting_cash=args.starting_cash, max_inventory=args.max_inventory,
            max_fill_qty=args.max_fill_qty,
            costs=CostProfile(fee_bps=args.fee_bps, spread_bps=args.spread_bps,
                              slippage_bps=args.slippage_bps, profile_id="binance-public-paper-assumption-v1"),
        )
        paths = write_research_bundle(snapshot, output, paper_report, overwrite=args.overwrite)
        summary = {
            "status": "completed", "symbol": snapshot.symbol, "interval": snapshot.interval,
            "requested_limit": snapshot.requested_limit, "raw_rows_returned": snapshot.raw_rows_returned,
            "closed_rows_used": len(snapshot.candles), "incomplete_candles_dropped": snapshot.incomplete_candles_dropped,
            "gap_count": len(snapshot.gaps), "first_closed_candle_utc": snapshot.candles[0].timestamp,
            "last_closed_candle_utc": snapshot.candles[-1].timestamp,
            "raw_response_sha256": snapshot.raw_response_sha256,
            "canonical_candles_sha256": snapshot.canonical_candles_sha256,
            "live_orders_submitted": 0, "testnet_orders_submitted": 0,
            "api_key_used": False, "live_trading_enabled": False,
            "paper_evaluation_included": paper_report is not None,
            "paper_results": {"edge": "UNPROVEN", "strategy_names": list((paper_report or {}).get("rea_evaluation", {}).get("strategies", {}).keys())},
            "files": paths,
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (BinancePublicDataError, OSError, ValueError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
