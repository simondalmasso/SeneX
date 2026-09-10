"""
SENECIO ORACLE — Polymarket-style Canonical Event Schema
=========================================================
Every event that flows through the system uses one of these typed models.
All events are timestamped (ISO8601 UTC), source-tagged, and traceable.

Canonical event types:
  MARKET_TICK       — price/volume update
  WALLET_ALERT      — whale / smart-money activity
  MARKET_CANDIDATE  — scanner-ranked opportunity
  SIGNAL            — brain decision (action vector)
  EXECUTION_SIM     — paper order fill
  RISK_STATE        — position / exposure snapshot
  AUDIT_TRACE       — decision/ error/ system trace
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str = "evt") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class EventType(str, enum.Enum):
    MARKET_TICK = "MARKET_TICK"
    WALLET_ALERT = "WALLET_ALERT"
    MARKET_CANDIDATE = "MARKET_CANDIDATE"
    SIGNAL = "SIGNAL"
    EXECUTION_SIM = "EXECUTION_SIM"
    RISK_STATE = "RISK_STATE"
    AUDIT_TRACE = "AUDIT_TRACE"


class Action(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"
    EXIT = "EXIT"
    WATCH = "WATCH"


class BaseEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    event_type: EventType
    ts: str = Field(default_factory=utc_now_iso)
    source: str = "senecio_oracle"
    symbol: Optional[str] = None
    trace_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)


class MarketTick(BaseEvent):
    event_type: Literal[EventType.MARKET_TICK] = EventType.MARKET_TICK
    symbol: str
    payload: dict[str, Any]  # {price, volume, bid, ask, ts_exchange, exchange}


class WalletAlert(BaseEvent):
    event_type: Literal[EventType.WALLET_ALERT] = EventType.WALLET_ALERT
    payload: dict[str, Any]  # {wallet, action, size_usd, token, tx_hash, label}


class MarketCandidate(BaseEvent):
    event_type: Literal[EventType.MARKET_CANDIDATE] = EventType.MARKET_CANDIDATE
    symbol: str
    payload: dict[str, Any]  # {scanner, score, rank, reasons, metrics}


class Signal(BaseEvent):
    event_type: Literal[EventType.SIGNAL] = EventType.SIGNAL
    symbol: str
    payload: dict[str, Any]  # {action, confidence, ev, sizing_usd, reasons[], checks{}}


class ExecutionSim(BaseEvent):
    event_type: Literal[EventType.EXECUTION_SIM] = EventType.EXECUTION_SIM
    symbol: str
    payload: dict[str, Any]  # {order_id, side, qty, fill_price, slippage_bps, status}


class RiskState(BaseEvent):
    event_type: Literal[EventType.RISK_STATE] = EventType.RISK_STATE
    payload: dict[str, Any]  # {positions[], gross_exposure, net_exposure, drawdown_pct}


class AuditTrace(BaseEvent):
    event_type: Literal[EventType.AUDIT_TRACE] = EventType.AUDIT_TRACE
    payload: dict[str, Any]  # {layer, msg, severity, context}


def to_log_line(ev: BaseEvent) -> str:
    """Compact JSONL representation for audit log."""
    return ev.model_dump_json()


def from_log_line(line: str) -> BaseEvent:
    """Reconstruct event from JSONL. Returns BaseEvent (loose typing)."""
    import json
    data = json.loads(line)
    et = data.get("event_type")
    mapping = {
        EventType.MARKET_TICK: MarketTick,
        EventType.WALLET_ALERT: WalletAlert,
        EventType.MARKET_CANDIDATE: MarketCandidate,
        EventType.SIGNAL: Signal,
        EventType.EXECUTION_SIM: ExecutionSim,
        EventType.RISK_STATE: RiskState,
        EventType.AUDIT_TRACE: AuditTrace,
    }
    cls = mapping.get(et, BaseEvent)
    return cls(**data)
