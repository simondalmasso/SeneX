from __future__ import annotations

import hmac
import json
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .decisions import (
    ConflictingDecisionError,
    CursorMismatchError,
    DecisionService,
    DecisionValidationError,
)
from .sealer import PACKET_HARD_MAX_BYTES
from .store import GPTraderStore

PROTOCOL_VERSION = "2025-06-18"
TOKEN_ENV = "SENEX_GPTRADER_MCP_TOKEN"
INGEST_TOKEN_ENV = "SENEX_GPTRADER_INGEST_TOKEN"
INGEST_REQUEST_MAX_BYTES = PACKET_HARD_MAX_BYTES + 1024


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "get_gptrader_health",
            "description": "Read PAPER-only GPTrader Decision MCP health.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "get_prediction_batch",
            "description": "Read one bounded batch of unseen sealed T0 packets.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cursor": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 16},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_gptrader_state",
            "description": "Read decision-safe hypothetical PAPER state.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "submit_paper_decisions",
            "description": "Durably submit one bounded TAKE/ABSTAIN decision batch.",
            "inputSchema": {
                "type": "object",
                "required": ["run_id", "cursor", "decisions"],
                "properties": {
                    "run_id": {"type": "string", "minLength": 1, "maxLength": 160},
                    "cursor": {"type": "string", "minLength": 1},
                    "decisions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 16,
                        "items": {
                            "type": "object",
                            "required": [
                                "packet_id",
                                "action",
                                "reason_codes",
                                "idempotency_key",
                            ],
                            "properties": {
                                "packet_id": {"type": "string", "minLength": 1},
                                "action": {"enum": ["TAKE", "ABSTAIN"]},
                                "reason_codes": {
                                    "type": "array",
                                    "maxItems": 8,
                                    "items": {"type": "string", "maxLength": 64},
                                },
                                "idempotency_key": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 160,
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
    ]


def _jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    *,
    data: Any = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _tool_result(value: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            }
        ],
        "structuredContent": value,
        "isError": False,
    }


async def _call_tool(
    service: DecisionService,
    name: str,
    arguments: dict[str, Any],
) -> Any:
    if name == "get_gptrader_health":
        if arguments:
            raise DecisionValidationError("get_gptrader_health takes no arguments")
        return service.get_gptrader_health()
    if name == "get_prediction_batch":
        return service.get_prediction_batch(
            arguments.get("cursor"),
            int(arguments.get("limit", 8)),
        )
    if name == "get_gptrader_state":
        if arguments:
            raise DecisionValidationError("get_gptrader_state takes no arguments")
        return service.get_gptrader_state()
    if name == "submit_paper_decisions":
        return await service.submit_paper_decisions(
            str(arguments.get("run_id") or ""),
            str(arguments.get("cursor") or ""),
            arguments.get("decisions"),
        )
    raise DecisionValidationError("UNKNOWN_TOOL")


async def _read_bounded_json(request: Request) -> Any:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > INGEST_REQUEST_MAX_BYTES:
            raise HTTPException(status_code=413, detail="INGEST_REQUEST_TOO_LARGE")
        body.extend(chunk)
    try:
        return json.loads(bytes(body))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="INVALID_INGEST_REQUEST") from exc


def build_mcp_app(
    service: DecisionService,
    *,
    token: str,
    ingest_token: str | None = None,
) -> FastAPI:
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("bearer token must be at least 32 characters")
    if ingest_token is not None and (
        not isinstance(ingest_token, str) or len(ingest_token) < 32
    ):
        raise ValueError("ingest bearer token must be at least 32 characters")

    app = FastAPI(
        title="SENEX GPTrader Decision MCP",
        version="gptrader.decision.v1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    if ingest_token is not None:
        @app.post("/ingest/t0")
        async def ingest_t0(request: Request):
            authorization = request.headers.get("authorization") or ""
            expected = f"Bearer {ingest_token}"
            if not hmac.compare_digest(authorization, expected):
                raise HTTPException(status_code=401, detail="INGEST_AUTH_REQUIRED")

            payload = await _read_bounded_json(request)
            if not isinstance(payload, dict) or set(payload) != {"packet"}:
                raise HTTPException(status_code=400, detail="INVALID_INGEST_REQUEST")
            try:
                accepted = service.sealer.ingest(payload["packet"])
            except (ValueError, RuntimeError, TypeError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail=type(exc).__name__,
                ) from exc
            return {
                "packet_id": accepted["packet_id"],
                "packet_seq": accepted["packet_seq"],
                "packet_hash": accepted["packet_hash"],
            }

    @app.post("/mcp")
    async def mcp(request: Request):
        authorization = request.headers.get("authorization") or ""
        expected = f"Bearer {token}"
        if not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="MCP_AUTH_REQUIRED")

        payload = await request.json()
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return JSONResponse(
                _jsonrpc_error(payload.get("id") if isinstance(payload, dict) else None, -32600, "Invalid Request")
            )

        request_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}
        if method == "initialize":
            return _jsonrpc_result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "senex-gptrader-decision",
                        "version": "gptrader.decision.v1",
                    },
                },
            )
        if method == "notifications/initialized":
            return JSONResponse(status_code=202, content={})
        if method == "ping":
            return _jsonrpc_result(request_id, {})
        if method == "tools/list":
            return _jsonrpc_result(request_id, {"tools": _tools()})
        if method == "tools/call":
            if not isinstance(params, dict):
                return _jsonrpc_error(request_id, -32602, "Invalid params")
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _jsonrpc_error(request_id, -32602, "Invalid params")
            try:
                value = await _call_tool(service, name, arguments)
            except (
                DecisionValidationError,
                CursorMismatchError,
                ConflictingDecisionError,
                ValueError,
            ) as exc:
                return _jsonrpc_error(
                    request_id,
                    -32602,
                    type(exc).__name__,
                    data={"detail": str(exc)},
                )
            return _jsonrpc_result(request_id, _tool_result(value))

        return _jsonrpc_error(request_id, -32601, "Method not found")

    return app


def create_app_from_env() -> FastAPI:
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{TOKEN_ENV} is required")
    if len(token) < 32:
        raise ValueError("bearer token must be at least 32 characters")

    ingest_token = os.environ.get(INGEST_TOKEN_ENV)
    if not ingest_token:
        raise RuntimeError(f"{INGEST_TOKEN_ENV} is required")
    if len(ingest_token) < 32:
        raise ValueError("ingest bearer token must be at least 32 characters")

    return build_mcp_app(
        DecisionService(GPTraderStore()),
        token=token,
        ingest_token=ingest_token,
    )
