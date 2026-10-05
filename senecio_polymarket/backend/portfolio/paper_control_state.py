"""Durable state for the native SENEX PAPER control lane.

This is deliberately local-file only. It preserves the minimum state required
for scientific continuity across H011 restarts and never enables LIVE trading.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .execution_engine import Position
from .persistence_paths import resolve_path
from .risk_kernel import KernelState

STATE_VERSION = "paper-control-state-v1"
DEFAULT_STATE_PATH = "data/portfolio/paper_control_state.json"


class PaperControlStateError(RuntimeError):
    pass


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _validate_finite_tree(value: Any, path: str = "ROOT") -> None:
    """Reject NaN/Infinity anywhere in the durable scientific state."""
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PaperControlStateError(
                f"PAPER_CONTROL_STATE_CORRUPT:NONFINITE:{path}"
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite_tree(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_finite_tree(item, f"{path}.{key}")
        return
    raise PaperControlStateError(
        f"PAPER_CONTROL_STATE_CORRUPT:UNSUPPORTED_TYPE:{path}"
    )


def _require_exact_nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise PaperControlStateError(
            f"PAPER_CONTROL_STATE_CORRUPT:{label}"
        )
    return value


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class PaperControlStateStore:
    def __init__(self, path: str | None = None):
        self.path = Path(
            resolve_path(
                "paper_control_state.json",
                DEFAULT_STATE_PATH,
                explicit=path,
                env_key="SENEX_PAPER_CONTROL_STATE_PATH",
            )
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict[str, Any]:
        try:
            raw = json.loads(
                self.path.read_text(encoding="utf-8"),
                parse_constant=_reject_json_constant,
            )
        except Exception as exc:
            raise PaperControlStateError(
                f"PAPER_CONTROL_STATE_CORRUPT:{type(exc).__name__}"
            ) from exc
        self._validate(raw)
        return raw

    def save(self, payload: dict[str, Any]) -> None:
        self._validate(payload)
        tmp = self.path.with_name(self.path.name + ".tmp")
        encoded = (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        try:
            with open(tmp, "wb", buffering=0) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            _fsync_parent(self.path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    @staticmethod
    def _validate(payload: Any) -> None:
        if not isinstance(payload, dict):
            raise PaperControlStateError("PAPER_CONTROL_STATE_CORRUPT:NOT_OBJECT")
        if payload.get("version") != STATE_VERSION:
            raise PaperControlStateError("PAPER_CONTROL_STATE_CORRUPT:VERSION")
        if payload.get("paper_only") is not True:
            raise PaperControlStateError("PAPER_CONTROL_STATE_CORRUPT:PAPER_ONLY")

        execution = payload.get("execution")
        risk = payload.get("risk_state")
        last_prices = payload.get("last_prices")
        meta_state = payload.get("meta_labeler_state")
        micro_state = payload.get("microstructure_state")
        if not isinstance(execution, dict) or not isinstance(risk, dict):
            raise PaperControlStateError("PAPER_CONTROL_STATE_CORRUPT:SECTIONS")
        if not isinstance(last_prices, dict):
            raise PaperControlStateError("PAPER_CONTROL_STATE_CORRUPT:LAST_PRICES")
        if not isinstance(meta_state, dict):
            raise PaperControlStateError(
                "PAPER_CONTROL_STATE_CORRUPT:META_LABELER_STATE"
            )
        if not isinstance(micro_state, dict):
            raise PaperControlStateError(
                "PAPER_CONTROL_STATE_CORRUPT:MICROSTRUCTURE_STATE"
            )

        _validate_finite_tree(payload)

        for key in ("cash", "starting_cash"):
            value = execution.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PaperControlStateError(
                    f"PAPER_CONTROL_STATE_CORRUPT:{key.upper()}"
                )
            if not math.isfinite(float(value)):
                raise PaperControlStateError(
                    f"PAPER_CONTROL_STATE_CORRUPT:{key.upper()}"
                )

        open_positions = execution.get("open_positions")
        closed_positions = execution.get("closed_positions")
        if not isinstance(open_positions, list):
            raise PaperControlStateError("PAPER_CONTROL_STATE_CORRUPT:OPEN_POSITIONS")
        if not isinstance(closed_positions, list):
            raise PaperControlStateError("PAPER_CONTROL_STATE_CORRUPT:CLOSED_POSITIONS")

        _require_exact_nonnegative_int(
            execution.get("total_orders", 0),
            "TOTAL_ORDERS",
        )
        for key in (
            "consecutive_losses",
            "proposals_evaluated",
            "proposals_approved",
            "proposals_rejected",
        ):
            _require_exact_nonnegative_int(
                risk.get(key, 0),
                key.upper(),
            )

        for collection_name, rows in (
            ("OPEN_POSITIONS", open_positions),
            ("CLOSED_POSITIONS", closed_positions),
        ):
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise PaperControlStateError(
                        f"PAPER_CONTROL_STATE_CORRUPT:{collection_name}"
                    )
                if "time_stop_minutes" in row:
                    _require_exact_nonnegative_int(
                        row["time_stop_minutes"],
                        f"{collection_name}_{index}_TIME_STOP_MINUTES",
                    )

        for symbol, price in last_prices.items():
            if not isinstance(symbol, str):
                raise PaperControlStateError(
                    "PAPER_CONTROL_STATE_CORRUPT:LAST_PRICES"
                )
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                raise PaperControlStateError(
                    "PAPER_CONTROL_STATE_CORRUPT:LAST_PRICES"
                )
            if not math.isfinite(float(price)):
                raise PaperControlStateError(
                    "PAPER_CONTROL_STATE_CORRUPT:LAST_PRICES"
                )


def position_from_state(data: dict[str, Any]) -> Position:
    if not isinstance(data, dict):
        raise PaperControlStateError("PAPER_CONTROL_STATE_CORRUPT:POSITION")
    try:
        return Position(**data)
    except Exception as exc:
        raise PaperControlStateError("PAPER_CONTROL_STATE_CORRUPT:POSITION") from exc


def kernel_state_from_state(data: dict[str, Any]) -> KernelState:
    if not isinstance(data, dict):
        raise PaperControlStateError("PAPER_CONTROL_STATE_CORRUPT:RISK_STATE")
    try:
        return KernelState(**data)
    except Exception as exc:
        raise PaperControlStateError("PAPER_CONTROL_STATE_CORRUPT:RISK_STATE") from exc


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def bootstrap_from_closed_journal(
    rows: Iterable[dict[str, Any]],
    *,
    starting_cash: float,
    consecutive_loss_threshold: int,
    cooldown_minutes: int,
    max_daily_loss_pct: float,
    max_drawdown_pct: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Explicit one-time migration from a closed-trade journal.

    The caller must separately establish that no open PAPER position needs to
    survive the migration. This function never guesses an open position.
    """
    records = [dict(row) for row in rows]
    if not records:
        raise PaperControlStateError("PAPER_CONTROL_STATE_BOOTSTRAP_EMPTY_JOURNAL")
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = now_utc.strftime("%Y-%m-%d")
    cash = float(starting_cash)
    peak = float(starting_cash)
    daily_pnl = 0.0
    streak = 0
    last_loss_ts: str | None = None
    closed_positions: list[dict[str, Any]] = []
    last_prices: dict[str, float] = {}

    for row in records:
        pnl = float(row.get("realized_pnl_usd") or 0.0)
        cash += pnl
        peak = max(peak, cash)
        exit_dt = _parse_ts(row.get("exit_ts"))
        if exit_dt is not None and exit_dt.strftime("%Y-%m-%d") == today:
            daily_pnl += pnl
        if pnl < 0:
            streak += 1
            last_loss_ts = row.get("exit_ts")
        elif pnl > 0:
            streak = 0
            last_loss_ts = None

        symbol = str(row.get("symbol") or "")
        if symbol and row.get("exit_price") is not None:
            last_prices[symbol] = float(row["exit_price"])
        closed_positions.append(
            Position(
                position_id=str(row.get("trade_id") or f"legacy-{len(closed_positions)+1}"),
                symbol=symbol,
                direction=str(row.get("direction") or "LONG").upper(),
                qty=float(row.get("qty") or 0.0),
                avg_entry_price=float(row.get("entry_price") or 0.0),
                entry_ts=str(row.get("entry_ts") or ""),
                stop_price=0.0,
                target_price=0.0,
                status="CLOSED",
                exit_price=float(row.get("exit_price") or 0.0),
                exit_ts=str(row.get("exit_ts") or ""),
                exit_reason=str(row.get("exit_reason") or ""),
                realized_pnl=pnl,
                fees_paid=float(row.get("total_fees_usd") or 0.0),
                risk_usd=float(row.get("risk_usd") or 0.0),
                proposal_id=row.get("prediction_id"),
            ).to_dict()
        )

    drawdown_pct = ((peak - cash) / peak * 100.0) if peak > 0 else 0.0
    daily_pct = (daily_pnl / max(float(starting_cash), 1.0)) * 100.0
    cooldown_until: str | None = None
    if streak >= int(consecutive_loss_threshold) and last_loss_ts:
        last_loss_dt = _parse_ts(last_loss_ts)
        if last_loss_dt is not None:
            candidate = last_loss_dt + timedelta(minutes=int(cooldown_minutes))
            if candidate > now_utc:
                cooldown_until = candidate.isoformat()

    kill = False
    reason = ""
    if streak >= int(consecutive_loss_threshold) * 2:
        kill = True
        reason = f"migration:auto:{streak}_consecutive_losses"
    if daily_pct <= -float(max_daily_loss_pct) * 100.0:
        kill = True
        reason = f"migration:auto:daily_loss={daily_pct:.2f}%"
    if drawdown_pct >= float(max_drawdown_pct) * 100.0:
        kill = True
        reason = f"migration:auto:drawdown={drawdown_pct:.2f}%"

    risk_state = KernelState(
        current_day=today,
        daily_pnl_usd=daily_pnl,
        daily_pnl_pct=daily_pct,
        peak_equity=peak,
        current_equity=cash,
        drawdown_pct=drawdown_pct,
        consecutive_losses=streak,
        last_loss_ts=last_loss_ts,
        cooldown_until=cooldown_until,
        kill_switch_active=kill,
        kill_switch_reason=reason,
        kill_switch_set_at=now_utc.isoformat() if kill else None,
        vol_regime="NORMAL",
        vol_pct=0.0,
        proposals_evaluated=0,
        proposals_approved=0,
        proposals_rejected=0,
    ).to_dict()

    return {
        "version": STATE_VERSION,
        "paper_only": True,
        "saved_at": now_utc.isoformat(),
        "migration": "JOURNAL_BOOTSTRAP_EXPLICIT",
        "execution": {
            "cash": cash,
            "starting_cash": float(starting_cash),
            "total_orders": len(records),
            "order_count_semantics": "MINIMUM_CLOSED_TRADES_AT_BOOTSTRAP",
            "open_positions": [],
            "closed_positions": closed_positions,
        },
        "risk_state": risk_state,
        "last_prices": last_prices,
    }
