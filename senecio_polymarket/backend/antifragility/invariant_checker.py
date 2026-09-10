"""
ACT-XXIX — Module 2: Invariant Checking & State-Machine Validation
==================================================================

Continuously verifies systemic invariants on every critical pipeline.

Public surface
--------------
- ``Invariant``                — base class with check() -> InvariantResult
- ``InvariantResult``          — frozen dataclass (name, ok, msg, severity, ts)
- ``InvariantRegistry``        — registry + run_all / run_by_tag
- ``StateMachineValidator``    — declare legal transitions, detect illegal
- ``DependencyGraphValidator`` — DAG validation, cycle detection
- ``CorruptionDetector``       — hash mismatch + schema violation detection
- ``RuntimeAssertions``        — ergonomic helpers (assert_in_range, etc.)

Severity levels
---------------
- INFO    — pass with note
- WARN    — soft violation (logged but doesn't fail the suite)
- ERROR   — hard violation (fails the suite)
- CRITICAL — system-stopping violation (escalates to kill switch)
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# Severity + Result
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class InvariantResult:
    name: str
    ok: bool
    severity: Severity
    msg: str
    ts: str
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


# ---------------------------------------------------------------------------
# Invariant base class
# ---------------------------------------------------------------------------

class Invariant:
    """Base class. Subclasses implement ``check(context) -> InvariantResult``."""
    name: str = "invariant"
    tags: tuple[str, ...] = ()
    severity: Severity = Severity.ERROR

    def check(self, context: dict | None = None) -> InvariantResult:
        raise NotImplementedError

    def _ok(self, msg: str = "ok", context: dict | None = None) -> InvariantResult:
        return InvariantResult(
            name=self.name, ok=True, severity=Severity.INFO,
            msg=msg, ts=_now_iso(),
            context=context or {},
        )

    def _fail(self, msg: str, context: dict | None = None,
              severity: Severity | None = None) -> InvariantResult:
        return InvariantResult(
            name=self.name, ok=False, severity=severity or self.severity,
            msg=msg, ts=_now_iso(),
            context=context or {},
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Concrete built-in invariants
# ---------------------------------------------------------------------------

class RangeInvariant(Invariant):
    """Assert ``value`` is in [low, high]."""
    def __init__(self, name: str, getter: Callable[[], float],
                 low: float, high: float,
                 severity: Severity = Severity.ERROR,
                 tags: tuple[str, ...] = ()):
        self.name = name
        self._getter = getter
        self._low = low
        self._high = high
        self.severity = severity
        self.tags = tags

    def check(self, context: dict | None = None) -> InvariantResult:
        try:
            v = float(self._getter())
        except Exception as e:
            return self._fail(f"getter raised: {e}")
        if v < self._low or v > self._high:
            return self._fail(
                f"{v} outside [{self._low}, {self._high}]",
                context={"value": v, "low": self._low, "high": self._high},
            )
        return self._ok(context={"value": v})


class HashChainInvariant(Invariant):
    """Verify a hash-chained event store's integrity."""
    def __init__(self, name: str, store,
                 severity: Severity = Severity.CRITICAL,
                 tags: tuple[str, ...] = ("integrity",)):
        self.name = name
        self._store = store
        self.severity = severity
        self.tags = tags

    def check(self, context: dict | None = None) -> InvariantResult:
        ok, broken = self._store.verify_chain()
        if ok:
            return self._ok(
                f"chain intact ({self._store.count()} events)",
                context={"count": self._store.count()},
            )
        return self._fail(
            f"chain broken at seqs {broken[:5]}",
            context={"broken_seqs": broken, "count": self._store.count()},
        )


