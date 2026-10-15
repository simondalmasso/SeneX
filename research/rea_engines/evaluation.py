"""Local-only CSV evaluation and provenance for the isolated REA lab."""
from __future__ import annotations

import csv
import hashlib
import io
import statistics
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime
from pathlib import Path
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional, Sequence

from .contracts import Candle, CostProfile, OrderIntent, as_decimal, normalize_timestamp
from .replay import DEFAULT_ASSUMPTIONS, FILL_POLICY_ID, Replay, ReplayConfig, RunReport, canonical_sha256
from .strategies import ComboStrategy, DCAConfig, DCAStrategy, GridConfig, GridStrategy


DEFAULT_SOURCE_SHA256 = "7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146"
DEFAULT_OOS_START = "2022-01-01T00:00:00Z"
DEFAULT_OOS_END = "2026-09-30T20:00:00Z"
DEFAULT_STARTING_CASH = Decimal("10000")
DEFAULT_MAX_INVENTORY = Decimal("0.02")
DEFAULT_MAX_FILL_QTY = Decimal("0.02")
ZERO = Decimal("0")
ONE = Decimal("1")


class EvaluationError(ValueError):
    """Raised when local dataset, evaluation configuration, or provenance is invalid."""


def _validate_sha256(value: object, field_name: str) -> str:
    digest = str(value).strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise EvaluationError(f"{field_name} must be a 64-character hexadecimal digest")
    return digest


