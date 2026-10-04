"""Isolated Binance-style PAPER wallet for SENEX.

This lane is intentionally incapable of live trading:
- no Binance SDK;
- no ccxt;
- no signer/wallet/API credentials;
- no outbound network calls;
- no public mutation endpoint.

It consumes the already-produced SENEX BTC prediction + observed reference price
and maintains a completely separate simulated USDT ledger. It must never be
confused with the native ACT-XXV PAPER portfolio or a real Binance balance.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LANE_ID = "BINANCE_SIM_18_63631644"
VENUE = "BINANCE_SIMULATED"
STARTING_BANKROLL_USDT = 18.63631644

RISK_PCT = 0.005
STOP_PCT = 0.02
TARGET_PCT = 0.04
MAX_NOTIONAL_PCT = 0.25
ENTRY_SLIPPAGE_BPS = 2.0
EXIT_SLIPPAGE_BPS = 3.0
TAKER_FEE_BPS = 5.0
TIME_STOP_SECONDS = 60 * 60
MAX_CLOSED_TRADES = 500


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        if math.isfinite(result):
            return result
        raise ValueError("timestamp must be finite")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp is required")
    raw = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc).timestamp()


def _iso_from_epoch(value: float) -> str:
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_symbol(value: object) -> str:
    return str(value or "").upper().replace("/", "").replace("-", "").strip()


def _finite_positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


class BinanceSimLane:
    """One-position isolated simulated USDT wallet.

    The lane follows the latest directional SENEX BTC signal and exits only on
    stop, target, or 60-minute time stop. A close never re-enters on the same
    prediction tick.
    """

    def __init__(
        self,
        *,
        state_path: str | Path,
        starting_bankroll_usdt: float = STARTING_BANKROLL_USDT,
    ):
        self.state_path = Path(state_path)
        self.starting_bankroll_usdt = float(starting_bankroll_usdt)
        if not math.isfinite(self.starting_bankroll_usdt) or self.starting_bankroll_usdt <= 0:
            raise ValueError("starting bankroll must be finite and positive")
        self._load_error: str | None = None
        self._state = self._fresh_state()
        self._load_if_present()

    def _fresh_state(self) -> dict[str, Any]:
        now = _utc_now()
        return {
            "version": 1,
            "lane_id": LANE_ID,
            "venue": VENUE,
            "simulation_only": True,
            "live_orders_possible": False,
            "starting_bankroll_usdt": self.starting_bankroll_usdt,
            "cash_usdt": self.starting_bankroll_usdt,
            "fees_paid_usdt": 0.0,
            "realized_pnl_usdt": 0.0,
            "total_simulated_orders": 0,
            "closed_trades": [],
            "open_position": None,
            "last_mark_price": None,
            "last_signal": None,
            "created_at": now,
            "updated_at": now,
        }

    def _load_if_present(self) -> None:
        if not self.state_path.exists():
            return
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("state must be an object")
            if loaded.get("lane_id") != LANE_ID:
                raise ValueError("lane identity mismatch")
            if float(loaded.get("starting_bankroll_usdt")) != self.starting_bankroll_usdt:
                raise ValueError("starting bankroll mismatch")
            self._state = loaded
        except Exception as exc:
            # Fail closed: expose the load error and refuse trading. Never
            # silently overwrite a possibly valuable simulated ledger.
            self._load_error = f"{type(exc).__name__}: {exc}"

    def _persist(self) -> None:
        if self._load_error is not None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self._state, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.state_path)

    @staticmethod
    def _directional_pnl(position: dict[str, Any], mark_price: float) -> float:
        qty = float(position["qty"])
        entry = float(position["entry_price"])
        if position["direction"] == "LONG":
            return qty * (float(mark_price) - entry)
        return qty * (entry - float(mark_price))

    def _equity(self) -> float:
        cash = float(self._state["cash_usdt"])
        position = self._state.get("open_position")
        mark = _finite_positive(self._state.get("last_mark_price"))
        if not isinstance(position, dict) or mark is None:
            return cash
        gross = self._directional_pnl(position, mark)
        exit_notional = abs(float(position["qty"]) * mark)
        est_exit_fee = exit_notional * TAKER_FEE_BPS / 10_000.0
        return cash + gross - est_exit_fee

    def _open(
        self,
        *,
        direction: str,
        reference_price: float,
        event_ts: float,
        prediction: dict[str, Any],
    ) -> dict[str, Any]:
        equity_before = self._equity()
        if equity_before <= 0:
            return {"action": "IGNORE", "reason": "NO_EQUITY"}

        notional = min(
            equity_before * MAX_NOTIONAL_PCT,
            max(0.0, float(self._state["cash_usdt"])),
        )
        if notional <= 0:
            return {"action": "IGNORE", "reason": "NO_CASH"}

        entry_sign = 1.0 if direction == "LONG" else -1.0
        entry_price = reference_price * (
            1.0 + entry_sign * ENTRY_SLIPPAGE_BPS / 10_000.0
        )
        qty = notional / entry_price
        entry_fee = notional * TAKER_FEE_BPS / 10_000.0

        if direction == "LONG":
            stop_price = entry_price * (1.0 - STOP_PCT)
            target_price = entry_price * (1.0 + TARGET_PCT)
        else:
            stop_price = entry_price * (1.0 + STOP_PCT)
            target_price = entry_price * (1.0 - TARGET_PCT)

        position = {
            "position_id": f"sim-{int(event_ts)}-{int(prediction.get('id') or 0)}",
            "symbol": "BTCUSDT",
            "direction": direction,
            "reference_price": reference_price,
            "entry_price": entry_price,
            "qty": qty,
            "notional_usdt": notional,
            "stop_price": stop_price,
            "target_price": target_price,
            "opened_at": _iso_from_epoch(event_ts),
            "opened_epoch": event_ts,
            "prediction_id": prediction.get("id"),
            "confidence": prediction.get("confidence"),
            "entry_fee_usdt": entry_fee,
        }
        self._state["cash_usdt"] = float(self._state["cash_usdt"]) - entry_fee
        self._state["fees_paid_usdt"] = float(self._state["fees_paid_usdt"]) + entry_fee
        self._state["total_simulated_orders"] = int(self._state["total_simulated_orders"]) + 1
        self._state["open_position"] = position
        self._state["updated_at"] = _iso_from_epoch(event_ts)
        self._persist()
        return {
            "action": "OPEN",
            "lane_id": LANE_ID,
            "direction": direction,
            "notional_usdt": notional,
            "entry_price": entry_price,
            "simulation_only": True,
        }

    def _close(
        self,
        *,
        reference_price: float,
        event_ts: float,
        reason: str,
    ) -> dict[str, Any]:
        position = self._state.get("open_position")
        if not isinstance(position, dict):
            return {"action": "IGNORE", "reason": "NO_OPEN_POSITION"}

        exit_sign = -1.0 if position["direction"] == "LONG" else 1.0
        exit_price = reference_price * (
            1.0 + exit_sign * EXIT_SLIPPAGE_BPS / 10_000.0
        )
        gross_pnl = self._directional_pnl(position, exit_price)
        exit_notional = abs(float(position["qty"]) * exit_price)
        exit_fee = exit_notional * TAKER_FEE_BPS / 10_000.0
        entry_fee = float(position.get("entry_fee_usdt") or 0.0)
        net_trade_pnl = gross_pnl - entry_fee - exit_fee

        self._state["cash_usdt"] = float(self._state["cash_usdt"]) + gross_pnl - exit_fee
        self._state["fees_paid_usdt"] = float(self._state["fees_paid_usdt"]) + exit_fee
        self._state["realized_pnl_usdt"] = (
            float(self._state["cash_usdt"]) - self.starting_bankroll_usdt
        )

        trade = {
            **position,
            "exit_reference_price": reference_price,
            "exit_price": exit_price,
            "closed_at": _iso_from_epoch(event_ts),
            "exit_reason": reason,
            "gross_pnl_usdt": gross_pnl,
            "exit_fee_usdt": exit_fee,
            "net_pnl_usdt": net_trade_pnl,
            "cash_after_usdt": float(self._state["cash_usdt"]),
        }
        trades = list(self._state.get("closed_trades") or [])
        trades.append(trade)
        self._state["closed_trades"] = trades[-MAX_CLOSED_TRADES:]
        self._state["open_position"] = None
        self._state["updated_at"] = _iso_from_epoch(event_ts)
        self._persist()
        return {
            "action": "CLOSE",
            "lane_id": LANE_ID,
            "reason": reason,
            "net_pnl_usdt": net_trade_pnl,
            "cash_after_usdt": float(self._state["cash_usdt"]),
            "simulation_only": True,
        }

    def _exit_reason(self, position: dict[str, Any], mark: float, event_ts: float) -> str | None:
        direction = str(position["direction"])
        if direction == "LONG":
            if mark <= float(position["stop_price"]):
                return "STOP"
            if mark >= float(position["target_price"]):
                return "TARGET"
        else:
            if mark >= float(position["stop_price"]):
                return "STOP"
            if mark <= float(position["target_price"]):
                return "TARGET"
        if event_ts - float(position["opened_epoch"]) >= TIME_STOP_SECONDS:
            return "TIME_STOP"
        return None

    def on_prediction(self, prediction: dict[str, Any]) -> dict[str, Any]:
        """Consume one already-produced oracle prediction.

        This method performs no I/O other than the local lane JSON file.
        """
        if self._load_error is not None:
            return {"action": "BLOCKED", "reason": "STATE_LOAD_ERROR"}

        if not isinstance(prediction, dict):
            return {"action": "IGNORE", "reason": "INVALID_PREDICTION"}
        if _normalize_symbol(prediction.get("symbol")) != "BTCUSDT":
            return {"action": "IGNORE", "reason": "NON_BTC_SYMBOL"}

        direction = str(prediction.get("prediction") or "").upper()

        reference_price = _finite_positive(prediction.get("price_now"))
        if reference_price is None:
            return {"action": "IGNORE", "reason": "INVALID_REFERENCE_PRICE"}

        try:
            event_ts = _parse_ts(prediction.get("ts") or prediction.get("timestamp"))
        except Exception:
            return {"action": "IGNORE", "reason": "INVALID_TIMESTAMP"}

        self._state["last_mark_price"] = reference_price
        self._state["last_signal"] = {
            "prediction_id": prediction.get("id"),
            "direction": direction or "UNKNOWN",
            "confidence": prediction.get("confidence"),
            "reference_price": reference_price,
            "ts": _iso_from_epoch(event_ts),
        }

        # Existing exposure is managed on every fresh BTC price observation,
        # even when the new prediction is FLAT/ABSTAIN. Entry eligibility is
        # evaluated only after exit/hold handling.
        position = self._state.get("open_position")
        if isinstance(position, dict):
            reason = self._exit_reason(position, reference_price, event_ts)
            if reason is not None:
                return self._close(
                    reference_price=reference_price,
                    event_ts=event_ts,
                    reason=reason,
                )
            self._state["updated_at"] = _iso_from_epoch(event_ts)
            self._persist()
            return {
                "action": "HOLD",
                "reason": "POSITION_ALREADY_OPEN",
                "position_id": position.get("position_id"),
                "simulation_only": True,
            }

        if direction not in {"LONG", "SHORT"}:
            self._state["updated_at"] = _iso_from_epoch(event_ts)
            self._persist()
            return {"action": "IGNORE", "reason": "NON_DIRECTIONAL_SIGNAL"}

        return self._open(
            direction=direction,
            reference_price=reference_price,
            event_ts=event_ts,
            prediction=prediction,
        )

    def public_state(self) -> dict[str, Any]:
        """Return a bounded, explicitly simulated read-only projection."""
        position = self._state.get("open_position")
        mark = _finite_positive(self._state.get("last_mark_price"))
        unrealized = None
        if isinstance(position, dict) and mark is not None:
            gross = self._directional_pnl(position, mark)
            exit_fee = abs(float(position["qty"]) * mark) * TAKER_FEE_BPS / 10_000.0
            unrealized = gross - exit_fee

        trades = list(self._state.get("closed_trades") or [])
        status = "STATE_LOAD_ERROR" if self._load_error is not None else "OK"
        return {
            "status": status,
            "lane_id": LANE_ID,
            "venue": VENUE,
            "account_label": "SIMULATED / PAPER — NOT BINANCE BALANCE",
            "simulation_only": True,
            "live_orders_possible": False,
            "hard_paper_lock": True,
            "research_verdict": "INCREMENTAL_EDGE_NOT_DEMONSTRATED",
            "edge": "UNPROVEN",
            "starting_bankroll_usdt": self.starting_bankroll_usdt,
            "cash_usdt": float(self._state["cash_usdt"]),
            "equity_usdt": self._equity(),
            "fees_paid_usdt": float(self._state["fees_paid_usdt"]),
            "realized_pnl_usdt": float(self._state["realized_pnl_usdt"]),
            "unrealized_pnl_usdt": unrealized,
            "open_position": position,
            "closed_trade_count": len(trades),
            "total_simulated_orders": int(self._state["total_simulated_orders"]),
            "recent_trades": list(reversed(trades[-20:])),
            "last_mark_price": mark,
            "last_signal": self._state.get("last_signal"),
            "risk_policy": {
                "risk_pct": RISK_PCT,
                "max_notional_pct": MAX_NOTIONAL_PCT,
                "stop_pct": STOP_PCT,
                "target_pct": TARGET_PCT,
                "time_stop_minutes": TIME_STOP_SECONDS // 60,
                "taker_fee_bps": TAKER_FEE_BPS,
                "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
                "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
                "max_open_positions": 1,
            },
            "state_path_name": self.state_path.name,
            "updated_at": self._state.get("updated_at"),
            "load_error": self._load_error,
        }

    def public_trades(self, limit: int = 20) -> dict[str, Any]:
        bounded = max(1, min(int(limit), 50))
        trades = list(self._state.get("closed_trades") or [])
        rows = list(reversed(trades[-bounded:]))
        return {
            "status": "OK" if self._load_error is None else "STATE_LOAD_ERROR",
            "lane_id": LANE_ID,
            "venue": VENUE,
            "simulation_only": True,
            "live_orders_possible": False,
            "count": len(rows),
            "limit": bounded,
            "trades": rows,
        }
