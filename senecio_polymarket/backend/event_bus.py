"""
SENECIO ORACLE — Event Bus
==========================
Central in-memory pub/sub that all layers publish/subscribe to.
- Async-first (asyncio.Queue per subscriber)
- Replayable (every event is also persisted via audit_store)
- Backpressure-safe (bounded queues with drop-oldest on overflow)
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable

from .models import BaseEvent, to_log_line
from .audit_store import AuditStore

log = logging.getLogger("senecio.bus")

EventHandler = Callable[[BaseEvent], Awaitable[None]]


class EventBus:
    def __init__(self, audit: AuditStore, max_queue: int = 1024):
        self.audit = audit
        self.max_queue = max_queue
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._global_subscribers: list[asyncio.Queue] = []
        self._closed = False
        self._counter = 0

    # ---- subscription management ----
    def subscribe(self, event_type: str | None = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.max_queue)
        if event_type is None:
            self._global_subscribers.append(q)
        else:
            self._subscribers[event_type].append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue, event_type: str | None = None) -> None:
        if event_type is None:
            if q in self._global_subscribers:
                self._global_subscribers.remove(q)
        else:
            if q in self._subscribers.get(event_type, []):
                self._subscribers[event_type].remove(q)

    # ---- publishing ----
    async def publish(self, ev: BaseEvent) -> None:
        """Publish an event to all matching subscribers + persist to audit log."""
        self._counter += 1
        # 1. persist (fire-and-forget, but flush periodically)
        try:
            self.audit.append(ev)
        except Exception as e:
            log.error("audit append failed: %s", e)

        # 2. dispatch to typed subscribers
        targets: list[asyncio.Queue] = list(self._subscribers.get(ev.event_type.value, []))
        # 3. dispatch to global subscribers
        targets.extend(self._global_subscribers)

        for q in targets:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # drop oldest to make room
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except Exception:
                    pass  # give up on this subscriber

    def stats(self) -> dict:
        return {
            "events_published": self._counter,
            "typed_subscribers": {k: len(v) for k, v in self._subscribers.items()},
            "global_subscribers": len(self._global_subscribers),
        }

    async def close(self) -> None:
        self._closed = True
        self.audit.flush()
