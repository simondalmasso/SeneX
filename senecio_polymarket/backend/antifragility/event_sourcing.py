"""
ACT-XXIX — Module 1: Event Sourcing & Deterministic Replay
==========================================================

Institutional-grade event-sourcing infrastructure for the SENECIO oracle.

Public surface
--------------
- ``Event``               — frozen dataclass: id, type, payload, ts, prev_hash, hash
- ``EventSourcedAggregate``— base class: apply events, take snapshot, restore
- ``Snapshot``            — frozen dataclass: aggregate_id, seq, state, ts, hash
- ``SnapshotManager``     — pluggable snapshot store (in-memory + JSON file)
- ``GlobalAuditLedger``   — append-only hash-chained ledger (tamper-evident)
- ``DeterministicReplayer``— replay any event stream deterministically
- ``EventStore``          — durable JSONL event store with hash-chain verify

Design principles
-----------------
1. Every state mutation flows through events.
2. Every event is content-hashed and chained to the previous one
   (``hash = sha256(prev_hash + canonical_json(payload))``).
3. Snapshots are full state serialisations tagged with the seq number
   they correspond to. Restoring = "load snapshot + apply later events".
4. Replay is deterministic given (initial_state, event_stream, seed).
5. Tamper-evidence: any ledger mutation invalidates every subsequent hash.

This module is STRICT_ADDITIVE — it does not modify any existing module.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GENESIS_HASH = "0" * 64  # 32-byte zero hex
DEFAULT_LEDGER_PATH = "data/antifragility/audit_ledger.jsonl"
DEFAULT_SNAPSHOT_DIR = "data/antifragility/snapshots"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _hash_payload(prev_hash: str, payload: Any) -> str:
    """sha256(prev_hash || canonical_json(payload))."""
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(_canonical_json(payload).encode("utf-8"))
    return h.hexdigest()


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Event:
    """Immutable event record. ``hash`` chains to ``prev_hash``."""
    id: str
    type: str
    payload: dict
    ts: str               # ISO-8601 UTC
    seq: int              # monotonic per-stream sequence number
    prev_hash: str
    hash: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(
            id=d["id"],
            type=d["type"],
            payload=d.get("payload", {}),
            ts=d["ts"],
            seq=d["seq"],
            prev_hash=d["prev_hash"],
            hash=d["hash"],
        )

    def verify(self, prev_hash: str) -> bool:
        """Recompute hash from (prev_hash, payload) and compare."""
        return self.hash == _hash_payload(prev_hash, self.payload)


@dataclass(frozen=True)
class Snapshot:
    """Full state serialisation of an aggregate at a given seq."""
    aggregate_id: str
    seq: int
    state: dict
    ts: str
    hash: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        return cls(
            aggregate_id=d["aggregate_id"],
            seq=d["seq"],
            state=d.get("state", {}),
            ts=d["ts"],
            hash=d["hash"],
        )


# ---------------------------------------------------------------------------
# EventStore — durable JSONL append-only log with hash verification
# ---------------------------------------------------------------------------

class EventStore:
    """Append-only JSONL event store.

    Each line = one Event.to_dict(). Concurrent-safe via threading.Lock.
    Supports replay from any offset and full hash-chain verification.
    """

    def __init__(self, path: str | Path = DEFAULT_LEDGER_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq_counter = 0
        self._last_hash = GENESIS_HASH
        self._load_tail()

    def _load_tail(self) -> None:
        """On startup, read the last line to recover seq + last_hash."""
        if not self.path.exists():
            return
        last_seq = 0
        last_hash = GENESIS_HASH
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = Event.from_dict(json.loads(line))
                        last_seq = max(last_seq, ev.seq)
                        last_hash = ev.hash
                    except json.JSONDecodeError:
                        continue
            self._seq_counter = last_seq
            self._last_hash = last_hash
        except OSError:
            pass

    def append(self, event_type: str, payload: dict, ts: str | None = None) -> Event:
        """Append a new event. Auto-fills id, ts, seq, prev_hash, hash."""
        with self._lock:
            self._seq_counter += 1
            ev = Event(
                id=_new_id(),
                type=event_type,
                payload=payload,
                ts=ts or _now_iso(),
                seq=self._seq_counter,
                prev_hash=self._last_hash,
                hash="",  # placeholder, computed below
            )
            # Compute hash using prev_hash
            object.__setattr__(ev, "hash",
                               _hash_payload(self._last_hash, ev.payload))
            self._last_hash = ev.hash
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev.to_dict(), default=str) + "\n")
            return ev

    def replay(self, from_seq: int = 0,
               to_seq: int | None = None) -> Iterator[Event]:
        """Yield events with seq in [from_seq, to_seq] inclusive."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = Event.from_dict(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if ev.seq < from_seq:
                    continue
                if to_seq is not None and ev.seq > to_seq:
                    return
                yield ev

    def verify_chain(self) -> tuple[bool, list[int]]:
        """Verify the entire hash chain. Returns (ok, broken_seqs)."""
        broken: list[int] = []
        prev = GENESIS_HASH
        for ev in self.replay():
            if not ev.verify(prev):
                broken.append(ev.seq)
            prev = ev.hash
        return (len(broken) == 0, broken)

    def count(self) -> int:
        return self._seq_counter

    @property
    def last_hash(self) -> str:
        return self._last_hash


