"""
SENECIO ORACLE — Layer 5: WebSocket Server + SSE fallback
==========================================================
Bridges the EventBus to live clients.
- WS endpoint /ws  : full event stream (filtered by ?type= if provided)
- SSE endpoint /sse: same stream as Server-Sent Events (fallback)

Each client gets its own bounded queue. On overflow, oldest events drop.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from .event_bus import EventBus
from .models import BaseEvent

log = logging.getLogger("senecio.ws")
router = APIRouter()


class ConnectionManager:
    def __init__(self, bus: EventBus):
        self.bus = bus
        self.ws_clients: list[tuple[WebSocket, Optional[str]]] = []

    def add_ws(self, ws: WebSocket, event_type: Optional[str]) -> asyncio.Queue:
        q = self.bus.subscribe(event_type)
        self.ws_clients.append((ws, event_type))
        return q

    def remove_ws(self, ws: WebSocket, q: asyncio.Queue, event_type: Optional[str]) -> None:
        self.bus.unsubscribe(q, event_type)
        self.ws_clients = [(c, et) for c, et in self.ws_clients if c is not ws]

    def stats(self) -> dict:
        return {"ws_clients": len(self.ws_clients), **self.bus.stats()}


def make_router(bus: EventBus) -> APIRouter:
    cm = ConnectionManager(bus)
    r = APIRouter()

    @r.websocket("/ws")
    async def ws_stream(websocket: WebSocket, type: Optional[str] = Query(default=None)):
        await websocket.accept()
        q = cm.add_ws(websocket, type)
        try:
            while True:
                ev: BaseEvent = await q.get()
                await websocket.send_text(ev.model_dump_json())
        except WebSocketDisconnect:
            log.info("ws client disconnected")
        except Exception as e:
            log.warning("ws error: %s", e)
        finally:
            cm.remove_ws(websocket, q, type)

    @r.get("/sse")
    async def sse_stream(type: Optional[str] = Query(default=None)):
        q = bus.subscribe(type)

        async def gen():
            try:
                while True:
                    ev: BaseEvent = await q.get()
                    yield {
                        "event": ev.event_type.value,
                        "data": ev.model_dump_json(),
                    }
            except asyncio.CancelledError:
                pass
            finally:
                bus.unsubscribe(q, type)

        return EventSourceResponse(gen())

    @r.get("/stats")
    async def stats():
        return cm.stats()

    return r