class SchemaInvariant(Invariant):
    """Verify ``obj`` matches a JSON-schema-like spec (subset).

    Spec syntax (recursive):
      {"type": "object", "required": ["a", "b"],
       "properties": {"a": {"type": "string"},
                       "b": {"type": "number", "min": 0, "max": 1}}}
      {"type": "array", "items": {"type": "integer"}}
      {"type": "string"} / {"type": "number"} / {"type": "boolean"}
    """
    def __init__(self, name: str, getter: Callable[[], Any], spec: dict,
                 severity: Severity = Severity.ERROR,
                 tags: tuple[str, ...] = ("schema",)):
        self.name = name
        self._getter = getter
        self._spec = spec
        self.severity = severity
        self.tags = tags

    def check(self, context: dict | None = None) -> InvariantResult:
        try:
            obj = self._getter()
        except Exception as e:
            return self._fail(f"getter raised: {e}")
        errs = _validate_schema(obj, self._spec, path="$")
        if errs:
            return self._fail(
                f"schema violations: {errs[:3]}",
                context={"errors": errs, "obj": str(obj)[:200]},
            )
        return self._ok(context={"obj_type": type(obj).__name__})


def _validate_schema(obj: Any, spec: dict, path: str = "$") -> list[str]:
    errs: list[str] = []
    t = spec.get("type")
    if t == "object":
        if not isinstance(obj, dict):
            errs.append(f"{path}: expected object, got {type(obj).__name__}")
            return errs
        for k in spec.get("required", []):
            if k not in obj:
                errs.append(f"{path}: missing required key '{k}'")
        for k, sub_spec in spec.get("properties", {}).items():
            if k in obj:
                errs.extend(_validate_schema(obj[k], sub_spec, f"{path}.{k}"))
    elif t == "array":
        if not isinstance(obj, list):
            errs.append(f"{path}: expected array, got {type(obj).__name__}")
            return errs
        item_spec = spec.get("items")
        if item_spec:
            for i, item in enumerate(obj):
                errs.extend(_validate_schema(item, item_spec, f"{path}[{i}]"))
    elif t == "string":
        if not isinstance(obj, str):
            errs.append(f"{path}: expected string, got {type(obj).__name__}")
    elif t == "number":
        if not isinstance(obj, (int, float)) or isinstance(obj, bool):
            errs.append(f"{path}: expected number, got {type(obj).__name__}")
        else:
            if "min" in spec and obj < spec["min"]:
                errs.append(f"{path}: {obj} < min {spec['min']}")
            if "max" in spec and obj > spec["max"]:
                errs.append(f"{path}: {obj} > max {spec['max']}")
    elif t == "integer":
        if not isinstance(obj, int) or isinstance(obj, bool):
            errs.append(f"{path}: expected integer, got {type(obj).__name__}")
    elif t == "boolean":
        if not isinstance(obj, bool):
            errs.append(f"{path}: expected boolean, got {type(obj).__name__}")
    return errs


class ThresholdInvariant(Invariant):
    """Assert ``value`` passes a predicate."""
    def __init__(self, name: str, getter: Callable[[], Any],
                 predicate: Callable[[Any], bool],
                 severity: Severity = Severity.ERROR,
                 tags: tuple[str, ...] = ()):
        self.name = name
        self._getter = getter
        self._predicate = predicate
        self.severity = severity
        self.tags = tags

    def check(self, context: dict | None = None) -> InvariantResult:
        try:
            v = self._getter()
            ok = bool(self._predicate(v))
        except Exception as e:
            return self._fail(f"predicate raised: {e}")
        if ok:
            return self._ok(context={"value": v})
        return self._fail(f"predicate failed for value={v!r}",
                          context={"value": v})


# ---------------------------------------------------------------------------
# InvariantRegistry
# ---------------------------------------------------------------------------

