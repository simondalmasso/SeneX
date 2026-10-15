"""Paper-only event journal and recovery coordinator; intentionally not runtime-wired."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .contracts import as_decimal, normalize_timestamp
from .execution_resilience import (
    ExecutionSnapshot, FillApplication, FillEvent, PaperState, ReconciliationReport,
    ResilienceError, SnapshotOrder, apply_fill as _apply_fill,
    register_order as _register_order,
)

SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64
_SUPPORTED_EVENTS = {
    "ORDER_REGISTERED", "FILL_APPLIED", "ORDER_CANCELLED", "ORDER_EXPIRED",
    "SNAPSHOT_RECONCILED", "DIVERGENCE_RESOLVED",
}
_TERMINAL = {"CANCELLED", "EXPIRED"}


class ContractError(ValueError):
    """Paper contract, journal integrity, risk, or recovery violation."""


def _decimal(value: object, name: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    try:
        return as_decimal(value, name, positive=positive, non_negative=non_negative)
    except (ValueError, TypeError) as exc:
        raise ContractError(str(exc)) from exc


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class RiskLimits:
    """Explicit paper limits; every fill is checked before durable commit."""
    max_inventory: Decimal
    max_fill_qty: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_inventory", _decimal(self.max_inventory, "max_inventory", positive=True))
        object.__setattr__(self, "max_fill_qty", _decimal(self.max_fill_qty, "max_fill_qty", positive=True))

    def to_payload(self) -> dict[str, str]:
        return {"max_inventory": str(self.max_inventory), "max_fill_qty": str(self.max_fill_qty)}


class PaperExecutionEngine:
    """Append-first paper reducer with hash-chained journal and checkpoint replay.

    Contract: one writer per journal path; no external calls; no order routing.
    A journal record is fsynced before in-memory state is advanced. A failed
    durability operation freezes this instance. Restore verifies the entire
    hash chain and reconstructs state from genesis to validate the checkpoint,
    then replays records after the checkpoint's journal sequence.
    """

    def __init__(
        self, *, initial_state: PaperState, journal_path: str | Path,
        risk_limits: RiskLimits, _allow_existing: bool = False,
    ) -> None:
        if not isinstance(initial_state, PaperState):
            raise ContractError("initial_state must be PaperState")
        if not isinstance(risk_limits, RiskLimits):
            raise ContractError("risk_limits must be RiskLimits")
        self._state = initial_state
        self.journal_path = Path(journal_path)
        if not self.journal_path.parent.exists() or not self.journal_path.parent.is_dir():
            raise ContractError("journal parent directory must already exist")
        try:
            if not self.journal_path.exists():
                fd = os.open(str(self.journal_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            elif self.journal_path.stat().st_size and not _allow_existing:
                raise ContractError("journal is not empty; use recover() instead of a fresh engine")
        except OSError as exc:
            raise ContractError(f"cannot initialize journal: {exc}") from exc
        self._writer_lock_path = Path(str(self.journal_path) + ".writer.lock")
        try:
            if not self._writer_lock_path.exists():
                lock_fd = os.open(str(self._writer_lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    os.write(lock_fd, b"\0")
                    os.fsync(lock_fd)
                finally:
                    os.close(lock_fd)
            elif self._writer_lock_path.stat().st_size < 1:
                raise ContractError("journal writer lock file is invalid")
        except FileExistsError:
            if not self._writer_lock_path.exists() or self._writer_lock_path.stat().st_size < 1:
                raise ContractError("cannot initialize journal writer lock")
        except OSError as exc:
            raise ContractError(f"cannot initialize journal writer lock: {exc}") from exc
        self.risk_limits = risk_limits
        self._journal_size = self.journal_path.stat().st_size
        self._terminal_statuses: dict[str, str] = {}
        self._journal_seq = 0
        self._journal_head = GENESIS_HASH
        self._last_timestamp: Optional[str] = None
        self._blocked_reason: Optional[str] = None
        self._discrepancies: tuple[str, ...] = ()
        self._events_by_id: dict[str, dict[str, Any]] = {}
        self._fill_payloads: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @property
    def state(self) -> PaperState:
        return self._state

    @property
    def journal_seq(self) -> int:
        return self._journal_seq

    @property
    def journal_head(self) -> str:
        return self._journal_head

    @property
    def blocked(self) -> bool:
        return self._blocked_reason is not None

    @property
    def blocked_reason(self) -> Optional[str]:
        return self._blocked_reason

    @property
    def discrepancies(self) -> tuple[str, ...]:
        return self._discrepancies

    def status(self, order_id: str) -> str:
        order = self._find_order(order_id)
        return self._terminal_statuses.get(order.order_id, order.status)

    def remaining_qty(self, order_id: str) -> Decimal:
        return self._find_order(order_id).remaining_qty

    def _find_order(self, order_id: str):
        key = _nonempty(order_id, "order_id")
        found = [order for order in self._state.orders if order.order_id == key]
        if len(found) != 1:
            raise ContractError(f"unknown order_id: {key}")
        return found[0]

    def _check_unblocked(self) -> None:
        if self.blocked:
            raise ContractError(f"engine is blocked pending explicit divergence resolution: {self._blocked_reason}")

    def _normalize_event_timestamp(self, timestamp: str) -> str:
        try:
            return normalize_timestamp(timestamp)
        except ValueError as exc:
            raise ContractError(f"invalid event timestamp: {exc}") from exc

    def _check_event_time(self, timestamp: str) -> str:
        normalized = self._normalize_event_timestamp(timestamp)
        if self._last_timestamp is not None and normalized < self._last_timestamp:
            raise ContractError("event timestamp must not move backwards")
        return normalized

    def _existing_event(self, event_id: str, event_type: str, timestamp: str,
                        payload: dict[str, Any], *, ignore_payload_keys: tuple[str, ...] = ()) -> Optional[dict[str, Any]]:
        prior = self._events_by_id.get(event_id)
        if prior is None:
            return None
        prior_payload = {k: v for k, v in prior["payload"].items() if k not in ignore_payload_keys}
        current_payload = {k: v for k, v in payload.items() if k not in ignore_payload_keys}
        if prior["event_type"] != event_type or prior["timestamp"] != timestamp or _canonical(prior_payload) != _canonical(current_payload):
            raise ContractError(f"event_id conflict: {event_id} was reused with a different payload")
        return prior

    @contextmanager
    def _writer_lock(self):
        """Serialize appenders across engine instances/processes for this journal path."""
        try:
            fd = os.open(str(self._writer_lock_path), os.O_RDWR)
        except OSError as exc:
            raise ContractError(f"cannot open journal writer lock: {exc}") from exc
        locked = False
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    locked = True
                except OSError as exc:
                    raise ContractError("journal already has an active writer") from exc
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError as exc:
                    raise ContractError("journal already has an active writer") from exc
            yield
        finally:
            if locked:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
            else:
                os.close(fd)

    def _append_record(self, record: dict[str, Any]) -> None:
        data = (_canonical(record) + "\n").encode("utf-8")
        try:
            with self._writer_lock():
                try:
                    current_size = self.journal_path.stat().st_size
                except OSError as exc:
                    self._blocked_reason = f"journal durability failure: {exc}"
                    raise ContractError(f"journal durability failure; state was not advanced: {exc}") from exc
                if current_size != self._journal_size:
                    self._blocked_reason = "journal writer conflict: durable journal changed since this engine loaded"
                    raise ContractError("journal writer conflict/stale head; reload via recover() before continuing")
                fd = None
                try:
                    fd = os.open(str(self.journal_path), os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0))
                    offset = 0
                    while offset < len(data):
                        written = os.write(fd, data[offset:])
                        if written <= 0:
                            raise OSError("short write while appending journal record")
                        offset += written
                    os.fsync(fd)
                except OSError as exc:
                    self._blocked_reason = f"journal durability failure: {exc}"
                    raise ContractError(f"journal durability failure; in-memory state was not advanced: {exc}") from exc
                finally:
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError as exc:
                            if self._blocked_reason is None:
                                self._blocked_reason = f"journal durability failure on close: {exc}"
                                raise ContractError(f"journal durability failure; in-memory state was not advanced: {exc}") from exc
                self._journal_size += len(data)
        except ContractError:
            raise
        except OSError as exc:
            self._blocked_reason = f"journal durability failure: {exc}"
            raise ContractError(f"journal durability failure; in-memory state was not advanced: {exc}") from exc

    def _record(self, *, event_id: str, event_type: str, timestamp: str,
                payload: dict[str, Any], allow_while_blocked: bool = False) -> tuple[bool, Any]:
        event_id = _nonempty(event_id, "event_id")
        timestamp = self._normalize_event_timestamp(timestamp)
        if event_type not in _SUPPORTED_EVENTS:
            raise ContractError(f"unsupported event_type: {event_type}")
        prior = self._existing_event(event_id, event_type, timestamp, payload,
                                     ignore_payload_keys=("fill_seq",) if event_type == "FILL_APPLIED" else ())
        if prior is not None:
            if event_type == "FILL_APPLIED":
                order = self._find_order(payload["order_id"])
                return False, FillApplication(False, True, self.status(order.order_id), order.remaining_qty)
            if event_type == "SNAPSHOT_RECONCILED":
                return False, self._make_reconciliation_report(payload)
            return False, None
        if self.blocked and not allow_while_blocked:
            self._check_unblocked()
        timestamp = self._check_event_time(timestamp)
        base = {
            "journal_seq": self._journal_seq + 1,
            "event_id": event_id,
            "event_type": event_type,
            "timestamp": timestamp,
            "payload": payload,
            "prev_sha256": self._journal_head,
        }
        record = {**base, "event_sha256": _sha(base)}
        candidate = self._reduce(record)
        self._append_record(record)
        self._state = candidate["state"]
        self._terminal_statuses = candidate["terminal_statuses"]
        self._blocked_reason = candidate["blocked_reason"]
        self._discrepancies = candidate["discrepancies"]
        self._last_timestamp = timestamp
        self._journal_seq = record["journal_seq"]
        self._journal_head = record["event_sha256"]
        self._events_by_id[event_id] = record
        if event_type == "FILL_APPLIED":
            self._fill_payloads[payload["fill_id"]] = {k: v for k, v in payload.items() if k != "fill_seq"}
        return True, candidate.get("result")

    def register_order(self, *, event_id: str, timestamp: str, order_id: str,
                       client_order_id: str, side: str, quantity: object) -> bool:
        with self._lock:
            ts = self._normalize_event_timestamp(timestamp)
            oid = _nonempty(order_id, "order_id")
            cid = _nonempty(client_order_id, "client_order_id")
            side = str(side).strip().lower()
            if side not in {"buy", "sell"}:
                raise ContractError("side must be buy or sell")
            qty = _decimal(quantity, "quantity", positive=True)
            payload = {"order_id": oid, "client_order_id": cid, "side": side, "quantity": str(qty)}
            recorded, _ = self._record(event_id=event_id, event_type="ORDER_REGISTERED", timestamp=ts, payload=payload)
            return recorded

    def apply_fill(self, *, event_id: str, fill_id: str, order_id: str, side: str,
                   quantity: object, price: object, fee: object = "0", spread_cost: object = "0",
                   slippage_cost: object = "0", timestamp: str) -> FillApplication:
        with self._lock:
            ts = self._normalize_event_timestamp(timestamp)
            base_payload = {
                "fill_id": _nonempty(fill_id, "fill_id"),
                "order_id": _nonempty(order_id, "order_id"),
                "side": str(side).strip().lower(),
                "quantity": str(_decimal(quantity, "quantity", positive=True)),
                "price": str(_decimal(price, "price", positive=True)),
                "fee": str(_decimal(fee, "fee", non_negative=True)),
                "spread_cost": str(_decimal(spread_cost, "spread_cost", non_negative=True)),
                "slippage_cost": str(_decimal(slippage_cost, "slippage_cost", non_negative=True)),
                "timestamp": ts,
            }
            event_id = _nonempty(event_id, "event_id")
            prior = self._events_by_id.get(event_id)
            if prior is not None:
                existing = self._existing_event(event_id, "FILL_APPLIED", ts, base_payload, ignore_payload_keys=("fill_seq",))
                if existing is not None:
                    order = self._find_order(base_payload["order_id"])
                    return FillApplication(False, True, self.status(order.order_id), order.remaining_qty)
            prior_fill = self._fill_payloads.get(base_payload["fill_id"])
            if prior_fill is not None:
                if _canonical(prior_fill) != _canonical(base_payload):
                    raise ContractError("fill_id conflict: ID reused with a different payload")
                order = self._find_order(base_payload["order_id"])
                return FillApplication(False, True, self.status(order.order_id), order.remaining_qty)
            self._check_unblocked()
            if base_payload["side"] not in {"buy", "sell"}:
                raise ContractError("fill side must be buy or sell")
            if _decimal(base_payload["quantity"], "quantity", positive=True) > self.risk_limits.max_fill_qty:
                raise ContractError("fill quantity exceeds max_fill_qty risk limit")
            order = self._find_order(base_payload["order_id"])
            if self.status(order.order_id) in _TERMINAL:
                raise ContractError("fill received for terminal cancelled/expired order")
            if base_payload["side"] == "buy" and self._state.inventory_qty + _decimal(base_payload["quantity"], "quantity", positive=True) > self.risk_limits.max_inventory:
                raise ContractError("buy fill would exceed max_inventory risk limit")
            fill_seq = self._state.last_event_seq + 1
            payload = {**base_payload, "fill_seq": fill_seq}
            _, result = self._record(event_id=event_id, event_type="FILL_APPLIED", timestamp=ts, payload=payload)
            if result is None:
                # The event was committed; derive its current application result.
                order = self._find_order(base_payload["order_id"])
                return FillApplication(True, False, self.status(order.order_id), order.remaining_qty)
            return result

    def _terminal_transition(self, *, event_id: str, timestamp: str, order_id: str,
                             reason: str, event_type: str) -> bool:
        with self._lock:
            ts = self._normalize_event_timestamp(timestamp)
            order = self._find_order(order_id)
            payload = {
                "order_id": order.order_id,
                "remaining_qty": str(order.remaining_qty),
                "reason": _nonempty(reason, "reason"),
            }
            event_key = _nonempty(event_id, "event_id")
            if self._existing_event(event_key, event_type, ts, payload) is not None:
                return False
            self._check_unblocked()
            if self.status(order.order_id) in _TERMINAL or order.status == "FILLED":
                raise ContractError("cannot cancel/expire an already terminal order")
            recorded, _ = self._record(event_id=event_key, event_type=event_type, timestamp=ts, payload=payload)
            return recorded

    def cancel_order(self, *, event_id: str, timestamp: str, order_id: str, reason: str) -> bool:
        return self._terminal_transition(event_id=event_id, timestamp=timestamp, order_id=order_id,
                                         reason=reason, event_type="ORDER_CANCELLED")

    def expire_order(self, *, event_id: str, timestamp: str, order_id: str, reason: str) -> bool:
        return self._terminal_transition(event_id=event_id, timestamp=timestamp, order_id=order_id,
                                         reason=reason, event_type="ORDER_EXPIRED")

    def reconcile(self, observed: ExecutionSnapshot, *, event_id: str, timestamp: str,
                  tolerance: object = "0") -> ReconciliationReport:
        with self._lock:
            ts = self._normalize_event_timestamp(timestamp)
            if not isinstance(observed, ExecutionSnapshot):
                raise ContractError("observed must be ExecutionSnapshot")
            tol = _decimal(tolerance, "tolerance", non_negative=True)
            payload = {
                "cash": str(observed.cash),
                "inventory_qty": str(observed.inventory_qty),
                "open_orders": [
                    {"order_id": order.order_id, "side": order.side, "remaining_qty": str(order.remaining_qty)}
                    for order in sorted(observed.open_orders, key=lambda item: item.order_id)
                ],
                "last_event_seq": observed.last_event_seq,
                "tolerance": str(tol),
            }
            _, result = self._record(event_id=event_id, event_type="SNAPSHOT_RECONCILED", timestamp=ts, payload=payload)
            return result

    def resolve_divergence(self, *, event_id: str, timestamp: str,
                           approval_ref: str, reason: str) -> bool:
        with self._lock:
            ts = self._normalize_event_timestamp(timestamp)
            payload = {
                "approval_ref": _nonempty(approval_ref, "approval_ref"),
                "reason": _nonempty(reason, "reason"),
                "action": "retain_local_paper_state",
                "prior_discrepancies": list(self._discrepancies),
            }
            event_key = _nonempty(event_id, "event_id")
            if self._existing_event(event_key, "DIVERGENCE_RESOLVED", ts, payload) is not None:
                return False
            if not self.blocked:
                raise ContractError("cannot resolve divergence when engine is not blocked")
            recorded, _ = self._record(event_id=event_key, event_type="DIVERGENCE_RESOLVED",
                                       timestamp=ts, payload=payload, allow_while_blocked=True)
            return recorded

    def _make_reconciliation_report(self, payload: dict[str, Any]) -> ReconciliationReport:
        tol = _decimal(payload["tolerance"], "tolerance", non_negative=True)
        issues: list[str] = []
        cash = _decimal(payload["cash"], "snapshot cash", non_negative=True)
        inventory = _decimal(payload["inventory_qty"], "snapshot inventory_qty", non_negative=True)
        last_seq = payload["last_event_seq"]
        if isinstance(last_seq, bool) or not isinstance(last_seq, int) or last_seq < 0:
            raise ContractError("snapshot last_event_seq must be a non-negative integer")
        if abs(self._state.cash - cash) > tol:
            issues.append(f"cash_mismatch:local={self._state.cash}:observed={cash}")
        if abs(self._state.inventory_qty - inventory) > tol:
            issues.append(f"inventory_mismatch:local={self._state.inventory_qty}:observed={inventory}")
        if self._state.last_event_seq != last_seq:
            issues.append(f"event_sequence_mismatch:local={self._state.last_event_seq}:observed={last_seq}")
        ours = {
            order.order_id: order for order in self._state.orders
            if order.status in {"OPEN", "PARTIALLY_FILLED"} and order.order_id not in self._terminal_statuses
        }
        raw_orders = payload["open_orders"]
        theirs: dict[str, SnapshotOrder] = {}
        for item in raw_orders:
            snap = SnapshotOrder(order_id=item["order_id"], side=item["side"], remaining_qty=item["remaining_qty"])
            if snap.order_id in theirs:
                raise ContractError("snapshot contains duplicate open order IDs")
            theirs[snap.order_id] = snap
        for oid in sorted(set(ours) - set(theirs)):
            issues.append(f"missing_observed_order:{oid}")
        for oid in sorted(set(theirs) - set(ours)):
            issues.append(f"unexpected_observed_order:{oid}")
        for oid in sorted(set(ours) & set(theirs)):
            if ours[oid].side != theirs[oid].side:
                issues.append(f"order_side_mismatch:{oid}")
            if abs(ours[oid].remaining_qty - theirs[oid].remaining_qty) > tol:
                issues.append(f"order_remaining_mismatch:{oid}:local={ours[oid].remaining_qty}:observed={theirs[oid].remaining_qty}")
        return ReconciliationReport(not issues, tuple(issues), self._state.last_event_seq, last_seq)

    def _reduce(self, record: dict[str, Any]) -> dict[str, Any]:
        etype, payload, timestamp = record["event_type"], record["payload"], record["timestamp"]
        if not isinstance(payload, dict):
            raise ContractError("journal event payload must be an object")
        if self._last_timestamp is not None and timestamp < self._last_timestamp:
            raise ContractError("journal event timestamp moved backwards")
        state = self._state
        statuses = dict(self._terminal_statuses)
        blocked_reason = self._blocked_reason
        discrepancies = self._discrepancies
        result = None
        if blocked_reason and etype != "DIVERGENCE_RESOLVED":
            raise ContractError("journal contains a state-changing event while divergence block is active")
        if etype == "ORDER_REGISTERED":
            required = {"order_id", "client_order_id", "side", "quantity"}
            if set(payload) != required:
                raise ContractError("ORDER_REGISTERED payload fields do not match schema")
            if any(o.order_id == payload["order_id"] or o.client_order_id == payload["client_order_id"] for o in state.orders):
                raise ContractError("order registration ID conflict")
            state = _register_order(state, order_id=payload["order_id"], client_order_id=payload["client_order_id"],
                                    side=payload["side"], quantity=payload["quantity"])
        elif etype == "FILL_APPLIED":
            required = {"fill_id", "order_id", "side", "quantity", "price", "fee", "spread_cost", "slippage_cost", "timestamp", "fill_seq"}
            if set(payload) != required:
                raise ContractError("FILL_APPLIED payload fields do not match schema")
            order = next((o for o in state.orders if o.order_id == payload["order_id"]), None)
            if order is None:
                raise ContractError("fill references an unknown order")
            if payload["order_id"] in statuses:
                raise ContractError("fill references terminal cancelled/expired order")
            qty = _decimal(payload["quantity"], "quantity", positive=True)
            if qty > self.risk_limits.max_fill_qty:
                raise ContractError("fill quantity exceeds max_fill_qty risk limit")
            if payload["side"] == "buy" and state.inventory_qty + qty > self.risk_limits.max_inventory:
                raise ContractError("buy fill would exceed max_inventory risk limit")
            if payload["fill_seq"] != state.last_event_seq + 1:
                raise ContractError("journal fill sequence is not the next state sequence")
            if payload["timestamp"] != timestamp:
                raise ContractError("fill payload timestamp must match journal envelope timestamp")
            event = FillEvent(
                fill_id=payload["fill_id"], order_id=payload["order_id"], side=payload["side"],
                quantity=payload["quantity"], price=payload["price"], fee=payload["fee"],
                spread_cost=payload["spread_cost"], slippage_cost=payload["slippage_cost"],
                timestamp=payload["timestamp"], event_seq=payload["fill_seq"],
            )
            try:
                state, result = _apply_fill(state, event)
            except ResilienceError as exc:
                raise ContractError(f"fill validation failed: {exc}") from exc
            if not result.applied:
                raise ContractError("journal contains a duplicate fill record instead of a no-op command")
        elif etype in {"ORDER_CANCELLED", "ORDER_EXPIRED"}:
            if set(payload) != {"order_id", "remaining_qty", "reason"}:
                raise ContractError(f"{etype} payload fields do not match schema")
            order = next((o for o in state.orders if o.order_id == payload["order_id"]), None)
            if order is None:
                raise ContractError(f"{etype} references an unknown order")
            if order.status == "FILLED" or order.order_id in statuses:
                raise ContractError(f"{etype} references an already terminal order")
            if _decimal(payload["remaining_qty"], "remaining_qty", non_negative=True) != order.remaining_qty:
                raise ContractError(f"{etype} remaining quantity does not match state")
            if not _nonempty(payload["reason"], "reason"):
                raise ContractError(f"{etype} reason is required")
            statuses[order.order_id] = "CANCELLED" if etype == "ORDER_CANCELLED" else "EXPIRED"
        elif etype == "SNAPSHOT_RECONCILED":
            required = {"cash", "inventory_qty", "open_orders", "last_event_seq", "tolerance"}
            if set(payload) != required:
                raise ContractError("SNAPSHOT_RECONCILED payload fields do not match schema")
            result = self._make_reconciliation_report(payload)
            if not result.matched:
                discrepancies = result.discrepancies
                blocked_reason = f"reconciliation divergence at journal seq {record['journal_seq']}"
        elif etype == "DIVERGENCE_RESOLVED":
            required = {"approval_ref", "reason", "action", "prior_discrepancies"}
            if set(payload) != required or payload["action"] != "retain_local_paper_state":
                raise ContractError("DIVERGENCE_RESOLVED payload/action invalid")
            if not blocked_reason:
                raise ContractError("DIVERGENCE_RESOLVED without active divergence")
            _nonempty(payload["approval_ref"], "approval_ref")
            _nonempty(payload["reason"], "reason")
            if payload["prior_discrepancies"] != list(discrepancies):
                raise ContractError("divergence resolution does not cite current discrepancies")
            blocked_reason = None
        else:
            raise ContractError(f"unsupported event type {etype}")
        return {
            "state": state,
            "terminal_statuses": statuses,
            "blocked_reason": blocked_reason,
            "discrepancies": discrepancies,
            "result": result,
        }

    def _engine_state_payload(self) -> dict[str, Any]:
        return {
            "state": self._state.to_payload(),
            "terminal_statuses": [{"order_id": oid, "status": status} for oid, status in sorted(self._terminal_statuses.items())],
            "journal_seq": self._journal_seq,
            "journal_head": self._journal_head,
            "last_timestamp": self._last_timestamp,
            "blocked_reason": self._blocked_reason,
            "discrepancies": list(self._discrepancies),
            "risk_limits": self.risk_limits.to_payload(),
        }

    def save_checkpoint(self, path: str | Path) -> str:
        with self._lock:
            if self._blocked_reason and self._blocked_reason.startswith("journal "):
                raise ContractError("cannot checkpoint while journal integrity/durability is uncertain; restart through fail-closed recovery")
            target = Path(path)
            if target.resolve() == self.journal_path.resolve():
                raise ContractError("checkpoint path must differ from journal path")
            if not target.parent.exists() or not target.parent.is_dir():
                raise ContractError("checkpoint parent directory must already exist")
            payload = self._engine_state_payload()
            envelope = {"schema_version": SCHEMA_VERSION, "state_sha256": _sha(payload), "engine_state": payload}
            data = (_canonical(envelope) + "\n").encode("utf-8")
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
                raise ContractError(f"cannot save checkpoint atomically: {exc}") from exc
            return envelope["state_sha256"]

    @classmethod
    def _read_journal(cls, path: Path) -> list[dict[str, Any]]:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ContractError(f"cannot read journal: {exc}") from exc
        if data and not data.endswith(b"\n"):
            raise ContractError("journal has an incomplete/truncated trailing record; recovery fails closed")
        try:
            lines = data.splitlines()
            records = [json.loads(line.decode("utf-8")) for line in lines]
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(f"journal contains invalid JSON/UTF-8: {exc}") from exc
        previous = GENESIS_HASH
        ids: set[str] = set()
        for expected_seq, record in enumerate(records, start=1):
            required = {"journal_seq", "event_id", "event_type", "timestamp", "payload", "prev_sha256", "event_sha256"}
            if not isinstance(record, dict) or set(record) != required:
                raise ContractError("journal record fields do not match schema")
            if record["journal_seq"] != expected_seq:
                raise ContractError("journal sequence gap or reorder")
            _nonempty(record["event_id"], "event_id")
            if record["event_id"] in ids:
                raise ContractError("journal contains duplicate event_id")
            ids.add(record["event_id"])
            if record["event_type"] not in _SUPPORTED_EVENTS:
                raise ContractError("journal contains unknown event type")
            try:
                normalized = normalize_timestamp(record["timestamp"])
            except ValueError as exc:
                raise ContractError(f"invalid journal timestamp: {exc}") from exc
            if normalized != record["timestamp"]:
                raise ContractError("journal timestamp is not canonically normalized")
            if record["prev_sha256"] != previous:
                raise ContractError("journal hash chain predecessor mismatch")
            base = {k: record[k] for k in required if k != "event_sha256"}
            actual = _sha(base)
            if record["event_sha256"] != actual:
                raise ContractError("journal event hash mismatch")
            previous = actual
        return records

    @classmethod
    def recover(cls, *, checkpoint_path: str | Path, journal_path: str | Path,
                expected_source_sha256: str, expected_config_sha256: str,
                risk_limits: RiskLimits) -> "PaperExecutionEngine":
        checkpoint = Path(checkpoint_path)
        logpath = Path(journal_path)
        if checkpoint.resolve() == logpath.resolve():
            raise ContractError("checkpoint path must differ from journal path")
        try:
            envelope = json.loads(checkpoint.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(f"cannot read checkpoint: {exc}") from exc
        if not isinstance(envelope, dict) or set(envelope) != {"schema_version", "state_sha256", "engine_state"}:
            raise ContractError("checkpoint envelope fields do not match schema")
        if envelope["schema_version"] != SCHEMA_VERSION:
            raise ContractError("unsupported paper engine checkpoint schema")
        cp = envelope["engine_state"]
        if not isinstance(cp, dict) or set(cp) != {"state", "terminal_statuses", "journal_seq", "journal_head", "last_timestamp", "blocked_reason", "discrepancies", "risk_limits"}:
            raise ContractError("checkpoint engine-state fields do not match schema")
        if envelope["state_sha256"] != _sha(cp):
            raise ContractError("checkpoint checksum mismatch")
        source = _nonempty(expected_source_sha256, "expected_source_sha256").lower()
        config = _nonempty(expected_config_sha256, "expected_config_sha256").lower()
        try:
            checkpoint_state = PaperState.from_payload(cp["state"])
        except (ValueError, TypeError, KeyError) as exc:
            raise ContractError(f"invalid checkpoint PaperState: {exc}") from exc
        if checkpoint_state.source_sha256 != source:
            raise ContractError("checkpoint source hash mismatch")
        if checkpoint_state.config_sha256 != config:
            raise ContractError("checkpoint config hash mismatch")
        if cp["risk_limits"] != risk_limits.to_payload():
            raise ContractError("checkpoint risk limits mismatch")
        if isinstance(cp["journal_seq"], bool) or not isinstance(cp["journal_seq"], int) or cp["journal_seq"] < 0:
            raise ContractError("checkpoint journal_seq invalid")
        records = cls._read_journal(logpath)
        cp_seq = cp["journal_seq"]
        if cp_seq > len(records):
            raise ContractError("checkpoint points beyond the durable journal")
        expected_head = GENESIS_HASH if cp_seq == 0 else records[cp_seq - 1]["event_sha256"]
        if cp["journal_head"] != expected_head:
            raise ContractError("checkpoint/journal anchor mismatch")
        starting = PaperState.initial(starting_cash=checkpoint_state.starting_cash,
                                      source_sha256=source, config_sha256=config)
        engine = cls(initial_state=starting, journal_path=logpath, risk_limits=risk_limits, _allow_existing=True)
        expected_cp_payload = cp
        if cp_seq == 0 and _canonical(engine._engine_state_payload()) != _canonical(expected_cp_payload):
            raise ContractError("genesis checkpoint state does not match initial state")
        for idx, record in enumerate(records, start=1):
            # The event record chain was checked as a whole before reducer replay.
            timestamp = record["timestamp"]
            engine._check_event_time(timestamp)
            candidate = engine._reduce(record)
            engine._state = candidate["state"]
            engine._terminal_statuses = candidate["terminal_statuses"]
            engine._blocked_reason = candidate["blocked_reason"]
            engine._discrepancies = candidate["discrepancies"]
            engine._last_timestamp = timestamp
            engine._journal_seq = record["journal_seq"]
            engine._journal_head = record["event_sha256"]
            engine._events_by_id[record["event_id"]] = record
            if record["event_type"] == "FILL_APPLIED":
                engine._fill_payloads[record["payload"]["fill_id"]] = {
                    k: v for k, v in record["payload"].items() if k != "fill_seq"
                }
            if idx == cp_seq:
                if _canonical(engine._engine_state_payload()) != _canonical(expected_cp_payload):
                    raise ContractError("checkpoint state does not match replayed journal prefix")
        if cp_seq == 0 and _canonical(engine._engine_state_payload()) != _canonical(expected_cp_payload):
            raise ContractError("genesis checkpoint state does not match initial state")
        return engine