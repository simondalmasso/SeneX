from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from .task_protocol import TaskGate, task_gate

HttpGet = Callable[[str, dict[str, str], float], dict[str, Any]]
HttpPost = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]

DECISION_SCHEMA_VERSION = "gptrader.decision.v1"
MCP_URL_ENV = "SENEX_GPTRADER_MCP_URL"
MCP_TOKEN_ENV = "SENEX_GPTRADER_MCP_TOKEN"
PROVIDER_BASE_URL_ENV = "SENEX_DECISION_PROVIDER_BASE_URL"
PROVIDER_MODEL_ENV = "SENEX_DECISION_PROVIDER_MODEL"
PROVIDER_API_KEY_ENV = "SENEX_DECISION_PROVIDER_API_KEY"
PROVIDER_MAX_TOKENS_ENV = "SENEX_DECISION_PROVIDER_MAX_TOKENS"
BATCH_LIMIT_ENV = "SENEX_GPTRADER_BATCH_LIMIT"


class MCPClientError(RuntimeError):
    pass


class MCPUnavailable(MCPClientError):
    pass


class MCPProtocolError(MCPClientError):
    pass


class DecisionOutputError(ValueError):
    pass


class DecisionAdapter(Protocol):
    def decide(
        self,
        packets: Sequence[dict[str, Any]],
        *,
        run_id: str,
    ) -> Any: ...


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _default_http_get(
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    req = urllib_request.Request(
        url,
        headers=dict(headers),
        method="GET",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except (urllib_error.URLError, TimeoutError, OSError):
        raise MCPUnavailable("remote endpoint unavailable") from None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MCPProtocolError("remote endpoint returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise MCPProtocolError("remote endpoint returned non-object JSON")
    return value


def _default_http_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    body = _canonical(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        raise MCPUnavailable("remote endpoint unavailable") from None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MCPProtocolError("remote endpoint returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise MCPProtocolError("remote endpoint returned non-object JSON")
    return value


@dataclass(repr=False)
class MCPJSONRPCClient:
    endpoint: str
    token: str = field(repr=False)
    timeout: float = 15.0
    http_post: HttpPost = field(default=_default_http_post, repr=False)
    _request_id: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.endpoint = str(self.endpoint or "").strip()
        self.token = str(self.token or "")
        if not self.endpoint.startswith("https://"):
            raise ValueError("MCP endpoint must use https")
        if len(self.token) < 32:
            raise ValueError("MCP bearer token must be at least 32 characters")

    def __repr__(self) -> str:
        return (
            f"MCPJSONRPCClient(endpoint={self.endpoint!r}, "
            f"timeout={self.timeout!r}, token=<redacted>)"
        )

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        try:
            response = self.http_post(
                self.endpoint,
                {"Authorization": f"Bearer {self.token}"},
                payload,
                float(self.timeout),
            )
        except MCPClientError:
            raise
        except Exception as exc:
            raise MCPUnavailable("MCP request failed") from None
        if response.get("error") is not None:
            error = response.get("error")
            message = error.get("message") if isinstance(error, dict) else "MCP error"
            raise MCPProtocolError(str(message or "MCP error"))
        if "result" not in response:
            raise MCPProtocolError("MCP response missing result")
        return response["result"]

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._rpc(
            "tools/call",
            {"name": str(name), "arguments": dict(arguments)},
        )
        if not isinstance(result, dict):
            raise MCPProtocolError("tool result must be an object")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise MCPProtocolError("tool result missing structuredContent")
        return structured

    def get_gptrader_health(self) -> dict[str, Any]:
        return self._call_tool("get_gptrader_health", {})

    def get_prediction_batch(
        self,
        cursor: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        return self._call_tool(
            "get_prediction_batch",
            {"cursor": cursor, "limit": int(limit)},
        )

    def get_gptrader_state(self) -> dict[str, Any]:
        return self._call_tool("get_gptrader_state", {})

    def submit_paper_decisions(
        self,
        run_id: str,
        cursor: str,
        decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._call_tool(
            "submit_paper_decisions",
            {
                "run_id": str(run_id),
                "cursor": str(cursor),
                "decisions": decisions,
            },
        )


@dataclass(repr=False)
class DirectHTTPDecisionClient:
    endpoint: str
    token: str = field(repr=False)
    timeout: float = 15.0
    http_get: HttpGet = field(default=_default_http_get, repr=False)
    http_post: HttpPost = field(default=_default_http_post, repr=False)
    base_url: str = field(init=False)

    def __post_init__(self) -> None:
        endpoint = str(self.endpoint or "").strip().rstrip("/")
        if not endpoint.startswith("https://"):
            raise ValueError("Decision HTTP endpoint must use https")
        if endpoint.endswith("/mcp"):
            endpoint = endpoint[:-4]
        self.endpoint = endpoint
        self.base_url = endpoint.rstrip("/")
        self.token = str(self.token or "")
        if len(self.token) < 32:
            raise ValueError("Decision bearer token must be at least 32 characters")

    def __repr__(self) -> str:
        return (
            f"DirectHTTPDecisionClient(base_url={self.base_url!r}, "
            f"timeout={self.timeout!r}, token=<redacted>)"
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _get(self, path: str) -> dict[str, Any]:
        try:
            value = self.http_get(
                self.base_url + path,
                self._headers(),
                float(self.timeout),
            )
        except MCPClientError:
            raise
        except Exception:
            raise MCPUnavailable("Decision HTTP request failed") from None
        if not isinstance(value, dict):
            raise MCPProtocolError("Decision HTTP response must be an object")
        return value

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            value = self.http_post(
                self.base_url + path,
                self._headers(),
                payload,
                float(self.timeout),
            )
        except MCPClientError:
            raise
        except Exception:
            raise MCPUnavailable("Decision HTTP request failed") from None
        if not isinstance(value, dict):
            raise MCPProtocolError("Decision HTTP response must be an object")
        return value

    def get_gptrader_health(self) -> dict[str, Any]:
        return self._get("/v1/readiness")

    def get_prediction_batch(
        self,
        cursor: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        params: list[tuple[str, Any]] = []
        if cursor is not None:
            params.append(("cursor", str(cursor)))
        params.append(("limit", int(limit)))
        query = urllib_parse.urlencode(params)
        return self._get(f"/v1/predictions/next?{query}")

    def submit_paper_decisions(
        self,
        run_id: str,
        cursor: str,
        decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._post(
            "/v1/decisions",
            {
                "run_id": str(run_id),
                "cursor": str(cursor),
                "decisions": decisions,
            },
        )


_ALLOWED_MODEL_DECISION_KEYS = frozenset({"packet_id", "action", "reason_codes"})


def normalize_decisions(
    output: Any,
    packets: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(output, dict) or set(output) != {"decisions"}:
        raise DecisionOutputError("model output must contain only decisions")
    raw = output.get("decisions")
    if not isinstance(raw, list):
        raise DecisionOutputError("decisions must be a list")
    if len(raw) != len(packets):
        raise DecisionOutputError("decision cardinality must match packet batch")
    if not raw:
        raise DecisionOutputError("empty decision batch is not valid")

    normalized: list[dict[str, Any]] = []
    for packet, item in zip(packets, raw):
        if not isinstance(packet, dict):
            raise DecisionOutputError("packet must be an object")
        packet_id = str(packet.get("packet_id") or "").strip()
        if not packet_id:
            raise DecisionOutputError("packet_id missing from sealed packet")
        if not isinstance(item, dict):
            raise DecisionOutputError("decision item must be an object")
        unknown = set(item) - _ALLOWED_MODEL_DECISION_KEYS
        if unknown:
            raise DecisionOutputError(
                "forbidden or unknown decision field(s): "
                + ",".join(sorted(str(key) for key in unknown))
            )
        supplied_packet_id = str(item.get("packet_id") or "").strip()
        if supplied_packet_id != packet_id:
            raise DecisionOutputError("decision order must match packet prefix")
        action = str(item.get("action") or "").upper().strip()
        if action not in {"TAKE", "ABSTAIN"}:
            raise DecisionOutputError("action must be TAKE or ABSTAIN")
        reasons = item.get("reason_codes")
        if not isinstance(reasons, list) or len(reasons) > 8:
            raise DecisionOutputError("reason_codes must contain at most 8 items")
        clean_reasons: list[str] = []
        for reason in reasons:
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 64:
                raise DecisionOutputError("invalid reason code")
            clean_reasons.append(reason.strip())

        idem_material = {
            "packet_id": packet_id,
            "action": action,
            "reason_codes": clean_reasons,
        }
        idem = "ext-v1-" + hashlib.sha256(
            _canonical(idem_material).encode("utf-8")
        ).hexdigest()[:48]
        normalized.append(
            {
                "packet_id": packet_id,
                "action": action,
                "reason_codes": clean_reasons,
                "idempotency_key": idem,
            }
        )
    return normalized


@dataclass(repr=False)
class OpenAICompatibleDecisionAdapter:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout: float = 30.0
    max_tokens: int | None = None
    http_post: HttpPost = field(default=_default_http_post, repr=False)

    def __post_init__(self) -> None:
        self.base_url = str(self.base_url or "").rstrip("/")
        self.model = str(self.model or "").strip()
        self.api_key = str(self.api_key or "")
        if not self.base_url.startswith("https://"):
            raise ValueError("LLM base URL must use https")
        if not self.model:
            raise ValueError("LLM model is required")
        if not self.api_key:
            raise ValueError("LLM API key is required")
        if self.max_tokens is not None:
            if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int):
                raise ValueError("LLM max_tokens must be an integer")
            if not 1 <= self.max_tokens <= 1_048_576:
                raise ValueError("LLM max_tokens must be between 1 and 1048576")

    def __repr__(self) -> str:
        return (
            f"OpenAICompatibleDecisionAdapter(base_url={self.base_url!r}, "
            f"model={self.model!r}, timeout={self.timeout!r}, api_key=<redacted>)"
        )

    def decide(
        self,
        packets: Sequence[dict[str, Any]],
        *,
        run_id: str,
    ) -> Any:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a PAPER-only decision filter. "
                        "For each sealed T0 packet return exactly one TAKE or ABSTAIN. "
                        "Never change or infer a new direction; SENEX owns direction. "
                        "Return strict JSON only with shape "
                        '{"decisions":[{"packet_id":"...","action":"TAKE|ABSTAIN",'
                        '"reason_codes":["..."]}]}. '
                        "Do not add fields."
                    ),
                },
                {
                    "role": "user",
                    "content": _canonical(
                        {
                            "sealed_t0_packets": list(packets),
                        }
                    ),
                },
            ],
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = int(self.max_tokens)
        try:
            response = self.http_post(
                self.base_url + "/chat/completions",
                {"Authorization": f"Bearer {self.api_key}"},
                payload,
                float(self.timeout),
            )
        except MCPClientError:
            raise
        except Exception as exc:
            raise MCPUnavailable("decision provider unavailable") from None

        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DecisionOutputError("provider response missing message content") from exc
        if not isinstance(content, str):
            raise DecisionOutputError("provider message content must be text")
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise DecisionOutputError("provider message is not strict JSON") from exc
        return value


@dataclass
class ExternalDecisionClient:
    mcp: Any
    adapter: DecisionAdapter
    batch_limit: int = 8

    def __post_init__(self) -> None:
        if isinstance(self.batch_limit, bool) or not isinstance(self.batch_limit, int):
            raise ValueError("batch_limit must be an integer")
        if not 1 <= self.batch_limit <= 16:
            raise ValueError("batch_limit must be between 1 and 16")

    def run_once(
        self,
        *,
        run_id: str,
        shadow_only: bool = False,
    ) -> dict[str, Any]:
        run_id = str(run_id or "").strip()
        if not run_id or len(run_id) > 160:
            raise ValueError("bounded run_id is required")

        try:
            health = self.mcp.get_gptrader_health()
        except MCPUnavailable:
            return {
                "gate": TaskGate.WAIT_MCP.value,
                "submitted": False,
                "applied": 0,
            }

        gate = task_gate(health if isinstance(health, dict) else None)
        if gate is not TaskGate.READY:
            return {
                "gate": gate.value,
                "submitted": False,
                "applied": 0,
            }
        if health.get("schema_version") != DECISION_SCHEMA_VERSION:
            return {
                "gate": TaskGate.SAFETY_BLOCK.value,
                "submitted": False,
                "applied": 0,
            }

        cursor = health.get("cursor")
        try:
            batch = self.mcp.get_prediction_batch(
                cursor=cursor,
                limit=self.batch_limit,
            )
        except MCPUnavailable:
            return {
                "gate": TaskGate.WAIT_MCP.value,
                "submitted": False,
                "applied": 0,
            }
        if not isinstance(batch, dict):
            raise MCPProtocolError("prediction batch must be an object")
        packets = batch.get("packets")
        cursor_in = batch.get("cursor_in")
        if not isinstance(packets, list):
            raise MCPProtocolError("prediction batch missing packets")
        if not isinstance(cursor_in, str) or not cursor_in:
            raise MCPProtocolError("prediction batch missing cursor_in")
        if not packets:
            return {
                "gate": TaskGate.READY.value,
                "submitted": False,
                "applied": 0,
                "cursor": cursor_in,
            }

        raw_decisions = self.adapter.decide(packets, run_id=run_id)
        decisions = normalize_decisions(raw_decisions, packets)
        if shadow_only:
            return {
                "gate": TaskGate.READY.value,
                "submitted": False,
                "applied": 0,
                "shadow_only": True,
                "cursor": cursor_in,
                "decisions": decisions,
            }
        result = self.mcp.submit_paper_decisions(
            run_id,
            cursor_in,
            decisions,
        )
        if not isinstance(result, dict):
            raise MCPProtocolError("submit result must be an object")
        return {
            "gate": TaskGate.READY.value,
            "submitted": True,
            "applied": int(result.get("applied") or 0),
            "duplicate": bool(result.get("duplicate")),
            "cursor": result.get("cursor"),
        }



def _required_env(environ: Mapping[str, str], name: str) -> str:
    value = str(environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def create_external_client_from_env(
    environ: Mapping[str, str] | None = None,
) -> ExternalDecisionClient:
    env = os.environ if environ is None else environ
    batch_raw = str(env.get(BATCH_LIMIT_ENV) or "8").strip()
    try:
        batch_limit = int(batch_raw)
    except ValueError:
        raise ValueError(f"{BATCH_LIMIT_ENV} must be an integer") from None

    mcp = DirectHTTPDecisionClient(
        _required_env(env, MCP_URL_ENV),
        token=_required_env(env, MCP_TOKEN_ENV),
    )
    max_tokens_raw = str(env.get(PROVIDER_MAX_TOKENS_ENV) or "").strip()
    if max_tokens_raw:
        try:
            provider_max_tokens = int(max_tokens_raw)
        except ValueError:
            raise ValueError(
                f"{PROVIDER_MAX_TOKENS_ENV} must be an integer"
            ) from None
    else:
        provider_max_tokens = None

    adapter = OpenAICompatibleDecisionAdapter(
        base_url=_required_env(env, PROVIDER_BASE_URL_ENV),
        model=_required_env(env, PROVIDER_MODEL_ENV),
        api_key=_required_env(env, PROVIDER_API_KEY_ENV),
        max_tokens=provider_max_tokens,
    )
    return ExternalDecisionClient(
        mcp=mcp,
        adapter=adapter,
        batch_limit=batch_limit,
    )


def _generated_run_id() -> str:
    now = datetime.now(timezone.utc)
    return "EXT_" + now.strftime("%Y%m%dT%H%M%S%fZ")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one fail-closed GPTrader PAPER decision batch."
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--shadow-only",
        action="store_true",
        help="Evaluate one sealed packet batch without submitting decisions.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    run_id = str(args.run_id or "").strip() or _generated_run_id()

    try:
        client = create_external_client_from_env()
        if args.shadow_only:
            result = client.run_once(
                run_id=run_id,
                shadow_only=True,
            )
        else:
            result = client.run_once(run_id=run_id)
    except Exception as exc:
        print(_canonical({"error": type(exc).__name__, "status": "ERROR"}))
        return 70

    print(_canonical(result))
    if result.get("gate") == TaskGate.WAIT_MCP.value:
        return 75
    if result.get("gate") == TaskGate.SAFETY_BLOCK.value:
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