class InvariantRegistry:
    """Thread-safe registry of invariants. Runs them on demand.

    Aggregates results, tracks pass/fail history, supports tagging.
    """

    def __init__(self, history_size: int = 1000):
        self._invariants: list[Invariant] = []
        self._by_tag: dict[str, list[int]] = defaultdict(list)
        self._history: deque[dict] = deque(maxlen=history_size)
        self._lock = threading.Lock()

    def register(self, inv: Invariant) -> Invariant:
        with self._lock:
            idx = len(self._invariants)
            self._invariants.append(inv)
            for t in inv.tags:
                self._by_tag[t].append(idx)
        return inv

    def unregister(self, name: str) -> bool:
        with self._lock:
            for i, inv in enumerate(self._invariants):
                if inv.name == name:
                    self._invariants.pop(i)
                    self._rebuild_index()
                    return True
            return False

    def _rebuild_index(self) -> None:
        self._by_tag = defaultdict(list)
        for i, inv in enumerate(self._invariants):
            for t in inv.tags:
                self._by_tag[t].append(i)

    def list_invariants(self) -> list[str]:
        with self._lock:
            return [inv.name for inv in self._invariants]

    def run_all(self, context: dict | None = None) -> list[InvariantResult]:
        with self._lock:
            invs = list(self._invariants)
        results = []
        for inv in invs:
            try:
                r = inv.check(context)
            except Exception as e:
                r = InvariantResult(
                    name=inv.name, ok=False, severity=Severity.CRITICAL,
                    msg=f"uncaught exception: {e!r}", ts=_now_iso(),
                )
            results.append(r)
            self._record(r)
        return results

    def run_by_tag(self, tag: str,
                   context: dict | None = None) -> list[InvariantResult]:
        with self._lock:
            idxs = list(self._by_tag.get(tag, []))
            invs = [self._invariants[i] for i in idxs]
        results = []
        for inv in invs:
            try:
                r = inv.check(context)
            except Exception as e:
                r = InvariantResult(
                    name=inv.name, ok=False, severity=Severity.CRITICAL,
                    msg=f"uncaught exception: {e!r}", ts=_now_iso(),
                )
            results.append(r)
            self._record(r)
        return results

    def _record(self, r: InvariantResult) -> None:
        with self._lock:
            self._history.append({
                "ts": r.ts, "name": r.name, "ok": r.ok,
                "severity": r.severity.value, "msg": r.msg,
            })

    def summary(self) -> dict:
        with self._lock:
            total = len(self._invariants)
            history = list(self._history)
        last_results: dict[str, InvariantResult] = {}
        # Use the most recent result per invariant name
        for entry in reversed(history):
            name = entry["name"]
            if name not in last_results:
                last_results[name] = entry
        ok_count = sum(1 for r in last_results.values() if r["ok"])
        fail_count = sum(1 for r in last_results.values() if not r["ok"])
        return {
            "total_invariants": total,
            "last_run_ok": ok_count,
            "last_run_fail": fail_count,
            "history_size": len(history),
        }


# ---------------------------------------------------------------------------
# StateMachineValidator — declare legal transitions
# ---------------------------------------------------------------------------

class StateMachineValidator:
    """Validates state transitions against a declared transition table.

    Usage:
        sm = StateMachineValidator()
        sm.add_state("IDLE")
        sm.add_state("PROPOSED")
        sm.add_state("EXECUTED")
        sm.add_state("CLOSED")
        sm.add_transition("IDLE", "PROPOSED")
        sm.add_transition("PROPOSED", "EXECUTED")
        sm.add_transition("EXECUTED", "CLOSED")
        sm.transition("IDLE", "PROPOSED")  # ok
        sm.transition("PROPOSED", "CLOSED")  # raises IllegalTransition
    """

    class IllegalTransition(Exception):
        pass

    def __init__(self, name: str = "default"):
        self.name = name
        self._states: set[str] = set()
        self._transitions: dict[str, set[str]] = defaultdict(set)
        self._current: str | None = None
        self._history: list[tuple[str, str, str]] = []
        self._lock = threading.Lock()

    def add_state(self, state: str) -> "StateMachineValidator":
        self._states.add(state)
        return self

    def add_transition(self, frm: str, to: str) -> "StateMachineValidator":
        if frm not in self._states:
            self._states.add(frm)
        if to not in self._states:
            self._states.add(to)
        self._transitions[frm].add(to)
        return self

    def set_current(self, state: str) -> None:
        with self._lock:
            if state not in self._states:
                raise ValueError(f"unknown state: {state}")
            self._current = state

    @property
    def current(self) -> str | None:
        return self._current

    def is_legal(self, frm: str, to: str) -> bool:
        return to in self._transitions.get(frm, set())

    def transition(self, frm: str, to: str, ts: str | None = None) -> str:
        with self._lock:
            if not self.is_legal(frm, to):
                raise StateMachineValidator.IllegalTransition(
                    f"illegal transition {frm} -> {to} "
                    f"(legal: {sorted(self._transitions.get(frm, set()))})")
            self._current = to
            self._history.append((frm, to, ts or _now_iso()))
            return to

    def legal_transitions_from(self, frm: str) -> set[str]:
        return set(self._transitions.get(frm, set()))

    def get_history(self) -> list[tuple[str, str, str]]:
        with self._lock:
            return list(self._history)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "states": sorted(self._states),
            "transitions": {k: sorted(v) for k, v in self._transitions.items()},
            "current": self._current,
            "history_size": len(self._history),
        }


