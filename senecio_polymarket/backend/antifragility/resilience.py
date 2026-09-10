"""
ACT-XXIX — Module 5: Resilience — Checkpoints, Recovery, Circuit Breakers
=========================================================================

Protects the system against crashes, resource exhaustion, and downstream
failures. Every subsystem gets a circuit breaker; every critical pipeline
gets a checkpoint + crash recovery; every resource gets a watchdog.

Public surface
--------------
- ``CircuitBreaker``              — closed/open/half-open state machine
- ``CircuitBreakerRegistry``      — manage many named breakers
- ``Checkpoint``                  — frozen snapshot of a named subsystem
- ``CheckpointManager``           — periodic + manual checkpoint store
- ``CrashRecovery``               — detect dirty shutdown, restore from CP
- ``ResourceWatchdog``            — memory growth, latency p99, deadlock
- ``BackgroundIntegrityVerifier`` — async loop running invariants periodically
- ``ResilienceCoordinator``       — orchestrator combining all the above
"""
from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import os
import sys
import threading
import time
import tracemalloc
from collections import deque, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _now_ts() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

class CircuitState(str, Enum):
    CLOSED = "CLOSED"          # normal operation
    OPEN = "OPEN"              # failing, requests rejected
    HALF_OPEN = "HALF_OPEN"    # trial mode — limited requests allowed


@dataclass
class CircuitBreakerStats:
    name: str
    state: CircuitState
    failure_count: int
    success_count: int
    consecutive_failures: int
    last_failure_ts: str | None
    last_success_ts: str | None
    opened_at: str | None
    total_rejections: int


class CircuitBreaker:
    """Classic 3-state circuit breaker.

    CLOSED  → after ``failure_threshold`` consecutive failures → OPEN
    OPEN    → after ``recovery_timeout_s`` seconds → HALF_OPEN
    HALF_OPEN → ``success_threshold`` consecutive successes → CLOSED
              → any failure → OPEN
    """

    def __init__(self, name: str,
                 failure_threshold: int = 5,
                 recovery_timeout_s: float = 30.0,
                 success_threshold: int = 2,
                 half_open_max_calls: int = 1):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.success_threshold = success_threshold
        self.half_open_max_calls = half_open_max_calls
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._failure_count = 0
        self._success_count = 0
        self._total_rejections = 0
        self._last_failure_ts: str | None = None
        self._last_success_ts: str | None = None
        self._opened_at: str | None = None
        self._half_open_calls = 0
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def _maybe_transition_to_half_open(self) -> None:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            opened_ts = datetime.fromisoformat(self._opened_at.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - opened_ts).total_seconds()
            if elapsed >= self.recovery_timeout_s:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self._consecutive_successes = 0

    def allow_request(self) -> bool:
        """Returns True if request should be allowed."""
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                self._total_rejections += 1
                return False
            # HALF_OPEN
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            self._total_rejections += 1
            return False

    def record_success(self) -> None:
        with self._lock:
            self._success_count += 1
            self._last_success_ts = _now_iso()
            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._opened_at = None
            elif self._state == CircuitState.CLOSED:
                self._consecutive_failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._consecutive_failures += 1
            self._last_failure_ts = _now_iso()
            if self._state == CircuitState.HALF_OPEN:
                self._trip_open()
            elif self._state == CircuitState.CLOSED:
                if self._consecutive_failures >= self.failure_threshold:
                    self._trip_open()

    def _trip_open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = _now_iso()
        self._consecutive_successes = 0
        self._half_open_calls = 0

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            self._opened_at = None
            self._half_open_calls = 0

    def stats(self) -> CircuitBreakerStats:
        with self._lock:
            self._maybe_transition_to_half_open()
            return CircuitBreakerStats(
                name=self.name,
                state=self._state,
                failure_count=self._failure_count,
                success_count=self._success_count,
                consecutive_failures=self._consecutive_failures,
                last_failure_ts=self._last_failure_ts,
                last_success_ts=self._last_success_ts,
                opened_at=self._opened_at,
                total_rejections=self._total_rejections,
            )

    def call(self, fn: Callable, *args, **kwargs):
        """Execute fn through the breaker. Raises CircuitOpenError if open."""
        if not self.allow_request():
            raise CircuitOpenError(
                f"circuit '{self.name}' is OPEN (rejected)")
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            self.record_failure()
            raise
        else:
            self.record_success()
            return result


