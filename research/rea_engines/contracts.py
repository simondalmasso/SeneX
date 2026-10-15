"""Immutable contracts for the isolated REA historical replay lab."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional


class ContractError(ValueError):
    """Raised when input violates a replay contract."""


def as_decimal(value: object, field_name: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ContractError(f"{field_name} must be numeric, not boolean")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ContractError(f"{field_name} must be a finite decimal") from None
    if not result.is_finite():
        raise ContractError(f"{field_name} must be finite")
    if positive and result <= 0:
        raise ContractError(f"{field_name} must be positive")
    if non_negative and result < 0:
        raise ContractError(f"{field_name} must be non-negative")
    return result


def normalize_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ContractError("timestamp must be an ISO-8601 datetime") from None
    else:
        raise ContractError("timestamp must be a non-empty ISO-8601 datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError("timestamp must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Candle:
    timestamp: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp))
        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, as_decimal(getattr(self, name), name, positive=True))
        object.__setattr__(self, "volume", as_decimal(self.volume, "volume", non_negative=True))
        if self.high < max(self.open, self.close):
            raise ContractError("high must be at least open and close")
        if self.low > min(self.open, self.close):
            raise ContractError("low must be at most open and close")
        if self.high < self.low:
            raise ContractError("high must be greater than or equal to low")


@dataclass(frozen=True)
class OrderIntent:
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Optional[Decimal] = None
    expires_after_bars: Optional[int] = None
    tag: str = ""

    def __post_init__(self) -> None:
        side = str(self.side).strip().lower()
        order_type = str(self.order_type).strip().lower()
        if side not in {"buy", "sell"}:
            raise ContractError("side must be 'buy' or 'sell'")
        if order_type not in {"market", "limit"}:
            raise ContractError("order_type must be 'market' or 'limit'")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "order_type", order_type)
        object.__setattr__(self, "quantity", as_decimal(self.quantity, "quantity", positive=True))
        if order_type == "limit":
            if self.limit_price is None:
                raise ContractError("limit orders require limit_price")
            object.__setattr__(self, "limit_price", as_decimal(self.limit_price, "limit_price", positive=True))
        elif self.limit_price is not None:
            raise ContractError("market orders must not specify limit_price")
        if self.expires_after_bars is not None:
            if isinstance(self.expires_after_bars, bool) or not isinstance(self.expires_after_bars, int):
                raise ContractError("expires_after_bars must be a positive integer or None")
            if self.expires_after_bars <= 0:
                raise ContractError("expires_after_bars must be a positive integer or None")
        if not isinstance(self.tag, str):
            raise ContractError("tag must be a string")


@dataclass(frozen=True)
class CancelIntent:
    order_id: str
    tag: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ContractError("order_id must be a non-empty string")
        if not isinstance(self.tag, str):
            raise ContractError("tag must be a string")


@dataclass(frozen=True)
class CostProfile:
    fee_bps: Decimal = Decimal("10")
    spread_bps: Decimal = Decimal("5")
    slippage_bps: Decimal = Decimal("5")
    profile_id: str = "rea-base-v1"

    def __post_init__(self) -> None:
        for name in ("fee_bps", "spread_bps", "slippage_bps"):
            object.__setattr__(self, name, as_decimal(getattr(self, name), name, non_negative=True))
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ContractError("profile_id must be a non-empty string")

    def scaled(self, factor: object, profile_id: Optional[str] = None) -> "CostProfile":
        multiplier = as_decimal(factor, "factor", positive=True)
        return CostProfile(
            fee_bps=self.fee_bps * multiplier,
            spread_bps=self.spread_bps * multiplier,
            slippage_bps=self.slippage_bps * multiplier,
            profile_id=profile_id or f"{self.profile_id}-x{multiplier}",
        )


@dataclass(frozen=True)
class PortfolioSnapshot:
    cash: Decimal
    inventory_qty: Decimal
    average_entry_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    equity: Decimal
    total_fees: Decimal = Decimal("0")
    total_spread_cost: Decimal = Decimal("0")
    total_slippage_cost: Decimal = Decimal("0")
    turnover: Decimal = Decimal("0")


@dataclass(frozen=True)
class PendingOrder:
    order_id: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Optional[Decimal]
    submitted_index: int
    submitted_timestamp: str
    expires_after_bars: Optional[int]
    tag: str = ""
    bars_waited: int = 0


@dataclass(frozen=True)
class Fill:
    order_id: str
    timestamp: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal
    notional: Decimal
    fee: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    realized_pnl: Decimal
    tag: str = ""


@dataclass(frozen=True)
class Rejection:
    order_id: str
    timestamp: str
    reason: str
    side: str
    quantity: Decimal
    tag: str = ""