def load_ohlcv_csv(path: str | Path, expected_sha256: Optional[str] = None) -> tuple[tuple[Candle, ...], str]:
    """Read a local OHLCV CSV only; never download or replace missing data."""
    source = Path(path).expanduser()
    if not source.exists() or not source.is_file():
        raise EvaluationError(f"local CSV input does not exist or is not a file: {source}")
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise EvaluationError(f"cannot read local CSV input: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != _validate_sha256(expected_sha256, "expected_sha256"):
        raise EvaluationError(f"source SHA-256 mismatch: expected {expected_sha256}, observed {digest}")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EvaluationError("CSV input must be UTF-8 encoded") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise EvaluationError("CSV input must include a header row")
    reader.fieldnames = [name.strip() for name in reader.fieldnames]
    required = ("timestamp", "open", "high", "low", "close", "volume")
    missing = [name for name in required if name not in reader.fieldnames]
    if missing:
        raise EvaluationError(f"CSV header is missing required columns: {', '.join(missing)}")
    candles: list[Candle] = []
    for line_number, row in enumerate(reader, start=2):
        if not row or all(value is None or str(value).strip() == "" for value in row.values()):
            continue
        try:
            candle = Candle(
                timestamp=row["timestamp"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
        except (TypeError, ValueError) as exc:
            raise EvaluationError(f"invalid OHLCV row {line_number}: {exc}") from exc
        if candles and candle.timestamp <= candles[-1].timestamp:
            raise EvaluationError(f"timestamps must be strictly increasing; row {line_number} is duplicate or out of order")
        candles.append(candle)
    if not candles:
        raise EvaluationError("CSV input contains no OHLCV data rows")
    return tuple(candles), digest


def canonical_report_sha256(payload: Mapping[str, Any]) -> str:
    """Hash canonical JSON with the top-level report_sha256 field excluded."""
    if not isinstance(payload, Mapping):
        raise EvaluationError("report payload must be a mapping")
    without_digest = {key: value for key, value in payload.items() if key != "report_sha256"}
    try:
        return canonical_sha256(without_digest)
    except (TypeError, ValueError) as exc:
        raise EvaluationError(f"report payload is not canonical-JSON serializable: {exc}") from exc


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _strategy_config(strategy: Any) -> dict[str, Any]:
    if hasattr(strategy, "grid") and hasattr(strategy, "dca"):
        return {
            "grid": _jsonable(strategy.grid.config),
            "dca": _jsonable(strategy.dca.config),
        }
    if hasattr(strategy, "config"):
        return _jsonable(strategy.config)
    return {"strategy_id": str(getattr(strategy, "strategy_id", type(strategy).__name__))}


def _default_strategy_factories() -> dict[str, Callable[[], Any]]:
    grid_config = GridConfig(
        lower_price="10000", upper_price="100000", levels=10,
        quote_per_order="100", take_profit_pct="0.01",
    )
    dca_config = DCAConfig(
        initial_quote="100", dca_quote="50", adverse_step_pct="0.05",
        take_profit_pct="0.03", max_adds=3,
    )
    combo_grid = GridConfig(
        lower_price="10000", upper_price="100000", levels=10,
        quote_per_order="100", take_profit_pct="0.01",
    )
    combo_dca = DCAConfig(
        initial_quote="100", dca_quote="50", adverse_step_pct="0.05",
        take_profit_pct="0.03", max_adds=3,
    )
    return {
        "grid": lambda: GridStrategy(grid_config),
        "dca": lambda: DCAStrategy(dca_config),
        "combo": lambda: ComboStrategy(grid_config=combo_grid, dca_config=combo_dca),
    }


class _BuyAndHoldQuote:
    strategy_id = "buy-and-hold-quote-v1"

    def __init__(self, quote: Decimal, config: ReplayConfig) -> None:
        self.quote = quote
        self.config = config
        self.submitted = False

    def on_candle(self, candle: Candle, portfolio_snapshot, open_orders) -> list[OrderIntent]:
        if self.submitted or open_orders or portfolio_snapshot.inventory_qty > ZERO:
            return []
        quantity = min(self.quote / candle.close, self.config.max_inventory, self.config.max_fill_qty)
        if quantity <= ZERO:
            return []
        self.submitted = True
        return [OrderIntent("buy", "market", quantity, tag="buy-and-hold-quote")]


class _BuyAndHoldQuantity:
    strategy_id = "buy-and-hold-fixed-qty-v1"

    def __init__(self, quantity: Decimal, config: ReplayConfig) -> None:
        self.quantity = quantity
        self.config = config
        self.submitted = False

    def on_candle(self, candle: Candle, portfolio_snapshot, open_orders) -> list[OrderIntent]:
        if self.submitted or open_orders or portfolio_snapshot.inventory_qty > ZERO:
            return []
        quantity = min(self.quantity, self.config.max_inventory, self.config.max_fill_qty)
        if quantity <= ZERO:
            return []
        self.submitted = True
        return [OrderIntent("buy", "market", quantity, tag="buy-and-hold-fixed-qty")]


def _compact_run(report: RunReport, *, include_equity_curve: bool = False) -> dict[str, Any]:
    payload = report.to_dict(include_digest=True)
    if not include_equity_curve:
        payload.pop("equity_curve", None)
    return {
        "strategy_id": report.strategy_id,
        "cost_profile": report.config.to_dict()["costs"],
        "metrics": dict(report.metrics),
        "fills": len(report.fills),
        "rejections": len(report.rejections),
        "expired_orders": len(report.expired_orders),
        "pending_orders": len(report.pending_orders),
        "report_sha256": report.report_sha256,
        "run": payload if include_equity_curve else None,
    }


def _interval_gaps(candles: Sequence[Candle]) -> list[dict[str, Any]]:
    if len(candles) < 3:
        return []
    timestamps = [datetime.fromisoformat(item.timestamp.replace("Z", "+00:00")) for item in candles]
    deltas = [(timestamps[index] - timestamps[index - 1]).total_seconds() for index in range(1, len(timestamps))]
    typical = statistics.median(deltas)
    if typical <= 0:
        return []
    result = []
    for index, delta in enumerate(deltas, start=1):
        if delta > typical * 1.5:
            result.append({
                "previous_timestamp": candles[index - 1].timestamp,
                "timestamp": candles[index].timestamp,
                "delta_seconds": str(Decimal(str(delta))),
                "typical_interval_seconds": str(Decimal(str(typical))),
            })
    return result


def evaluate_strategies(
    candles: Sequence[Candle],
    source_sha256: str,
    *,
    starting_cash: object = DEFAULT_STARTING_CASH,
    max_inventory: object = DEFAULT_MAX_INVENTORY,
    max_fill_qty: object = DEFAULT_MAX_FILL_QTY,
    costs: Optional[CostProfile] = None,
    oos_start: Optional[str] = DEFAULT_OOS_START,
    oos_end: Optional[str] = DEFAULT_OOS_END,
    strategy_factories: Optional[Mapping[str, Callable[[], Any]]] = None,
    include_benchmarks: bool = True,
    cost_stress_factors: Sequence[object] = (1, 2, 3),
    quote_benchmark: object = "100",
    fixed_qty_benchmark: object = "0.02",
) -> dict[str, Any]:
    """Evaluate frozen strategy factories on an explicit chronological OOS slice."""
    bars = tuple(candles)
    if not bars:
        raise EvaluationError("at least one candle is required")
    source_digest = _validate_sha256(source_sha256, "source_sha256")
    for index, candle in enumerate(bars):
        if not isinstance(candle, Candle):
            raise EvaluationError(f"row {index} is not a Candle")
        if index and candle.timestamp <= bars[index - 1].timestamp:
            raise EvaluationError("candle timestamps must be strictly increasing")
    try:
        start_ts = normalize_timestamp(oos_start) if oos_start is not None else bars[0].timestamp
        end_ts = normalize_timestamp(oos_end) if oos_end is not None else bars[-1].timestamp
    except ValueError as exc:
        raise EvaluationError(f"invalid OOS interval: {exc}") from exc
    if end_ts < start_ts:
        raise EvaluationError("oos_end must not be earlier than oos_start")
    oos_bars = tuple(bar for bar in bars if start_ts <= bar.timestamp <= end_ts)
    if not oos_bars:
        raise EvaluationError("OOS interval contains no candles")
    cost_profile = costs or CostProfile()
    if not isinstance(cost_profile, CostProfile):
        raise EvaluationError("costs must be a CostProfile")
    config = ReplayConfig(
        starting_cash=starting_cash, max_inventory=max_inventory,
        max_fill_qty=max_fill_qty, costs=cost_profile, config_id="rea-replay-v1",
    )
    factories = dict(strategy_factories) if strategy_factories is not None else _default_strategy_factories()
    if not factories:
        raise EvaluationError("at least one strategy factory is required")
    for name, factory in factories.items():
        if not isinstance(name, str) or not name.strip() or not callable(factory):
            raise EvaluationError("strategy factories must map non-empty names to callable factories")

    factors = tuple(as_decimal(factor, "cost_stress_factor", positive=True) for factor in cost_stress_factors)
    if not factors or ONE not in factors:
        raise EvaluationError("cost_stress_factors must include 1x baseline")
    if len(set(factors)) != len(factors):
        raise EvaluationError("cost_stress_factors must not contain duplicates")

    benchmark_factories: dict[str, Callable[[], Any]] = {}
    quote_benchmark_name = "buy_and_hold_quote_100"
    if include_benchmarks:
        quote = as_decimal(quote_benchmark, "quote_benchmark", positive=True)
        fixed_qty = as_decimal(fixed_qty_benchmark, "fixed_qty_benchmark", positive=True)
        quote_benchmark_name = f"buy_and_hold_quote_{format(quote.normalize(), 'f')}"
        benchmark_factories = {
            quote_benchmark_name: lambda: _BuyAndHoldQuote(quote, config),
            "buy_and_hold_fixed_qty": lambda: _BuyAndHoldQuantity(fixed_qty, config),
        }

    replay = Replay()

    def evaluate_factory(factory: Callable[[], Any], name: str) -> dict[str, Any]:
        base_strategy = factory()
        base_config = _strategy_config(base_strategy)
        base_report = replay.run(oos_bars, base_strategy, config, source_sha256=source_digest)
        stress_runs: dict[str, Any] = {}
        for factor in factors:
            factor_name = f"{format(factor.normalize(), 'f')}x"
            if factor == ONE:
                stress_report = base_report
            else:
                stress_costs = cost_profile.scaled(factor, profile_id=f"{cost_profile.profile_id}-{factor_name}")
                stress_config = replace(config, costs=stress_costs, config_id=f"rea-replay-v1-{factor_name}")
                stress_report = replay.run(oos_bars, factory(), stress_config, source_sha256=source_digest)
            compact = _compact_run(stress_report, include_equity_curve=False)
            compact.pop("run", None)
            stress_runs[factor_name] = compact

        annual: dict[str, Any] = {}
        years = sorted({int(bar.timestamp[:4]) for bar in oos_bars})
        for year in years:
            year_bars = tuple(bar for bar in oos_bars if bar.timestamp.startswith(f"{year:04d}-"))
            if not year_bars:
                continue
            annual_report = replay.run(year_bars, factory(), config, source_sha256=source_digest)
            annual[str(year)] = _compact_run(annual_report, include_equity_curve=False)
            annual[str(year)].pop("run", None)

        return {
            "strategy_config": base_config,
            "base": base_report.to_dict(include_digest=True),
            "cost_stress": stress_runs,
            "annual_subperiods": annual,
            "annual_subperiod_semantics": "Each calendar-year slice resets to starting_cash; parameters remain fixed and no cross-year compounding is implied.",
        }

    candidate_results = {name: evaluate_factory(factory, name) for name, factory in factories.items()}
    benchmark_results = {name: evaluate_factory(factory, name) for name, factory in benchmark_factories.items()}

    strategy_config_payload = {name: item["strategy_config"] for name, item in candidate_results.items()}
    benchmark_config_payload = {name: item["strategy_config"] for name, item in benchmark_results.items()}
    cost_profile_payload = {
        "base": {
            "profile_id": cost_profile.profile_id,
            "fee_bps": str(cost_profile.fee_bps),
            "spread_bps": str(cost_profile.spread_bps),
            "slippage_bps": str(cost_profile.slippage_bps),
        },
        "stress_factors": [format(factor.normalize(), "f") for factor in factors],
    }
    fill_policy_payload = {
        "fill_policy_id": FILL_POLICY_ID,
        "assumptions": list(DEFAULT_ASSUMPTIONS),
    }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "completed",
        "engine_version": "rea-engine-lab-v1",
        "scope": "independent clean-room spot strategy approximations; not recovered proprietary engine code",
        "dataset": {
            "source_sha256": source_digest,
            "source_rows": len(bars),
            "source_timestamp_start": bars[0].timestamp,
            "source_timestamp_end": bars[-1].timestamp,
            "evaluated_rows": len(oos_bars),
            "oos_timestamp_start": oos_bars[0].timestamp,
            "oos_timestamp_end": oos_bars[-1].timestamp,
            "requested_oos_start": start_ts,
            "requested_oos_end": end_ts,
            "interval_gaps": _interval_gaps(bars),
            "evaluated_interval_gaps": _interval_gaps(oos_bars),
        },
        "replay_config": config.to_dict(),
        "config_sha256": canonical_sha256({
            "replay_config": config.to_dict(),
            "strategy_configs": strategy_config_payload,
            "benchmark_configs": benchmark_config_payload,
        }),
        "cost_profile_sha256": canonical_sha256(cost_profile_payload),
        "cost_profile_provenance": cost_profile_payload,
        "fill_policy_id": FILL_POLICY_ID,
        "fill_policy_sha256": canonical_sha256(fill_policy_payload),
        "strategy_count": len(candidate_results),
        "strategies": candidate_results,
        "benchmarks": benchmark_results,
        "cost_stress_factors": [format(factor.normalize(), "f") for factor in factors],
        "benchmark_notes": ({
            quote_benchmark_name: "Uses a fixed quote allocation with the same starting-equity denominator and the same inventory/fill caps; it is not full-cash exposure.",
            "buy_and_hold_fixed_qty": "Uses the configured fixed quantity, capped by the same inventory/fill limits; report separately because exposure differs from quote-based allocation.",
        } if include_benchmarks else {}),
        "safety": {
            "mode": "historical-paper",
            "network_calls": 0,
            "live_orders_submitted": 0,
            "EDGE": "UNPROVEN",
            "zero_spend": True,
        },
        "limitations": [
            "OHLCV cannot reveal queue priority or intrabar price path.",
            "All strategies are clean-room approximations, not exact commercial-product engines.",
            "Yearly subperiods reset capital and are not a compounded portfolio path.",
            "A historical return is not proof of future profitability.",
        ],
    }
    payload["report_sha256"] = canonical_report_sha256(payload)
    return payload