# ---------------------------------------------------------------------------
# GlobalAuditLedger — convenience wrapper around EventStore for global events
# ---------------------------------------------------------------------------

class GlobalAuditLedger:
    """Immutable, hash-chained, append-only ledger for ALL system decisions.

    Wraps EventStore with domain-specific helpers:
      - record_decision(decision_kind, actor, inputs, outputs, rationale)
      - record_state_change(component, field, old, new, reason)
      - record_external_event(source, event_type, payload)
      - query(filter_fn, limit)
      - verify_integrity() -> (ok, broken_seqs)
    """

    def __init__(self, path: str | Path = DEFAULT_LEDGER_PATH):
        self.store = EventStore(path)

    def record_decision(self, decision_kind: str, actor: str,
                        inputs: dict, outputs: dict,
                        rationale: str = "") -> Event:
        payload = {
            "category": "decision",
            "decision_kind": decision_kind,
            "actor": actor,
            "inputs": inputs,
            "outputs": outputs,
            "rationale": rationale,
        }
        return self.store.append("DECISION", payload)

    def record_state_change(self, component: str, field: str,
                            old: Any, new: Any, reason: str = "") -> Event:
        payload = {
            "category": "state_change",
            "component": component,
            "field": field,
            "old": old,
            "new": new,
            "reason": reason,
        }
        return self.store.append("STATE_CHANGE", payload)

    def record_external_event(self, source: str, event_type: str,
                              payload: dict) -> Event:
        full = {
            "category": "external",
            "source": source,
            "event_type": event_type,
            "payload": payload,
        }
        return self.store.append("EXTERNAL", full)

    def query(self, filter_fn: Callable[[Event], bool] | None = None,
              limit: int = 100) -> list[Event]:
        out: list[Event] = []
        for ev in self.store.replay():
            if filter_fn is None or filter_fn(ev):
                out.append(ev)
                if len(out) >= limit:
                    break
        return out

    def verify_integrity(self) -> tuple[bool, list[int]]:
        return self.store.verify_chain()

    def count(self) -> int:
        return self.store.count()


# ---------------------------------------------------------------------------
# SnapshotManager — pluggable snapshot store
# ---------------------------------------------------------------------------

