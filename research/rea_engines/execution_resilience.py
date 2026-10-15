"""Offline execution-fidelity and restart/reconciliation test harness.

This module has no network or exchange adapter and is not imported by SENEX runtime.
All state is an explicit PAPER-only record; snapshots are caller-supplied test data.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Optional

from .contracts import Candle, as_decimal, normalize_timestamp

ZERO = Decimal("0")
CHECKPOINT_SCHEMA_VERSION = 1


class ResilienceError(ValueError):
    """Raised when paper execution/recovery inputs fail closed."""


class IntrabarPolicy(str, Enum):
    WORST_CASE_STOP_FIRST = "worst_case_stop_first"
    BEST_CASE_TARGET_FIRST = "best_case_target_first"
    REJECT_AMBIGUOUS = "reject_ambiguous"


@dataclass(frozen=True)
class IntrabarDecision:
    trigger: str
    fill_price: Optional[Decimal]
    ambiguous: bool
    resolution_policy: str
    candle_timestamp: str


def resolve_long_bracket(
    candle: Candle,
    stop_price: object,
    target_price: object,
    *,
    policy: IntrabarPolicy = IntrabarPolicy.WORST_CASE_STOP_FIRST,
) -> IntrabarDecision:
    """Resolve a long-only bracket with explicit handling for unknowable OHLC path."""
    if not isinstance(candle, Candle):
        raise ResilienceError("candle must be a validated Candle")
    try:
        stop = as_decimal(stop_price, "stop_price", positive=True)
        target = as_decimal(target_price, "target_price", positive=True)
    except ValueError as exc:
        raise ResilienceError(str(exc)) from exc
    if stop >= target:
        raise ResilienceError("long stop_price must be below target_price")
    if not isinstance(policy, IntrabarPolicy):
        raise ResilienceError("policy must be an IntrabarPolicy")
    if candle.open <= stop:
        return IntrabarDecision("STOP", candle.open, False, policy.value, candle.timestamp)
    if candle.open >= target:
        return IntrabarDecision("TARGET", candle.open, False, policy.value, candle.timestamp)
    stop_hit = candle.low <= stop
    target_hit = candle.high >= target
    if stop_hit and target_hit:
        if policy is IntrabarPolicy.WORST_CASE_STOP_FIRST:
            return IntrabarDecision("STOP", stop, True, policy.value, candle.timestamp)
        if policy is IntrabarPolicy.BEST_CASE_TARGET_FIRST:
            return IntrabarDecision("TARGET", target, True, policy.value, candle.timestamp)
        return IntrabarDecision("AMBIGUOUS", None, True, policy.value, candle.timestamp)
    if stop_hit:
        return IntrabarDecision("STOP", stop, False, policy.value, candle.timestamp)
    if target_hit:
        return IntrabarDecision("TARGET", target, False, policy.value, candle.timestamp)
    return IntrabarDecision("NONE", None, False, policy.value, candle.timestamp)


def _sha(value: object, field: str) -> str:
    text = str(value).strip().lower()
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise ResilienceError(f"{field} must be a 64-character hexadecimal SHA-256")
    return text


def _dec(value: object, field: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    try:
        return as_decimal(value, field, positive=positive, non_negative=non_negative)
    except (TypeError, ValueError) as exc:
        raise ResilienceError(str(exc)) from exc


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest_payload(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    client_order_id: str
    side: str
    requested_qty: Decimal
    filled_qty: Decimal = ZERO
    status: str = "OPEN"

    def __post_init__(self) -> None:
        if not self.order_id.strip() or not self.client_order_id.strip():
            raise ResilienceError("order IDs must be non-empty")
        side = self.side.strip().lower()
        if side not in {"buy", "sell"}:
            raise ResilienceError("order side must be buy or sell")
        requested = _dec(self.requested_qty, "requested_qty", positive=True)
        filled = _dec(self.filled_qty, "filled_qty", non_negative=True)
        if filled > requested:
            raise ResilienceError("filled_qty cannot exceed requested_qty")
        expected = "OPEN" if filled == ZERO else ("FILLED" if filled == requested else "PARTIALLY_FILLED")
        if self.status != expected:
            raise ResilienceError(f"order status mismatch: expected {expected}")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "requested_qty", requested)
        object.__setattr__(self, "filled_qty", filled)

    @property
    def remaining_qty(self) -> Decimal:
        return self.requested_qty - self.filled_qty


@dataclass(frozen=True)
class PaperState:
    schema_version: int
    source_sha256: str
    config_sha256: str
    starting_cash: Decimal
    cash: Decimal
    inventory_qty: Decimal = ZERO
    inventory_cost_basis: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    total_fees: Decimal = ZERO
    total_spread_cost: Decimal = ZERO
    total_slippage_cost: Decimal = ZERO
    turnover: Decimal = ZERO
    last_processed_timestamp: Optional[str] = None
    last_event_seq: int = 0
    orders: tuple[PaperOrder, ...] = ()
    processed_fill_ids: tuple[str, ...] = ()
    processed_fill_fingerprints: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise ResilienceError("unsupported checkpoint schema_version")
        object.__setattr__(self, "source_sha256", _sha(self.source_sha256, "source_sha256"))
        object.__setattr__(self, "config_sha256", _sha(self.config_sha256, "config_sha256"))
        object.__setattr__(self, "starting_cash", _dec(self.starting_cash, "starting_cash", positive=True))
        object.__setattr__(self, "cash", _dec(self.cash, "cash", non_negative=True))
        for field in ("inventory_qty", "inventory_cost_basis", "total_fees", "total_spread_cost", "total_slippage_cost", "turnover"):
            object.__setattr__(self, field, _dec(getattr(self, field), field, non_negative=True))
        object.__setattr__(self, "realized_pnl", _dec(self.realized_pnl, "realized_pnl"))
        if self.inventory_qty == ZERO and self.inventory_cost_basis != ZERO:
            raise ResilienceError("zero inventory must have zero inventory_cost_basis")
        if isinstance(self.last_event_seq, bool) or not isinstance(self.last_event_seq, int) or self.last_event_seq < 0:
            raise ResilienceError("last_event_seq must be a non-negative integer")
        if self.last_processed_timestamp is not None:
            object.__setattr__(self, "last_processed_timestamp", normalize_timestamp(self.last_processed_timestamp))
        ids = [order.order_id for order in self.orders]
        clients = [order.client_order_id for order in self.orders]
        if len(ids) != len(set(ids)) or len(clients) != len(set(clients)):
            raise ResilienceError("checkpoint contains duplicate order or client order IDs")
        if len(self.processed_fill_ids) != len(set(self.processed_fill_ids)):
            raise ResilienceError("checkpoint contains duplicate processed fill IDs")
        if any(not isinstance(fid, str) or not fid.strip() for fid in self.processed_fill_ids):
            raise ResilienceError("processed fill IDs must be non-empty strings")
        fingerprint_ids = [fid for fid, _ in self.processed_fill_fingerprints]
        if fingerprint_ids != list(self.processed_fill_ids):
            raise ResilienceError("processed fill fingerprints must match processed fill IDs in order")
        for _, fingerprint in self.processed_fill_fingerprints:
            _sha(fingerprint, "fill_fingerprint")

    @classmethod
    def initial(cls, *, starting_cash: object, source_sha256: str, config_sha256: str) -> "PaperState":
        cash = _dec(starting_cash, "starting_cash", positive=True)
        return cls(
            schema_version=CHECKPOINT_SCHEMA_VERSION, source_sha256=source_sha256,
            config_sha256=config_sha256, starting_cash=cash, cash=cash,
        )

    def to_payload(self) -> dict:
        return {
            "schema_version": self.schema_version, "source_sha256": self.source_sha256,
            "config_sha256": self.config_sha256, "starting_cash": str(self.starting_cash),
            "cash": str(self.cash), "inventory_qty": str(self.inventory_qty),
            "inventory_cost_basis": str(self.inventory_cost_basis), "realized_pnl": str(self.realized_pnl),
            "total_fees": str(self.total_fees), "total_spread_cost": str(self.total_spread_cost),
            "total_slippage_cost": str(self.total_slippage_cost), "turnover": str(self.turnover),
            "last_processed_timestamp": self.last_processed_timestamp, "last_event_seq": self.last_event_seq,
            "orders": [{
                "order_id": o.order_id, "client_order_id": o.client_order_id, "side": o.side,
                "requested_qty": str(o.requested_qty), "filled_qty": str(o.filled_qty), "status": o.status,
            } for o in self.orders],
            "processed_fill_ids": list(self.processed_fill_ids),
            "processed_fill_fingerprints": [
                {"fill_id": fid, "sha256": fingerprint}
                for fid, fingerprint in self.processed_fill_fingerprints
            ],
        }

    @classmethod
    def from_payload(cls, payload: object) -> "PaperState":
        if not isinstance(payload, dict):
            raise ResilienceError("checkpoint state must be an object")
        required = {
            "schema_version", "source_sha256", "config_sha256", "starting_cash", "cash",
            "inventory_qty", "inventory_cost_basis", "realized_pnl", "total_fees",
            "total_spread_cost", "total_slippage_cost", "turnover", "last_processed_timestamp",
            "last_event_seq", "orders", "processed_fill_ids", "processed_fill_fingerprints",
        }
        if set(payload) != required:
            raise ResilienceError("checkpoint state fields do not match schema")
        try:
            orders = tuple(PaperOrder(**order) for order in payload["orders"])
            return cls(
                schema_version=payload["schema_version"], source_sha256=payload["source_sha256"],
                config_sha256=payload["config_sha256"], starting_cash=payload["starting_cash"],
                cash=payload["cash"], inventory_qty=payload["inventory_qty"],
                inventory_cost_basis=payload["inventory_cost_basis"], realized_pnl=payload["realized_pnl"],
                total_fees=payload["total_fees"], total_spread_cost=payload["total_spread_cost"],
                total_slippage_cost=payload["total_slippage_cost"], turnover=payload["turnover"],
                last_processed_timestamp=payload["last_processed_timestamp"], last_event_seq=payload["last_event_seq"],
                orders=orders, processed_fill_ids=tuple(payload["processed_fill_ids"]),
                processed_fill_fingerprints=tuple(
                    (item["fill_id"], item["sha256"]) for item in payload["processed_fill_fingerprints"]
                ),
            )
        except (TypeError, KeyError) as exc:
            raise ResilienceError(f"invalid checkpoint state: {exc}") from exc

    def checkpoint_sha256(self) -> str:
        return _digest_payload(self.to_payload())


@dataclass(frozen=True)
class FillEvent:
    fill_id: str
    order_id: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    timestamp: str
    event_seq: int

    def fingerprint_sha256(self) -> str:
        return _digest_payload({
            "fill_id": self.fill_id, "order_id": self.order_id, "side": self.side,
            "quantity": str(self.quantity), "price": str(self.price), "fee": str(self.fee),
            "spread_cost": str(self.spread_cost), "slippage_cost": str(self.slippage_cost),
            "timestamp": self.timestamp, "event_seq": self.event_seq,
        })

    def __post_init__(self) -> None:
        if not self.fill_id.strip() or not self.order_id.strip():
            raise ResilienceError("fill_id and order_id must be non-empty")
        side = self.side.strip().lower()
        if side not in {"buy", "sell"}:
            raise ResilienceError("fill side must be buy or sell")
        for name, positive, nonnegative in (
            ("quantity", True, False), ("price", True, False),
            ("fee", False, True), ("spread_cost", False, True), ("slippage_cost", False, True),
        ):
            object.__setattr__(self, name, _dec(getattr(self, name), name, positive=positive, non_negative=nonnegative))
        if isinstance(self.event_seq, bool) or not isinstance(self.event_seq, int) or self.event_seq <= 0:
            raise ResilienceError("event_seq must be a positive integer")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp))


@dataclass(frozen=True)
class FillApplication:
    applied: bool
    duplicate: bool
    status: str
    remaining_qty: Decimal


def register_order(state: PaperState, *, order_id: str, client_order_id: str, side: str, quantity: object) -> PaperState:
    """Register a paper order idempotently; conflicting client IDs fail closed."""
    requested = _dec(quantity, "quantity", positive=True)
    side = str(side).strip().lower()
    if side not in {"buy", "sell"}:
        raise ResilienceError("order side must be buy or sell")
    order_id, client_order_id = str(order_id).strip(), str(client_order_id).strip()
    if not order_id or not client_order_id:
        raise ResilienceError("order IDs must be non-empty")
    for existing in state.orders:
        same = (
            existing.order_id == order_id and existing.client_order_id == client_order_id
            and existing.side == side and existing.requested_qty == requested
        )
        if same:
            return state
        if existing.order_id == order_id or existing.client_order_id == client_order_id:
            raise ResilienceError("order submission idempotency conflict")
    order = PaperOrder(order_id, client_order_id, side, requested)
    return replace(state, orders=state.orders + (order,))


def apply_fill(state: PaperState, event: FillEvent) -> tuple[PaperState, FillApplication]:
    """Apply a sequenced paper fill, including partials; duplicate fill IDs no-op."""
    order = next((item for item in state.orders if item.order_id == event.order_id), None)
    if event.fill_id in state.processed_fill_ids:
        if order is None:
            raise ResilienceError("processed fill references an unknown order")
        previous = dict(state.processed_fill_fingerprints).get(event.fill_id)
        if previous != event.fingerprint_sha256():
            raise ResilienceError("fill idempotency conflict: duplicate fill ID has different payload")
        return state, FillApplication(False, True, order.status, order.remaining_qty)
    if order is None:
        raise ResilienceError("fill references an unknown order")
    if event.event_seq != state.last_event_seq + 1:
        raise ResilienceError("fill event sequence gap or stale unseen event")
    if state.last_processed_timestamp is not None and event.timestamp < state.last_processed_timestamp:
        raise ResilienceError("fill timestamp must not move backwards")
    if order.status == "FILLED":
        raise ResilienceError("fill received for an already filled order")
    if event.side != order.side:
        raise ResilienceError("fill side differs from registered order")
    if event.quantity > order.remaining_qty:
        raise ResilienceError("fill quantity exceeds order remaining quantity")
    notional = event.quantity * event.price
    costs = event.fee + event.spread_cost + event.slippage_cost
    if event.side == "buy":
        debit = notional + costs
        if debit > state.cash:
            raise ResilienceError("insufficient paper cash for fill and costs")
        cash, inventory = state.cash - debit, state.inventory_qty + event.quantity
        basis, realized = state.inventory_cost_basis + debit, state.realized_pnl
    else:
        if event.quantity > state.inventory_qty:
            raise ResilienceError("sell fill would create a short position")
        if costs > notional:
            raise ResilienceError("sale costs exceed gross proceeds")
        proceeds = notional - costs
        allocated = state.inventory_cost_basis if event.quantity == state.inventory_qty else state.inventory_cost_basis * event.quantity / state.inventory_qty
        cash, inventory = state.cash + proceeds, state.inventory_qty - event.quantity
        basis = state.inventory_cost_basis - allocated
        if inventory == ZERO:
            basis = ZERO
        realized = state.realized_pnl + proceeds - allocated
    filled = order.filled_qty + event.quantity
    status = "FILLED" if filled == order.requested_qty else "PARTIALLY_FILLED"
    changed_order = replace(order, filled_qty=filled, status=status)
    orders = tuple(changed_order if o.order_id == order.order_id else o for o in state.orders)
    changed = replace(
        state, cash=cash, inventory_qty=inventory, inventory_cost_basis=basis, realized_pnl=realized,
        total_fees=state.total_fees + event.fee, total_spread_cost=state.total_spread_cost + event.spread_cost,
        total_slippage_cost=state.total_slippage_cost + event.slippage_cost, turnover=state.turnover + notional,
        last_processed_timestamp=event.timestamp, last_event_seq=event.event_seq,
        orders=orders, processed_fill_ids=state.processed_fill_ids + (event.fill_id,),
        processed_fill_fingerprints=state.processed_fill_fingerprints + ((event.fill_id, event.fingerprint_sha256()),),
    )
    return changed, FillApplication(True, False, status, changed_order.remaining_qty)


@dataclass(frozen=True)
class SnapshotOrder:
    order_id: str
    side: str
    remaining_qty: Decimal

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ResilienceError("snapshot order_id must be non-empty")
        side = self.side.strip().lower()
        if side not in {"buy", "sell"}:
            raise ResilienceError("snapshot order side must be buy or sell")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "remaining_qty", _dec(self.remaining_qty, "remaining_qty", positive=True))


@dataclass(frozen=True)
class ExecutionSnapshot:
    cash: Decimal
    inventory_qty: Decimal
    open_orders: tuple[SnapshotOrder, ...]
    last_event_seq: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "cash", _dec(self.cash, "snapshot cash", non_negative=True))
        object.__setattr__(self, "inventory_qty", _dec(self.inventory_qty, "snapshot inventory_qty", non_negative=True))
        if isinstance(self.last_event_seq, bool) or not isinstance(self.last_event_seq, int) or self.last_event_seq < 0:
            raise ResilienceError("snapshot last_event_seq must be a non-negative integer")
        ids = [o.order_id for o in self.open_orders]
        if len(ids) != len(set(ids)):
            raise ResilienceError("snapshot contains duplicate open order IDs")


@dataclass(frozen=True)
class ReconciliationReport:
    matched: bool
    discrepancies: tuple[str, ...]
    local_event_seq: int
    observed_event_seq: int


def reconcile_snapshot(local: PaperState, observed: ExecutionSnapshot, *, tolerance: object = ZERO) -> ReconciliationReport:
    """Compare local paper state with a supplied snapshot; never auto-correct silently."""
    tol = _dec(tolerance, "tolerance", non_negative=True)
    issues: list[str] = []
    if abs(local.cash - observed.cash) > tol:
        issues.append(f"cash_mismatch:local={local.cash}:observed={observed.cash}")
    if abs(local.inventory_qty - observed.inventory_qty) > tol:
        issues.append(f"inventory_mismatch:local={local.inventory_qty}:observed={observed.inventory_qty}")
    if local.last_event_seq != observed.last_event_seq:
        issues.append(f"event_sequence_mismatch:local={local.last_event_seq}:observed={observed.last_event_seq}")
    ours = {o.order_id: o for o in local.orders if o.status in {"OPEN", "PARTIALLY_FILLED"}}
    theirs = {o.order_id: o for o in observed.open_orders}
    for oid in sorted(set(ours) - set(theirs)):
        issues.append(f"missing_observed_order:{oid}")
    for oid in sorted(set(theirs) - set(ours)):
        issues.append(f"unexpected_observed_order:{oid}")
    for oid in sorted(set(ours) & set(theirs)):
        a, b = ours[oid], theirs[oid]
        if a.side != b.side:
            issues.append(f"order_side_mismatch:{oid}")
        if abs(a.remaining_qty - b.remaining_qty) > tol:
            issues.append(f"order_remaining_mismatch:{oid}:local={a.remaining_qty}:observed={b.remaining_qty}")
    return ReconciliationReport(not issues, tuple(issues), local.last_event_seq, observed.last_event_seq)


def save_checkpoint(path: str | Path, state: PaperState) -> str:
    """Atomically persist a checksummed local JSON checkpoint."""
    target = Path(path)
    if not target.parent.exists() or not target.parent.is_dir():
        raise ResilienceError("checkpoint parent directory must already exist")
    payload = state.to_payload()
    digest = _digest_payload(payload)
    envelope = {"schema_version": CHECKPOINT_SCHEMA_VERSION, "state_sha256": digest, "state": payload}
    data = (_canonical_json(envelope) + "\n").encode("utf-8")
    tmp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(tmp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ResilienceError(f"cannot save checkpoint atomically: {exc}") from exc
    return digest


def load_checkpoint(path: str | Path, *, expected_source_sha256: str, expected_config_sha256: str) -> PaperState:
    """Load only intact state whose source/config hashes match this replay."""
    source_hash = _sha(expected_source_sha256, "expected_source_sha256")
    config_hash = _sha(expected_config_sha256, "expected_config_sha256")
    try:
        envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResilienceError(f"cannot read checkpoint: {exc}") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"schema_version", "state_sha256", "state"}:
        raise ResilienceError("checkpoint envelope fields do not match schema")
    if envelope["schema_version"] != CHECKPOINT_SCHEMA_VERSION:
        raise ResilienceError("unsupported checkpoint envelope schema_version")
    if not isinstance(envelope["state"], dict):
        raise ResilienceError("checkpoint state must be an object")
    if envelope["state_sha256"] != _digest_payload(envelope["state"]):
        raise ResilienceError("checkpoint checksum mismatch")
    state = PaperState.from_payload(envelope["state"])
    if state.source_sha256 != source_hash:
        raise ResilienceError("checkpoint source hash mismatch")
    if state.config_sha256 != config_hash:
        raise ResilienceError("checkpoint config hash mismatch")
    return state