# ---------------------------------------------------------------------------
# DependencyGraphValidator — DAG validation
# ---------------------------------------------------------------------------

class DependencyGraphValidator:
    """Validates a directed graph is acyclic (DAG) and well-formed.

    Usage:
        dg = DependencyGraphValidator()
        dg.add_node("portfolio_engine")
        dg.add_node("risk_kernel")
        dg.add_edge("portfolio_engine", "risk_kernel")  # PE depends on RK
        dg.add_edge("risk_kernel", "execution_engine")
        cycle = dg.detect_cycle()  # None or list of nodes
        topo = dg.topological_sort()
    """

    def __init__(self, name: str = "dependency_graph"):
        self.name = name
        self._nodes: set[str] = set()
        self._edges: dict[str, set[str]] = defaultdict(set)  # src -> {dsts}
        self._reverse: dict[str, set[str]] = defaultdict(set)
        self._lock = threading.Lock()

    def add_node(self, name: str) -> "DependencyGraphValidator":
        with self._lock:
            self._nodes.add(name)
        return self

    def add_edge(self, src: str, dst: str) -> "DependencyGraphValidator":
        """Declare ``src`` depends on ``dst``."""
        with self._lock:
            self._nodes.add(src)
            self._nodes.add(dst)
            self._edges[src].add(dst)
            self._reverse[dst].add(src)
        return self

    def detect_cycle(self) -> list[str] | None:
        """Return a cycle as a list of nodes, or None if DAG is acyclic."""
        with self._lock:
            WHITE, GRAY, BLACK = 0, 1, 2
            color = {n: WHITE for n in self._nodes}
            parent: dict[str, str | None] = {n: None for n in self._nodes}
            cycle: list[str] | None = None

            def dfs(u: str) -> bool:
                nonlocal cycle
                color[u] = GRAY
                for v in self._edges.get(u, set()):
                    if color[v] == GRAY:
                        # Found cycle: walk back from u to v
                        path = [u]
                        cur = u
                        while cur is not None and cur != v:
                            cur = parent[cur]
                            if cur is not None:
                                path.append(cur)
                        path.reverse()
                        path.append(u)
                        cycle = path
                        return True
                    if color[v] == WHITE:
                        parent[v] = u
                        if dfs(v):
                            return True
                color[u] = BLACK
                return False

            for n in self._nodes:
                if color[n] == WHITE:
                    if dfs(n):
                        break
            return cycle

    def topological_sort(self) -> list[str] | None:
        """Kahn's algorithm. Returns None if cycle exists."""
        with self._lock:
            in_deg = {n: 0 for n in self._nodes}
            for src, dsts in self._edges.items():
                for d in dsts:
                    in_deg[d] += 1
            queue = sorted([n for n, d in in_deg.items() if d == 0])
            out: list[str] = []
            local_in = dict(in_deg)
            local_edges = {k: set(v) for k, v in self._edges.items()}
            while queue:
                n = queue.pop(0)
                out.append(n)
                for m in sorted(local_edges.get(n, set())):
                    local_in[m] -= 1
                    if local_in[m] == 0:
                        # Insert in sorted order for determinism
                        import bisect
                        bisect.insort(queue, m)
            if len(out) != len(self._nodes):
                return None
            return out

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "nodes": sorted(self._nodes),
            "edges": {k: sorted(v) for k, v in self._edges.items()},
        }