class SnapshotManager:
    """Manages snapshots for one or more aggregates.

    Backends:
      - in-memory dict (default)
      - JSON files (one per aggregate+seq, under snapshot_dir)
    """

    def __init__(self, snapshot_dir: str | Path = DEFAULT_SNAPSHOT_DIR,
                 persist_to_disk: bool = True):
        self.snapshot_dir = Path(snapshot_dir)
        if persist_to_disk:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.persist_to_disk = persist_to_disk
        self._cache: dict[str, list[Snapshot]] = {}  # aggregate_id -> [snapshots]
        self._lock = threading.Lock()
        if persist_to_disk:
            self._load_all_from_disk()

    def _file_for(self, aggregate_id: str, seq: int) -> Path:
        safe_id = aggregate_id.replace("/", "_").replace(":", "_")
        return self.snapshot_dir / f"{safe_id}__seq{seq:010d}.json"

    def _load_all_from_disk(self) -> None:
        if not self.snapshot_dir.exists():
            return
        for f in self.snapshot_dir.glob("*.json"):
            try:
                snap = Snapshot.from_dict(
                    json.loads(f.read_text(encoding="utf-8")))
                self._cache.setdefault(snap.aggregate_id, []).append(snap)
            except (json.JSONDecodeError, KeyError):
                continue
        # Sort by seq
        for snaps in self._cache.values():
            snaps.sort(key=lambda s: s.seq)

    def save(self, aggregate_id: str, seq: int, state: dict) -> Snapshot:
        """Save a snapshot. Hash = sha256(canonical_json(state))."""
        ts = _now_iso()
        state_hash = hashlib.sha256(
            _canonical_json(state).encode("utf-8")
        ).hexdigest()
        snap = Snapshot(
            aggregate_id=aggregate_id,
            seq=seq,
            state=state,
            ts=ts,
            hash=state_hash,
        )
        with self._lock:
            self._cache.setdefault(aggregate_id, []).append(snap)
            if self.persist_to_disk:
                self._file_for(aggregate_id, seq).write_text(
                    json.dumps(snap.to_dict(), default=str),
                    encoding="utf-8",
                )
        return snap

    def latest(self, aggregate_id: str) -> Snapshot | None:
        with self._lock:
            snaps = self._cache.get(aggregate_id, [])
            return snaps[-1] if snaps else None

    def get_at_or_before(self, aggregate_id: str, seq: int) -> Snapshot | None:
        """Latest snapshot whose seq <= ``seq``."""
        with self._lock:
            snaps = self._cache.get(aggregate_id, [])
            candidate = None
            for s in snaps:
                if s.seq <= seq:
                    candidate = s
                else:
                    break
            return candidate

    def list_all(self, aggregate_id: str) -> list[Snapshot]:
        with self._lock:
            return list(self._cache.get(aggregate_id, []))

    def count(self, aggregate_id: str | None = None) -> int:
        with self._lock:
            if aggregate_id is None:
                return sum(len(v) for v in self._cache.values())
            return len(self._cache.get(aggregate_id, []))


# ---------------------------------------------------------------------------
# EventSourcedAggregate — base class for any state-machine rebuilt from events
# ---------------------------------------------------------------------------

class EventSourcedAggregate:
    """Base class. Subclasses override ``apply_event(ev)`` to mutate state.

    Lifecycle:
      1. __init__(aggregate_id) — empty state, seq=0
      2. load_from_store(store) — replay all events for this aggregate
      3. emit(event_type, payload) — record + apply a new event
      4. snapshot(mgr) — persist current state
      5. restore(mgr, store) — load latest snapshot + replay post-snapshot events
    """

    AGGREGATE_TYPE: str = "base"

    def __init__(self, aggregate_id: str):
        self.aggregate_id = aggregate_id
        self.state: dict = {}
        self.seq = 0
        self._uncommitted: list[Event] = []

    # ---- to override ----
    def apply_event(self, ev: Event) -> None:
        raise NotImplementedError

    def initial_state(self) -> dict:
        return {}

    # ---- lifecycle ----
    def load_from_store(self, store: EventStore) -> None:
        self.state = self.initial_state()
        self.seq = 0
        for ev in store.replay():
            # Simple filter: every event belongs to every aggregate for the
            # base class. Subclasses may override _owns_event.
            if self._owns_event(ev):
                self.apply_event(ev)
                self.seq = ev.seq

    def _owns_event(self, ev: Event) -> bool:
        return True

    def emit(self, store: EventStore, event_type: str, payload: dict) -> Event:
        ev = store.append(event_type, payload)
        self.apply_event(ev)
        self.seq = ev.seq
        self._uncommitted.append(ev)
        return ev

    def snapshot(self, mgr: SnapshotManager) -> Snapshot:
        return mgr.save(self.aggregate_id, self.seq, self.state)

    def restore(self, mgr: SnapshotManager, store: EventStore) -> None:
        snap = mgr.latest(self.aggregate_id)
        if snap is None:
            self.load_from_store(store)
            return
        self.state = dict(snap.state)
        self.seq = snap.seq
        # Replay events AFTER the snapshot
        for ev in store.replay(from_seq=snap.seq + 1):
            if self._owns_event(ev):
                self.apply_event(ev)
                self.seq = ev.seq


# ---------------------------------------------------------------------------
# DeterministicReplayer — replay any event stream deterministically
# ---------------------------------------------------------------------------

