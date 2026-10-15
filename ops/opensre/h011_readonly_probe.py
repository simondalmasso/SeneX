#!/usr/bin/env python3
"""Collect a bounded, GET-only SENEX H011 snapshot for OpenSRE review.

No credentials, account endpoints, mutating routes or remediation actions are used.
Redirects are rejected, and the only permitted host is the public H011 runtime.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_BASE_URL = "https://h011-web--senecio-h011--wbjggn89fnf8.code.run"
_ALLOWED_HOST = urlsplit(DEFAULT_BASE_URL).hostname
READ_ONLY_METHODS = {"get", "head", "options"}
PROBES = (
    ("healthz", "/healthz"),
    ("readyz", "/readyz"),
    ("provenance", "/api/runtime/provenance"),
    ("oracle_score", "/api/oracle/score?symbol=BTCUSDT"),
    ("paper_live_gate", "/api/portfolio/live_gate?symbol=BTCUSDT"),
    ("openapi", "/openapi.json"),
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def validate_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    if (
        parts.scheme != "https"
        or parts.hostname != _ALLOWED_HOST
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError("base URL must be the allowlisted HTTPS H011 public host")
    return f"https://{_ALLOWED_HOST}"


def _summary(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "healthz": ("status", "alive", "service"),
        "readyz": (
            "status", "ready", "readiness", "paper_control_ready",
            "authority_readiness", "paper_lock", "orders_disabled",
            "provenance_exact", "snapshot_fresh",
        ),
        "provenance": (
            "exact", "source_commit", "source_tree", "build_digest",
            "manifest_exact", "commit_exact", "tree_exact",
            "build_digest_matches_runtime_files",
        ),
        "oracle_score": (
            "score_status", "status", "independent_1h_rows",
            "proof_qualified_rows_raw", "edge_evidence",
        ),
        "paper_live_gate": (
            "status", "orders_enabled", "live_capital_locked",
            "hard_paper_lock", "trade_mode", "reasons",
        ),
    }
    if name == "openapi":
        paths = payload.get("paths")
        operations: list[str] = []
        if isinstance(paths, dict):
            for route, methods in sorted(paths.items()):
                if not isinstance(methods, dict):
                    continue
                for method in sorted(methods):
                    if method.lower() in {"get", "head", "options", "post", "put", "patch", "delete"}:
                        operations.append(f"{method.upper()} {route}")
        return {
            "operation_count": len(operations),
            "operations": operations,
            "non_read_only_operations": [
                op for op in operations if op.split(" ", 1)[0].lower() not in READ_ONLY_METHODS
            ],
        }
    result = {key: payload[key] for key in fields.get(name, ()) if key in payload}
    for container_name, names in (
        ("safety", ("trade_mode", "orders_enabled", "live_capital_locked", "hard_paper_lock")),
        ("paper_control", ("status", "restored", "paper_lock", "orders_disabled")),
    ):
        container = payload.get(container_name)
        if isinstance(container, dict):
            for key in names:
                if key in container and key not in result:
                    result[f"{container_name}_{key}"] = container[key]
    return result


def _fetch_json(opener: Any, url: str, timeout: float) -> tuple[int | None, dict[str, Any] | None, str | None]:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "SENEX-OpenSRE-ReadOnly-Probe/1"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            status, raw = int(response.status), response.read()
    except HTTPError as exc:
        status = int(exc.code)
        try:
            raw = exc.read()
        except Exception:
            raw = b""
    except (URLError, TimeoutError, OSError) as exc:
        return None, None, type(exc).__name__
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None, "INVALID_JSON"
    if not isinstance(payload, dict):
        return status, None, "JSON_NOT_OBJECT"
    return status, payload, None


_MISSING = object()


def _find_key(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_key(child, key)
            if found is not _MISSING:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, key)
            if found is not _MISSING:
                return found
    return _MISSING


def read_snapshot(base_url: str = DEFAULT_BASE_URL, *, timeout: float = 8.0, opener: Any = None) -> dict[str, Any]:
    base = validate_base_url(base_url)
    if not 1.0 <= timeout <= 30.0:
        raise ValueError("timeout must be between 1 and 30 seconds")
    client = opener if opener is not None else build_opener(_NoRedirect())
    probes: dict[str, Any] = {}
    raw_payloads: dict[str, dict[str, Any]] = {}
    for name, path in PROBES:
        status, payload, error = _fetch_json(client, base + path, timeout)
        probes[name] = {
            "http_status": status,
            "ok": status == 200 and error is None and payload is not None,
            "summary": _summary(name, payload) if isinstance(payload, dict) else {},
        }
        if error is not None:
            probes[name]["error_class"] = error
        if isinstance(payload, dict):
            raw_payloads[name] = payload

    def recursive_value(key: str) -> Any:
        for payload in raw_payloads.values():
            found = _find_key(payload, key)
            if found is not _MISSING:
                return found
        return _MISSING

    all_probes_ok = all(probes[n]["ok"] for n in ("healthz", "readyz", "provenance", "oracle_score", "paper_live_gate", "openapi"))
    non_read_only = probes["openapi"]["summary"].get("non_read_only_operations")
    checks = {
        "all_public_probes_http_200": all_probes_ok,
        "provenance_exact": recursive_value("exact") is True,
        "hard_paper_lock": recursive_value("hard_paper_lock") is True,
        "orders_disabled": recursive_value("orders_enabled") is False,
        "live_capital_locked": recursive_value("live_capital_locked") is True,
        "trade_mode_paper": recursive_value("trade_mode") == "PAPER",
        "public_api_read_only": isinstance(non_read_only, list) and not non_read_only,
    }
    safe = all(checks.values())
    return {
        "schema": "senex.opensre.readonly.v1",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "mode": "READ_ONLY",
        "base_url": base,
        "status": "HEALTHY" if safe else "DEGRADED_OR_INCOMPLETE",
        "checks": checks,
        "probes": probes,
        "security": {
            "http_method_used": "GET",
            "redirects_followed": False,
            "account_endpoints_called": 0,
            "order_endpoints_called": 0,
            "credentials_loaded": False,
            "remediation_performed": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args(argv)
    try:
        report = read_snapshot(args.base_url, timeout=args.timeout)
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, sort_keys=True, indent=2)
    print(rendered if args.json else f"SENEX H011 {report['status']}\n{rendered}")
    return 0 if report["status"] == "HEALTHY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