# ---------------------------------------------------------------------------
# CorruptionDetector — hash + schema violation detection
# ---------------------------------------------------------------------------

class CorruptionDetector:
    """Detects corruption in stored objects by hash comparison + schema.

    Two modes:
      1. Register an object's expected hash; later verify it.
      2. Register a schema; later verify objects against it.
    """

    def __init__(self):
        self._expected_hashes: dict[str, str] = {}
        self._schemas: dict[str, dict] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _hash_obj(obj: Any) -> str:
        return hashlib.sha256(
            json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def register_hash(self, key: str, obj: Any) -> str:
        h = self._hash_obj(obj)
        with self._lock:
            self._expected_hashes[key] = h
        return h

    def register_schema(self, key: str, spec: dict) -> None:
        with self._lock:
            self._schemas[key] = spec

    def verify_hash(self, key: str, obj: Any) -> tuple[bool, str]:
        """Returns (ok, expected_hash)."""
        with self._lock:
            expected = self._expected_hashes.get(key)
        if expected is None:
            return True, "no-expected-hash"
        actual = self._hash_obj(obj)
        return (actual == expected), expected

    def verify_schema(self, key: str, obj: Any) -> tuple[bool, list[str]]:
        with self._lock:
            spec = self._schemas.get(key)
        if spec is None:
            return True, []
        errs = _validate_schema(obj, spec)
        return (len(errs) == 0), errs

    def verify_all(self, key: str, obj: Any) -> dict:
        ok_h, expected_h = self.verify_hash(key, obj)
        ok_s, errs = self.verify_schema(key, obj)
        return {
            "key": key,
            "hash_ok": ok_h,
            "expected_hash": expected_h,
            "actual_hash": self._hash_obj(obj),
            "schema_ok": ok_s,
            "schema_errors": errs,
            "ok": ok_h and ok_s,
        }


# ---------------------------------------------------------------------------
# RuntimeAssertions — ergonomic helpers
# ---------------------------------------------------------------------------

class RuntimeAssertions:
    """Convenience assertion helpers that raise ``AssertionError`` on fail.

    Designed for inline use in critical pipelines:

        RuntimeAssertions.in_range(confidence, 0.0, 1.0, "confidence")
        RuntimeAssertions.is_type(direction, str, "direction")
        RuntimeAssertions.not_none(prediction_id, "prediction_id")
    """

    @staticmethod
    def in_range(value: float, low: float, high: float, name: str = "value") -> None:
        if not (low <= value <= high):
            raise AssertionError(
                f"{name}={value} outside [{low}, {high}]")

    @staticmethod
    def is_type(value: Any, expected: type, name: str = "value") -> None:
        if not isinstance(value, expected):
            raise AssertionError(
                f"{name} expected {expected.__name__}, got {type(value).__name__}")

    @staticmethod
    def not_none(value: Any, name: str = "value") -> None:
        if value is None:
            raise AssertionError(f"{name} is None")

    @staticmethod
    def is_in(value: Any, allowed: Iterable, name: str = "value") -> None:
        allowed_list = list(allowed)
        if value not in allowed_list:
            raise AssertionError(
                f"{name}={value!r} not in allowed {allowed_list}")

    @staticmethod
    def matches(value: str, pattern: str, name: str = "value") -> None:
        if not re.match(pattern, value):
            raise AssertionError(
                f"{name}={value!r} does not match pattern {pattern!r}")

    @staticmethod
    def is_positive(value: float, name: str = "value") -> None:
        if value <= 0:
            raise AssertionError(f"{name}={value} not positive")

    @staticmethod
    def is_non_negative(value: float, name: str = "value") -> None:
        if value < 0:
            raise AssertionError(f"{name}={value} negative")


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "Severity",
    "InvariantResult",
    "Invariant",
    "RangeInvariant",
    "HashChainInvariant",
    "SchemaInvariant",
    "ThresholdInvariant",
    "InvariantRegistry",
    "StateMachineValidator",
    "DependencyGraphValidator",
    "CorruptionDetector",
    "RuntimeAssertions",
]