class CircuitOpenError(Exception):
    pass


class CircuitBreakerRegistry:
    """Registry of named circuit breakers."""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get_or_create(self, name: str, **kwargs) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name=name, **kwargs)
            return self._breakers[name]

    def get(self, name: str) -> CircuitBreaker | None:
        with self._lock:
            return self._breakers.get(name)

    def list_names(self) -> list[str]:
        with self._lock:
            return sorted(self._breakers.keys())

    def all_stats(self) -> list[dict]:
        with self._lock:
            return [asdict(self._breakers[n].stats()) for n in sorted(self._breakers)]


# ---------------------------------------------------------------------------
# Checkpoint + CheckpointManager
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Checkpoint:
    """A saved snapshot of one subsystem's state at a point in time."""
    name: str            # subsystem name (e.g. "portfolio_engine")
    seq: int             # monotonic per-subsystem sequence
    state: dict          # serialised state
    ts: str
    hash: str
    is_clean: bool       # True if taken at a known-good moment

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Checkpoint":
        return cls(
            name=d["name"], seq=d["seq"], state=d.get("state", {}),
            ts=d["ts"], hash=d["hash"], is_clean=d.get("is_clean", True),
        )


class CheckpointManager:
    """Manages checkpoints for multiple subsystems.

    - In-memory cache (deque per subsystem, capped at ``max_per_subsystem``)
    - Optional disk persistence under ``checkpoint_dir``
    - ``take_snapshot(name, state, is_clean=True)`` → Checkpoint
    - ``latest(name)`` → most recent Checkpoint
    - ``restore(name)`` → latest clean Checkpoint's state
    """

    def __init__(self, checkpoint_dir: str | Path = "data/antifragility/checkpoints",
                 max_per_subsystem: int = 20,
                 persist_to_disk: bool = True):
        self.checkpoint_dir = Path(checkpoint_dir)
        if persist_to_disk:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.persist_to_disk = persist_to_disk
        self.max_per_subsystem = max_per_subsystem
        self._cache: dict[str, deque[Checkpoint]] = defaultdict(
            lambda: deque(maxlen=max_per_subsystem))
        self._counters: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        if persist_to_disk:
            self._load_all_from_disk()

    def _file_for(self, name: str, seq: int) -> Path:
        safe = name.replace("/", "_").replace(":", "_")
        return self.checkpoint_dir / f"{safe}__seq{seq:010d}.json"

    def _load_all_from_disk(self) -> None:
        if not self.checkpoint_dir.exists():
            return
        for f in sorted(self.checkpoint_dir.glob("*.json")):
            try:
                cp = Checkpoint.from_dict(
                    json.loads(f.read_text(encoding="utf-8")))
                self._cache[cp.name].append(cp)
                self._counters[cp.name] = max(self._counters[cp.name], cp.seq)
            except (json.JSONDecodeError, KeyError):
                continue

    def take_snapshot(self, name: str, state: dict,
                      is_clean: bool = True) -> Checkpoint:
        with self._lock:
            self._counters[name] += 1
            seq = self._counters[name]
            ts = _now_iso()
            h = hashlib.sha256(
                json.dumps(state, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            cp = Checkpoint(name=name, seq=seq, state=state, ts=ts,
                            hash=h, is_clean=is_clean)
            self._cache[name].append(cp)
            if self.persist_to_disk:
                self._file_for(name, seq).write_text(
                    json.dumps(cp.to_dict(), default=str), encoding="utf-8")
            return cp

    def latest(self, name: str) -> Checkpoint | None:
        with self._lock:
            deq = self._cache.get(name)
            return deq[-1] if deq else None

    def latest_clean(self, name: str) -> Checkpoint | None:
        with self._lock:
            deq = self._cache.get(name, deque())
            for cp in reversed(deq):
                if cp.is_clean:
                    return cp
            return None

    def restore(self, name: str) -> dict | None:
        """Returns state of latest clean checkpoint, or None."""
        cp = self.latest_clean(name)
        return cp.state if cp else None

    def list_checkpoints(self, name: str) -> list[Checkpoint]:
        with self._lock:
            return list(self._cache.get(name, deque()))

    def count(self, name: str | None = None) -> int:
        with self._lock:
            if name is None:
                return sum(len(v) for v in self._cache.values())
            return len(self._cache.get(name, deque()))


# ---------------------------------------------------------------------------
# CrashRecovery
# ---------------------------------------------------------------------------

class CrashRecovery:
    """Detects dirty shutdowns and restores subsystems from checkpoints.

    Usage:
        cr = CrashRecovery(checkpoint_mgr)
        cr.mark_started()           # call on startup
        # ... do work, call checkpoint_mgr.take_snapshot periodically ...
        if cr.was_dirty_shutdown():
            state = cr.recover_subsystem("portfolio_engine")
    """

    HEARTBEAT_FILE = "data/antifragility/crash_recovery_heartbeat.json"

    def __init__(self, checkpoint_mgr: CheckpointManager,
                 heartbeat_file: str | Path | None = None):
        self.checkpoint_mgr = checkpoint_mgr
        self.heartbeat_file = Path(heartbeat_file or self.HEARTBEAT_FILE)
        self.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def mark_started(self, subsystems: list[str] | None = None) -> dict:
        """Write a heartbeat file on startup indicating the system is up."""
        hb = {
            "started_at": _now_iso(),
            "pid": os.getpid(),
            "subsystems": subsystems or [],
            "clean_shutdown": False,
        }
        self.heartbeat_file.write_text(
            json.dumps(hb, default=str), encoding="utf-8")
        return hb

    def mark_clean_shutdown(self) -> None:
        """Call on graceful shutdown to mark heartbeat as clean."""
        if not self.heartbeat_file.exists():
            return
        try:
            hb = json.loads(self.heartbeat_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        hb["clean_shutdown"] = True
        hb["shutdown_at"] = _now_iso()
        self.heartbeat_file.write_text(
            json.dumps(hb, default=str), encoding="utf-8")

    def was_dirty_shutdown(self) -> bool:
        if not self.heartbeat_file.exists():
            return False
        try:
            hb = json.loads(self.heartbeat_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return True
        return not hb.get("clean_shutdown", False)

    def recover_subsystem(self, name: str) -> dict | None:
        """Returns restored state, or None if no clean checkpoint available."""
        return self.checkpoint_mgr.restore(name)

    def recover_all(self, subsystems: list[str]) -> dict[str, dict | None]:
        return {name: self.recover_subsystem(name) for name in subsystems}

    def heartbeat(self) -> dict:
        """Update the heartbeat timestamp (call periodically)."""
        if not self.heartbeat_file.exists():
            return self.mark_started()
        try:
            hb = json.loads(self.heartbeat_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self.mark_started()
        hb["last_heartbeat"] = _now_iso()
        self.heartbeat_file.write_text(
            json.dumps(hb, default=str), encoding="utf-8")
        return hb


# ---------------------------------------------------------------------------
# ResourceWatchdog — memory, latency, deadlock
# ---------------------------------------------------------------------------

class ResourceWatchdog:
    """Monitors runtime resources and emits alerts.

    Tracks:
      - Memory growth (using tracemalloc when enabled)
      - Latency p99 of named operations
      - Per-thread CPU time (for deadlock detection heuristic)
      - Custom user-defined monitors
    """

    def __init__(self, memory_warning_mb: float = 500.0,
                 memory_critical_mb: float = 1000.0,
                 latency_p99_warning_ms: float = 500.0,
                 deadlock_check_interval_s: float = 60.0):
        self.memory_warning_mb = memory_warning_mb
        self.memory_critical_mb = memory_critical_mb
        self.latency_p99_warning_ms = latency_p99_warning_ms
        self.deadlock_check_interval_s = deadlock_check_interval_s
        self._latency_samples: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=1000))
        self._alerts: deque[dict] = deque(maxlen=500)
        self._custom_monitors: dict[str, Callable[[], dict]] = {}
        self._tracemalloc_started = False
        self._lock = threading.Lock()

    def start_tracemalloc(self) -> None:
        if not tracemalloc.is_tracing():
            tracemalloc.start()
            self._tracemalloc_started = True

    def stop_tracemalloc(self) -> None:
        if self._tracemalloc_started and tracemalloc.is_tracing():
            tracemalloc.stop()
            self._tracemalloc_started = False

    def record_latency(self, operation: str, duration_ms: float) -> None:
        with self._lock:
            self._latency_samples[operation].append(float(duration_ms))

    def latency_stats(self, operation: str) -> dict:
        with self._lock:
            samples = list(self._latency_samples.get(operation, deque()))
        if not samples:
            return {"operation": operation, "samples": 0}
        arr = sorted(samples)
        n = len(arr)
        return {
            "operation": operation,
            "samples": n,
            "min_ms": arr[0],
            "max_ms": arr[-1],
            "mean_ms": sum(arr) / n,
            "p50_ms": arr[n // 2],
            "p95_ms": arr[int(n * 0.95)] if n >= 20 else arr[-1],
            "p99_ms": arr[int(n * 0.99)] if n >= 100 else arr[-1],
            "warning_threshold_ms": self.latency_p99_warning_ms,
        }

    def memory_usage_mb(self) -> dict:
        """Returns current process memory usage in MB."""
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            mem = proc.memory_info().rss / (1024 * 1024)
            return {
                "rss_mb": mem,
                "warning_mb": self.memory_warning_mb,
                "critical_mb": self.memory_critical_mb,
                "status": (
                    "CRITICAL" if mem > self.memory_critical_mb
                    else "WARNING" if mem > self.memory_warning_mb
                    else "OK"
                ),
            }
        except ImportError:
            # Fallback: use tracemalloc if available
            if tracemalloc.is_tracing():
                current, peak = tracemalloc.get_traced_memory()
                return {
                    "traced_current_mb": current / (1024 * 1024),
                    "traced_peak_mb": peak / (1024 * 1024),
                    "warning_mb": self.memory_warning_mb,
                    "critical_mb": self.memory_critical_mb,
                }
            return {"error": "psutil not available and tracemalloc not started"}

    def register_monitor(self, name: str,
                         check_fn: Callable[[], dict]) -> None:
        with self._lock:
            self._custom_monitors[name] = check_fn

    def check_all(self) -> dict:
        """Run all monitors and return a snapshot."""
        result = {
            "ts": _now_iso(),
            "memory": self.memory_usage_mb(),
            "latency": {},
            "custom": {},
        }
        # Latency stats for all operations
        with self._lock:
            ops = list(self._latency_samples.keys())
        for op in ops:
            stats = self.latency_stats(op)
            if stats.get("p99_ms", 0) > self.latency_p99_warning_ms:
                self._alerts.append({
                    "ts": _now_iso(), "severity": "WARN",
                    "component": f"latency:{op}",
                    "msg": f"p99 {stats['p99_ms']:.1f}ms > {self.latency_p99_warning_ms}ms",
                })
            result["latency"][op] = stats
        # Memory alerts
        mem = result["memory"]
        if mem.get("status") == "CRITICAL":
            self._alerts.append({
                "ts": _now_iso(), "severity": "CRITICAL",
                "component": "memory",
                "msg": f"RSS {mem.get('rss_mb', 0):.1f}MB > {self.memory_critical_mb}MB",
            })
        elif mem.get("status") == "WARNING":
            self._alerts.append({
                "ts": _now_iso(), "severity": "WARN",
                "component": "memory",
                "msg": f"RSS {mem.get('rss_mb', 0):.1f}MB > {self.memory_warning_mb}MB",
            })
        # Custom monitors
        with self._lock:
            monitors = list(self._custom_monitors.items())
        for name, fn in monitors:
            try:
                result["custom"][name] = fn()
            except Exception as e:
                result["custom"][name] = {"error": str(e)}
                self._alerts.append({
                    "ts": _now_iso(), "severity": "ERROR",
                    "component": f"monitor:{name}",
                    "msg": f"monitor raised: {e}",
                })
        return result

    def alerts(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._alerts)[-limit:]


# ---------------------------------------------------------------------------
# BackgroundIntegrityVerifier
# ---------------------------------------------------------------------------

class BackgroundIntegrityVerifier:
    """Runs a verification function periodically in a background thread.

    Usage:
        biv = BackgroundIntegrityVerifier(
            check_fn=lambda: my_invariant_registry.run_all(),
            interval_s=60.0,
        )
        biv.start()
        # ... later ...
        biv.stop()
    """

    def __init__(self, check_fn: Callable[[], Any],
                 interval_s: float = 60.0,
                 name: str = "integrity_verifier"):
        self.check_fn = check_fn
        self.interval_s = interval_s
        self.name = name
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._results: deque[dict] = deque(maxlen=100)
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name=self.name, daemon=True)
        self._thread.start()
        self._running = True

    def stop(self, timeout: float = 5.0) -> None:
        if not self._running:
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._running = False

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = self.check_fn()
                ok = True
                if isinstance(result, list):
                    ok = all(getattr(r, "ok", True) for r in result)
                elif isinstance(result, dict):
                    ok = result.get("ok", True)
            except Exception as e:
                result = {"error": str(e)}
                ok = False
            with self._lock:
                self._results.append({
                    "ts": _now_iso(),
                    "ok": ok,
                    "result": str(result)[:500],
                })
            # Wait but allow stop signal
            self._stop_event.wait(self.interval_s)

    def latest(self) -> dict | None:
        with self._lock:
            return self._results[-1] if self._results else None

    def history(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(self._results)[-limit:]

    @property
    def is_running(self) -> bool:
        return self._running


# ---------------------------------------------------------------------------
# ResilienceCoordinator
# ---------------------------------------------------------------------------

class ResilienceCoordinator:
    """One-stop shop for resilience: registry + checkpoints + watchdog + BIV.

    Subsystems register themselves, get a circuit breaker, and can checkpoint
    their state. The coordinator runs a background integrity verifier.
    """

    def __init__(self, checkpoint_dir: str | Path = "data/antifragility/checkpoints",
                 verification_fn: Callable[[], Any] | None = None,
                 verification_interval_s: float = 60.0):
        self.breakers = CircuitBreakerRegistry()
        self.checkpoints = CheckpointManager(checkpoint_dir=checkpoint_dir)
        self.crash_recovery = CrashRecovery(self.checkpoints)
        self.watchdog = ResourceWatchdog()
        self.biv = (
            BackgroundIntegrityVerifier(
                check_fn=verification_fn,
                interval_s=verification_interval_s,
                name="resilience_biv",
            ) if verification_fn is not None else None
        )
        self._subsystems: dict[str, dict] = {}
        self._lock = threading.Lock()

    def register_subsystem(self, name: str,
                           state_getter: Callable[[], dict],
                           breaker_config: dict | None = None) -> None:
        with self._lock:
            self._subsystems[name] = {"state_getter": state_getter}
        self.breakers.get_or_create(name, **(breaker_config or {}))

    def checkpoint_all(self) -> dict[str, Checkpoint | None]:
        out: dict[str, Checkpoint | None] = {}
        with self._lock:
            subs = dict(self._subsystems)
        for name, cfg in subs.items():
            try:
                state = cfg["state_getter"]()
                cp = self.checkpoints.take_snapshot(name, state, is_clean=True)
                out[name] = cp
            except Exception:
                out[name] = None
        return out

    def start_biv(self) -> None:
        if self.biv is not None:
            self.biv.start()

    def stop_biv(self) -> None:
        if self.biv is not None:
            self.biv.stop()

    def snapshot(self) -> dict:
        return {
            "ts": _now_iso(),
            "breakers": self.breakers.all_stats(),
            "checkpoint_counts": {
                name: self.checkpoints.count(name)
                for name in self._subsystems
            },
            "watchdog": self.watchdog.check_all(),
            "biv_running": self.biv.is_running if self.biv else False,
            "biv_latest": self.biv.latest() if self.biv else None,
            "crash_recovery_dirty": self.crash_recovery.was_dirty_shutdown(),
        }


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "CircuitState",
    "CircuitBreakerStats",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitBreakerRegistry",
    "Checkpoint",
    "CheckpointManager",
    "CrashRecovery",
    "ResourceWatchdog",
    "BackgroundIntegrityVerifier",
    "ResilienceCoordinator",
]