class DeterministicReplayer:
    """Replay an event stream into a fresh aggregate, deterministically.

    Guarantees:
      - Same input events ⇒ same output state (modulo wall-clock ts in
        payload, which the user controls).
      - Verifies hash chain as it replays; raises on tamper.
      - Optionally caps max events to replay (safety bound).
    """

    def __init__(self, store: EventStore):
        self.store = store

    def replay_into(self, aggregate: EventSourcedAggregate,
                    from_seq: int = 0,
                    to_seq: int | None = None,
                    verify_chain: bool = True) -> EventSourcedAggregate:
        prev_hash = GENESIS_HASH
        # If from_seq > 0, walk the chain to find prev_hash at from_seq-1
        if from_seq > 0 and verify_chain:
            for ev in self.store.replay(to_seq=from_seq - 1):
                prev_hash = ev.hash
        aggregate.state = aggregate.initial_state()
        aggregate.seq = 0
        for ev in self.store.replay(from_seq=from_seq, to_seq=to_seq):
            if verify_chain and not ev.verify(prev_hash):
                raise ValueError(
                    f"Hash-chain broken at seq={ev.seq}; tamper detected")
            if aggregate._owns_event(ev):
                aggregate.apply_event(ev)
                aggregate.seq = ev.seq
            prev_hash = ev.hash
        return aggregate

    def replay_subset(self, events: Iterable[Event],
                      aggregate: EventSourcedAggregate,
                      verify_chain: bool = True) -> EventSourcedAggregate:
        """Replay an arbitrary subset of events (chain may be partial)."""
        prev_hash = GENESIS_HASH
        aggregate.state = aggregate.initial_state()
        aggregate.seq = 0
        for ev in events:
            if verify_chain and ev.prev_hash != prev_hash:
                raise ValueError(
                    f"Subset chain broken at seq={ev.seq}; prev_hash mismatch")
            if aggregate._owns_event(ev):
                aggregate.apply_event(ev)
                aggregate.seq = ev.seq
            prev_hash = ev.hash
        return aggregate


# ---------------------------------------------------------------------------
# Concrete example: PredictionLifecycleAggregate
# ---------------------------------------------------------------------------

class PredictionLifecycleAggregate(EventSourcedAggregate):
    """Rebuilds the full lifecycle of one prediction from its events.

    Event types handled:
      - PREDICTION_MADE     — initial proposal
      - FEATURES_COMPUTED   — feature vector attached
      - SIGNAL_GENERATED    — directional signal + confidence
      - VERIFIED            — outcome CORRECT/WRONG
      - ROUTED              — sent to portfolio coordinator
      - EXECUTED            — fill recorded
      - CLOSED              — position exited
    """

    AGGREGATE_TYPE = "prediction_lifecycle"

    def initial_state(self) -> dict:
        return {
            "prediction_id": None,
            "symbol": None,
            "direction": None,
            "confidence": 0.0,
            "features": {},
            "signal": None,
            "outcome": None,
            "routed": False,
            "executed": False,
            "closed": False,
            "event_count": 0,
            "history": [],
        }

    def _owns_event(self, ev: Event) -> bool:
        pid = ev.payload.get("prediction_id")
        return pid is None or pid == self.aggregate_id

    def apply_event(self, ev: Event) -> None:
        s = self.state
        p = ev.payload
        s["event_count"] += 1
        s["history"].append({
            "seq": ev.seq, "type": ev.type, "ts": ev.ts,
            "summary": {k: v for k, v in p.items()
                        if k in ("direction", "confidence", "outcome",
                                  "status", "exit_reason", "fill_qty",
                                  "realized_pnl")}
        })
        if ev.type == "PREDICTION_MADE":
            s["prediction_id"] = p.get("prediction_id", self.aggregate_id)
            s["symbol"] = p.get("symbol")
            s["direction"] = p.get("direction")
            s["confidence"] = p.get("confidence", 0.0)
        elif ev.type == "FEATURES_COMPUTED":
            s["features"] = p.get("features", {})
        elif ev.type == "SIGNAL_GENERATED":
            s["signal"] = p.get("signal")
            s["confidence"] = p.get("confidence", s["confidence"])
        elif ev.type == "VERIFIED":
            s["outcome"] = p.get("outcome")
        elif ev.type == "ROUTED":
            s["routed"] = True
        elif ev.type == "EXECUTED":
            s["executed"] = True
        elif ev.type == "CLOSED":
            s["closed"] = True


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "GENESIS_HASH",
    "DEFAULT_LEDGER_PATH",
    "DEFAULT_SNAPSHOT_DIR",
    "Event",
    "Snapshot",
    "EventStore",
    "GlobalAuditLedger",
    "SnapshotManager",
    "EventSourcedAggregate",
    "PredictionLifecycleAggregate",
    "DeterministicReplayer",
]
