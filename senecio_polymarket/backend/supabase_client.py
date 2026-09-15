"""SENEX R7B runtime overlay candidate for backend.supabase_client.

NON-PRODUCTION CANDIDATE. Baseline authority is the byte-exact R7A runtime.
This overlay preserves the public function signatures used by H011 while
replacing recurrent full-history / exact-count scans with a provenance-bound
sealed authority base plus bounded deltas and mutable-row refreshes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from . import authority_seal as durable_seal
from .artifact_identity import ArtifactIdentityError, internal_identity_projection

log = logging.getLogger("senecio.supabase")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_TABLE = os.environ.get("SUPABASE_TABLE", "oracle_predictions")


def _require_config() -> tuple[str, str]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be provided by the runtime environment")
    return SUPABASE_URL, SUPABASE_KEY


def build_supabase_headers(
    supabase_key: str,
    *,
    prefer: str = "return=representation",
) -> dict[str, str]:
    if not supabase_key:
        raise RuntimeError("SUPABASE_KEY must be provided by the runtime environment")
    headers = {
        "apikey": supabase_key,
        "Content-Type": "application/json",
        "Prefer": prefer,
    }
    if supabase_key.startswith("eyJ") and supabase_key.count(".") == 2:
        headers["Authorization"] = f"Bearer {supabase_key}"
    return headers


_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    supabase_url, supabase_key = _require_config()
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=f"{supabase_url}/rest/v1",
            headers=build_supabase_headers(supabase_key),
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
    return _client


class D1QuotaExceededError(RuntimeError):
    """Deterministic D1 daily row-read quota exhaustion; retry only after reset."""


class D1TransientUnavailableError(RuntimeError):
    """Temporary D1 unavailability while the short breaker is open."""


D1_TRANSIENT_FAILURE_THRESHOLD = 3
D1_TRANSIENT_BACKOFF_BASE_SEC = 30
D1_TRANSIENT_BACKOFF_MAX_SEC = 300

_d1_quota_breaker: dict[str, Any] = {
    "opened_at": None,
    "open_until": None,
    "reason": None,
    "network_calls": 0,
    "suppressed_calls": 0,
}

_d1_transient_breaker: dict[str, Any] = {
    "opened_at": None,
    "open_until": None,
    "reason": None,
    "consecutive_failures": 0,
    "backoff_seconds": D1_TRANSIENT_BACKOFF_BASE_SEC,
    "open_count": 0,
    "suppressed_calls": 0,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _next_d1_reset(now: datetime | None = None) -> datetime:
    current = (now or _now_utc()).astimezone(timezone.utc)
    tomorrow = current.date().toordinal() + 1
    reset_date = datetime.fromordinal(tomorrow).date()
    return datetime(reset_date.year, reset_date.month, reset_date.day, tzinfo=timezone.utc)


def _is_d1_quota_response(response: Any) -> bool:
    status = int(getattr(response, "status_code", 0) or 0)
    text = str(getattr(response, "text", "") or "").lower()
    markers = (
        "daily row read limit",
        "free tier daily row read",
        "d1 quota",
        "quota exceeded",
        "exceeded d1",
        "exceeded d1's free tier daily row read limit",
    )
    return status == 429 or any(marker in text for marker in markers)


def _quota_breaker_status(now: datetime | None = None) -> dict[str, Any]:
    current = (now or _now_utc()).astimezone(timezone.utc)
    raw_until = _d1_quota_breaker.get("open_until")
    open_until = datetime.fromisoformat(raw_until) if isinstance(raw_until, str) and raw_until else None
    is_open = bool(open_until is not None and current < open_until)
    if open_until is not None and not is_open:
        _d1_quota_breaker["opened_at"] = None
        _d1_quota_breaker["open_until"] = None
        _d1_quota_breaker["reason"] = None
        open_until = None
    return {
        "open": is_open,
        "opened_at": _d1_quota_breaker.get("opened_at"),
        "open_until": open_until.isoformat() if open_until is not None else None,
        "reason": _d1_quota_breaker.get("reason"),
        "network_calls": int(_d1_quota_breaker.get("network_calls") or 0),
        "suppressed_calls": int(_d1_quota_breaker.get("suppressed_calls") or 0),
    }


def get_d1_quota_breaker_status() -> dict[str, Any]:
    return _quota_breaker_status()


def _transient_breaker_status(now: datetime | None = None) -> dict[str, Any]:
    current = (now or _now_utc()).astimezone(timezone.utc)
    raw_until = _d1_transient_breaker.get("open_until")
    open_until = datetime.fromisoformat(raw_until) if isinstance(raw_until, str) and raw_until else None
    is_open = bool(open_until is not None and current < open_until)
    if open_until is not None and not is_open:
        _d1_transient_breaker["opened_at"] = None
        _d1_transient_breaker["open_until"] = None
        _d1_transient_breaker["reason"] = None
        _d1_transient_breaker["consecutive_failures"] = 0
        open_until = None
    return {
        "open": is_open,
        "opened_at": _d1_transient_breaker.get("opened_at"),
        "open_until": open_until.isoformat() if open_until is not None else None,
        "reason": _d1_transient_breaker.get("reason"),
        "consecutive_failures": int(_d1_transient_breaker.get("consecutive_failures") or 0),
        "backoff_seconds": int(_d1_transient_breaker.get("backoff_seconds") or D1_TRANSIENT_BACKOFF_BASE_SEC),
        "suppressed_calls": int(_d1_transient_breaker.get("suppressed_calls") or 0),
    }



def _reset_d1_transient_breaker() -> None:
    _d1_transient_breaker.update({
        "opened_at": None,
        "open_until": None,
        "reason": None,
        "consecutive_failures": 0,
        "backoff_seconds": D1_TRANSIENT_BACKOFF_BASE_SEC,
        "open_count": 0,
    })


def _open_d1_transient_breaker(now: datetime | None = None) -> dict[str, Any]:
    current = (now or _now_utc()).astimezone(timezone.utc)
    open_count = int(_d1_transient_breaker.get("open_count") or 0)
    backoff = min(D1_TRANSIENT_BACKOFF_BASE_SEC * (2 ** open_count), D1_TRANSIENT_BACKOFF_MAX_SEC)
    _d1_transient_breaker["opened_at"] = current.isoformat()
    _d1_transient_breaker["open_until"] = (current + timedelta(seconds=backoff)).isoformat()
    _d1_transient_breaker["reason"] = "D1_TRANSIENT_UNAVAILABLE"
    _d1_transient_breaker["backoff_seconds"] = backoff
    _d1_transient_breaker["open_count"] = open_count + 1
    return _transient_breaker_status(current)


def _record_d1_transient_failure(reason: str) -> dict[str, Any]:
    count = int(_d1_transient_breaker.get("consecutive_failures") or 0) + 1
    _d1_transient_breaker["consecutive_failures"] = count
    _d1_transient_breaker["reason"] = reason
    if count >= D1_TRANSIENT_FAILURE_THRESHOLD:
        return _open_d1_transient_breaker()
    return _transient_breaker_status()


def _open_d1_quota_breaker(now: datetime | None = None) -> dict[str, Any]:
    current = (now or _now_utc()).astimezone(timezone.utc)
    reset = _next_d1_reset(current)
    _d1_quota_breaker["opened_at"] = current.isoformat()
    _d1_quota_breaker["open_until"] = reset.isoformat()
    _d1_quota_breaker["reason"] = "D1_QUOTA_EXCEEDED"
    return _quota_breaker_status(current)


async def _d1_get(client: Any, path: str, **kwargs: Any) -> Any:
    quota_status = _quota_breaker_status()
    if quota_status["open"]:
        _d1_quota_breaker["suppressed_calls"] = int(_d1_quota_breaker.get("suppressed_calls") or 0) + 1
        raise D1QuotaExceededError(
            f"D1_QUOTA_EXCEEDED;retry_at={quota_status['open_until']};network_call_suppressed=true"
        )
    transient_status = _transient_breaker_status()
    if transient_status["open"]:
        _d1_transient_breaker["suppressed_calls"] = int(_d1_transient_breaker.get("suppressed_calls") or 0) + 1
        raise D1TransientUnavailableError(
            f"D1_TRANSIENT_UNAVAILABLE;retry_at={transient_status['open_until']};network_call_suppressed=true"
        )
    _d1_quota_breaker["network_calls"] = int(_d1_quota_breaker.get("network_calls") or 0) + 1
    try:
        response = await client.get(path, **kwargs)
    except httpx.TransportError as exc:
        _record_d1_transient_failure(f"TRANSPORT:{type(exc).__name__}")
        raise
    if _is_d1_quota_response(response):
        _reset_d1_transient_breaker()
        opened = _open_d1_quota_breaker()
        raise D1QuotaExceededError(
            f"D1_QUOTA_EXCEEDED;retry_at={opened['open_until']};network_call_suppressed=false"
        )
    response_status = int(getattr(response, "status_code", 0) or 0)
    if 500 <= response_status <= 599:
        _record_d1_transient_failure(f"HTTP_{response_status}")
    else:
        _reset_d1_transient_breaker()
    return response


async def insert_prediction(prediction: dict) -> Optional[dict]:
    row = {
        "ts": prediction.get("timestamp"),
        "symbol": prediction.get("symbol"),
        "prediction": prediction.get("prediction"),
        "confidence": float(prediction.get("confidence", 0)),
        "ev": float(prediction.get("ev", 0)),
        "price_now": float(prediction.get("price_now", 0)),
        "price_15m_later": prediction.get("price_15m_later"),
        "outcome": prediction.get("outcome"),
        "exchange_used": prediction.get("exchange_used", "unknown"),
        "audit": prediction.get("_audit"),
    }
    try:
        c = _get_client()
        r = await c.post(f"/{SUPABASE_TABLE}", json=row)
        if r.status_code in (200, 201):
            data = r.json()
            if isinstance(data, list) and data:
                log.info("supabase insert OK id=%s", data[0].get("id"))
                return data[0]
            return data
        log.error("supabase insert failed: %s %s", r.status_code, r.text[:300])
        return None
    except Exception as e:
        log.error("supabase insert error: %s", e)
        return None


async def fetch_predictions_strict(limit: int = 50, symbol: Optional[str] = None) -> list[dict]:
    """Bounded newest-first query that preserves upstream failure semantics."""
    c = _get_client()
    params = {"limit": str(limit), "order": "ts.desc"}
    if symbol:
        params["symbol"] = f"eq.{symbol}"
    r = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
    if r.status_code != 200:
        raise RuntimeError(_d1_failure("SUPABASE_FETCH", r))
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("SUPABASE_FETCH_RESPONSE_NOT_LIST")
    return data


async def fetch_predictions(limit: int = 50, symbol: Optional[str] = None) -> list[dict]:
    """Bounded diagnostic/read-model query; legacy callers degrade failures to empty."""
    try:
        return await fetch_predictions_strict(limit=limit, symbol=symbol)
    except Exception as e:
        log.error("supabase fetch error: %s", e)
        return []


# ---------------------------------------------------------------------------
# R7B incremental authority overlay
# ---------------------------------------------------------------------------
AUTHORITY_HISTORY_PAGE_SIZE_MAX = 500
AUTHORITY_HISTORY_MAX_PAGES = 10_000
AUTHORITY_DELTA_PAGE_SIZE_MAX = max(1, min(100, int(os.environ.get("SENEX_AUTHORITY_DELTA_PAGE_SIZE_MAX", "50"))))
AUTHORITY_DELTA_MAX_PAGES = max(1, min(4, int(os.environ.get("SENEX_AUTHORITY_DELTA_MAX_PAGES", "2"))))
AUTHORITY_MUTABLE_ID_BATCH = max(1, min(50, int(os.environ.get("SENEX_AUTHORITY_MUTABLE_ID_BATCH", "50"))))
AUTHORITY_MUTABLE_ID_MAX = max(1, min(200, int(os.environ.get("SENEX_AUTHORITY_MUTABLE_ID_MAX", "200"))))
AUTHORITY_SEAL_MAX_AGE_S = max(300, int(os.environ.get("SENEX_AUTHORITY_SEAL_MAX_AGE_SEC", str(7 * 24 * 3600))))
AUTHORITY_RUNTIME_SEAL_CONTRACT = "senex-authority-runtime-seal-v1"
AUTHORITY_MUTATION_CONTRACT = "APPEND_ROWS_AND_MUTATE_DIRECTIONAL_ONLY_UNTIL_PROOF_QUALIFIED"
AUTHORITY_HISTORY_SELECT = (
    "id,ts,symbol,prediction,confidence,price_now,outcome,exchange_used,"
    "origin_price_v1:audit->origin_price_v1,"
    "outcomes_dual:audit->outcomes_dual"
)

_authority_symbol_state: dict[str, dict[str, Any]] = {}
_exact_count_state: dict[str, Any] = {
    "bootstrapped": False,
    "count": None,
    "cursor": None,
    "bootstrap_count_calls": 0,
}
_r7b_diagnostics: dict[str, Any] = {
    "authority_bootstrap_requests": 0,
    "authority_bootstrap_rows": 0,
    "authority_delta_requests": 0,
    "authority_mutable_lookup_requests": 0,
    "exact_count_bootstrap_calls": 0,
    "exact_count_delta_requests": 0,
    "pending_exact_count_calls": 0,
    "durable_authority_loads": 0,
    "durable_authority_writes": 0,
    "durable_count_loads": 0,
    "durable_count_writes": 0,
    "bootstrap_guard_writes": 0,
}


def _normalize_symbol(value: Any) -> str:
    return str(value or "").upper().replace("/", "").replace("-", "").strip()


def _new_exact_count_state() -> dict[str, Any]:
    return {"bootstrapped": False, "count": None, "cursor": None, "bootstrap_count_calls": 0}


_scoped_exact_count_state: dict[str, dict[str, Any]] = {}


def _count_scope(symbol: Any = None) -> str:
    normalized = _normalize_symbol(symbol)
    return "GLOBAL_EXACT_COUNT" if not normalized else f"{normalized}_EXACT_COUNT"


def _count_state_for(symbol: Any = None) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    if not normalized:
        return _exact_count_state
    return _scoped_exact_count_state.setdefault(normalized, _new_exact_count_state())


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def _runtime_identity() -> dict[str, str]:
    """Return the one fail-closed internal artifact identity used by authority state."""
    try:
        identity = internal_identity_projection()
    except ArtifactIdentityError as exc:
        raise AuthorityHistoryIncompleteError("AUTHORITY_RUNTIME_PROVENANCE_INCOMPLETE") from exc
    return {str(key): str(value) for key, value in identity.items()}


def _cursor_tuple(value: Any) -> tuple[str, str] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        ts, row_id = str(value.get("ts") or ""), str(value.get("id") or "")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        ts, row_id = str(value[0] or ""), str(value[1] or "")
    else:
        return None
    return (ts, row_id) if ts and row_id else None


def _cursor_dict(value: tuple[str, str] | None) -> dict[str, str] | None:
    return {"ts": value[0], "id": value[1]} if value else None


def _cursor_order_key(value: tuple[str, str] | None) -> tuple[Any, ...]:
    if value is None:
        return ("", (0, -1))
    ts, row_id = value
    try:
        id_key: Any = (0, int(row_id))
    except Exception:
        id_key = (1, str(row_id))
    return str(ts), id_key


def _cursor_gt(left: tuple[str, str], right: tuple[str, str] | None) -> bool:
    return right is None or _cursor_order_key(left) > _cursor_order_key(right)


def _row_cursor(row: dict[str, Any]) -> tuple[str, str]:
    ts = str(row.get("ts") or "")
    row_id = str(row.get("id") or "")
    if not ts or not row_id:
        raise AuthorityHistoryIncompleteError("AUTHORITY_HISTORY_CURSOR_FIELD_MISSING")
    return ts, row_id


def _authority_id_floor(rows: dict[str, dict[str, Any]]) -> int:
    floor = 0
    for row in rows.values():
        try:
            row_id = int(_row_cursor(row)[1])
        except Exception as exc:
            raise AuthorityHistoryIncompleteError("AUTHORITY_HISTORY_ID_NONINTEGER") from exc
        if row_id <= 0:
            raise AuthorityHistoryIncompleteError("AUTHORITY_HISTORY_ID_NONPOSITIVE")
        floor = max(floor, row_id)
    return floor


def _authority_temporal_cursor(rows: dict[str, dict[str, Any]]) -> tuple[str, str] | None:
    if not rows:
        return None
    return max((_row_cursor(row) for row in rows.values()), key=_cursor_order_key)


def _authority_row_from_projection(row: dict[str, Any]) -> dict[str, Any]:
    projected = dict(row)
    origin = projected.pop("origin_price_v1", None)
    dual = projected.pop("outcomes_dual", None)
    if origin is None and dual is None and isinstance(projected.get("audit"), dict):
        audit = projected["audit"]
        projected["audit"] = {
            key: audit[key] for key in ("origin_price_v1", "outcomes_dual") if key in audit
        }
        return projected
    audit: dict[str, Any] = {}
    if isinstance(origin, dict):
        audit["origin_price_v1"] = origin
    if isinstance(dual, dict):
        audit["outcomes_dual"] = dual
    projected["audit"] = audit
    return projected


class AuthorityHistoryIncompleteError(RuntimeError):
    """Complete authority cannot be proven from bootstrap seal + bounded live evidence."""


class ExactCountUnavailableError(RuntimeError):
    """Exact visible count cannot be proven under the code-proven no-delete writer contract."""


def _state_key(symbol: str | None) -> str:
    normalized = _normalize_symbol(symbol)
    return normalized or "*"


def _runtime_seal_hash(value: Any) -> str:
    raw = str(os.environ.get("SENEX_AUTHORITY_SEAL_KEY") or "")
    if not raw:
        return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()
    if len(raw) < 32:
        raise AuthorityHistoryIncompleteError("AUTHORITY_SEAL_KEY_TOO_SHORT")
    digest = hmac.new(raw.encode("utf-8"), _canonical_json(value), hashlib.sha256).hexdigest()
    return "hmac-sha256:" + digest


def _seal_state(key: str, rows: dict[str, dict[str, Any]], cursor: tuple[str, str] | None) -> dict[str, Any]:
    identity = _runtime_identity()
    ordered = sorted(rows.values(), key=lambda row: _cursor_order_key(_row_cursor(row)))
    payload = {
        "contract": AUTHORITY_RUNTIME_SEAL_CONTRACT,
        "scope": key,
        "runtime_identity": identity,
        "writer_mutation_contract": AUTHORITY_MUTATION_CONTRACT,
        "coverage_cursor": _cursor_dict(cursor),
        "row_count": len(ordered),
        "authority_rows_sha256": "sha256:" + hashlib.sha256(_canonical_json(ordered)).hexdigest(),
    }
    payload["seal_hash"] = _runtime_seal_hash(payload)
    return payload


def _validate_state_metadata(key: str, state: dict[str, Any]) -> None:
    seal = state.get("seal")
    rows = state.get("rows")
    cursor = state.get("cursor")
    if not isinstance(seal, dict) or not isinstance(rows, dict):
        raise AuthorityHistoryIncompleteError("AUTHORITY_RUNTIME_SEAL_MISSING")
    if seal.get("contract") != AUTHORITY_RUNTIME_SEAL_CONTRACT:
        raise AuthorityHistoryIncompleteError("AUTHORITY_RUNTIME_SEAL_CONTRACT_INVALID")
    if seal.get("scope") != key:
        raise AuthorityHistoryIncompleteError("AUTHORITY_RUNTIME_SEAL_SCOPE_MISMATCH")
    if seal.get("runtime_identity") != _runtime_identity():
        raise AuthorityHistoryIncompleteError("AUTHORITY_RUNTIME_SEAL_PROVENANCE_MISMATCH")
    if seal.get("writer_mutation_contract") != AUTHORITY_MUTATION_CONTRACT:
        raise AuthorityHistoryIncompleteError("AUTHORITY_RUNTIME_SEAL_MUTATION_CONTRACT_INVALID")
    if int(seal.get("row_count", -1)) != len(rows):
        raise AuthorityHistoryIncompleteError("AUTHORITY_RUNTIME_SEAL_ROW_COUNT_MISMATCH")
    if _cursor_tuple(seal.get("coverage_cursor")) != cursor:
        raise AuthorityHistoryIncompleteError("AUTHORITY_RUNTIME_SEAL_CURSOR_MISMATCH")
    ordered = sorted(rows.values(), key=lambda row: _cursor_order_key(_row_cursor(row)))
    expected_rows_hash = "sha256:" + hashlib.sha256(_canonical_json(ordered)).hexdigest()
    if seal.get("authority_rows_sha256") != expected_rows_hash:
        raise AuthorityHistoryIncompleteError("AUTHORITY_RUNTIME_SEAL_ROWS_HASH_MISMATCH")
    supplied_hash = str(seal.get("seal_hash") or "")
    seal_payload = dict(seal)
    seal_payload.pop("seal_hash", None)
    expected_seal_hash = _runtime_seal_hash(seal_payload)
    if not hmac.compare_digest(supplied_hash, expected_seal_hash):
        raise AuthorityHistoryIncompleteError("AUTHORITY_RUNTIME_SEAL_HASH_MISMATCH")


def _d1_failure(prefix: str, response: Any) -> str:
    status = int(getattr(response, "status_code", 0) or 0)
    text = str(getattr(response, "text", "") or "").lower()
    quota_markers = (
        "daily row read limit",
        "free tier daily row read",
        "d1 quota",
        "quota exceeded",
        "exceeded d1",
    )
    if status == 429 or any(marker in text for marker in quota_markers):
        return f"{prefix}:D1_QUOTA_EXCEEDED"
    return f"{prefix}_HTTP_{status}"


async def _keyset_page(
    symbol: str | None,
    cursor: tuple[str, str] | None,
    *,
    select: str,
    limit: int,
    diagnostics_key: str,
    error_prefix: str,
) -> list[dict[str, Any]]:
    """Fetch one lexicographic (ts,id) page using index-searchable predicates.

    A cursor continuation is deliberately split into (same ts,id>cursor) and
    (ts>cursor) requests. This avoids the OR predicate that can degrade a
    composite-index range search into a symbol-prefix history walk.
    """
    bounded = max(1, int(limit))
    normalized = _normalize_symbol(symbol)
    c = _get_client()

    async def request(params: dict[str, str]) -> list[dict[str, Any]]:
        if normalized:
            params["symbol"] = f"eq.{normalized}"
        _r7b_diagnostics[diagnostics_key] += 1
        try:
            response = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
        except D1QuotaExceededError as exc:
            raise AuthorityHistoryIncompleteError(f"{error_prefix}:D1_QUOTA_EXCEEDED:{exc}") from exc
        except Exception as exc:
            raise AuthorityHistoryIncompleteError(f"{error_prefix}_REQUEST_ERROR:{type(exc).__name__}") from exc
        if response.status_code != 200:
            raise AuthorityHistoryIncompleteError(_d1_failure(error_prefix, response))
        data = response.json()
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise AuthorityHistoryIncompleteError(f"{error_prefix}_RESPONSE_INVALID")
        return data

    if cursor is None:
        page = await request({"select": select, "order": "ts.asc,id.asc", "limit": str(bounded)})
    else:
        cursor_ts, cursor_id = cursor
        same = await request({
            "select": select,
            "ts": f"eq.{cursor_ts}",
            "id": f"gt.{cursor_id}",
            "order": "id.asc",
            "limit": str(bounded),
        })
        remaining = bounded - len(same)
        later: list[dict[str, Any]] = []
        if remaining > 0:
            later = await request({
                "select": select,
                "ts": f"gt.{cursor_ts}",
                "order": "ts.asc,id.asc",
                "limit": str(remaining),
            })
        page = same + later

    previous = cursor
    seen: set[tuple[str, str]] = set()
    for row in page:
        key = _row_cursor(row)
        if not _cursor_gt(key, previous):
            raise AuthorityHistoryIncompleteError(f"{error_prefix}_NON_MONOTONIC")
        if key in seen:
            raise AuthorityHistoryIncompleteError(f"{error_prefix}_DUPLICATE_CURSOR")
        seen.add(key)
        previous = key
    return page


async def _bootstrap_authority_history(
    symbol: str | None,
    *,
    page_size: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    """Explicit, rate-limited full bootstrap; never part of steady-state refresh."""
    normalized = _normalize_symbol(symbol)
    bounded_page_size = max(1, min(int(page_size), AUTHORITY_HISTORY_PAGE_SIZE_MAX))
    bounded_max_pages = max(1, min(int(max_pages), AUTHORITY_HISTORY_MAX_PAGES))
    tail_before = await _tail_cursor(normalized or None)
    collected: list[dict[str, Any]] = []
    cursor: tuple[str, str] | None = None
    seen: set[tuple[str, str]] = set()
    for _ in range(bounded_max_pages):
        page = await _keyset_page(
            normalized or None,
            cursor,
            select=AUTHORITY_HISTORY_SELECT,
            limit=bounded_page_size,
            diagnostics_key="authority_bootstrap_requests",
            error_prefix="AUTHORITY_BOOTSTRAP",
        )
        for raw in page:
            row = _authority_row_from_projection(raw)
            key = _row_cursor(row)
            if key in seen:
                raise AuthorityHistoryIncompleteError("AUTHORITY_BOOTSTRAP_DUPLICATE_CURSOR")
            seen.add(key)
            collected.append(row)
        _r7b_diagnostics["authority_bootstrap_rows"] += len(page)
        if len(page) < bounded_page_size:
            tail_after = await _tail_cursor(normalized or None)
            final_cursor = _row_cursor(collected[-1]) if collected else None
            if tail_before != tail_after:
                raise AuthorityHistoryIncompleteError("AUTHORITY_BOOTSTRAP_RACE_TAIL_CHANGED")
            if final_cursor != tail_after:
                raise AuthorityHistoryIncompleteError("AUTHORITY_BOOTSTRAP_RACE_CURSOR_MISMATCH")
            return collected
        next_cursor = _row_cursor(page[-1])
        if next_cursor == cursor:
            raise AuthorityHistoryIncompleteError("AUTHORITY_BOOTSTRAP_CURSOR_STALLED")
        cursor = next_cursor
    raise AuthorityHistoryIncompleteError(f"AUTHORITY_BOOTSTRAP_PAGE_CAP_HIT:{bounded_max_pages}")


async def _fetch_authority_delta_raw(
    symbol: str | None,
    cursor: tuple[str, str] | None,
    *,
    page_size: int,
    max_pages: int,
    id_floor: int | None = None,
) -> list[dict[str, Any]]:
    bounded_page_size = max(1, min(int(page_size), AUTHORITY_DELTA_PAGE_SIZE_MAX))
    bounded_pages = max(1, min(int(max_pages), AUTHORITY_DELTA_MAX_PAGES))
    normalized = _normalize_symbol(symbol)
    collected: list[dict[str, Any]] = []

    if id_floor is not None:
        try:
            current_id = int(id_floor)
        except Exception as exc:
            raise AuthorityHistoryIncompleteError("AUTHORITY_DELTA_ID_FLOOR_INVALID") from exc
        if current_id < 0:
            raise AuthorityHistoryIncompleteError("AUTHORITY_DELTA_ID_FLOOR_NEGATIVE")
        c = _get_client()
        for _ in range(bounded_pages):
            params = {
                "select": AUTHORITY_HISTORY_SELECT,
                "id": f"gt.{current_id}",
                "order": "id.asc",
                "limit": str(bounded_page_size),
            }
            if normalized:
                params["symbol"] = f"eq.{normalized}"
            _r7b_diagnostics["authority_delta_requests"] += 1
            try:
                response = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
            except D1QuotaExceededError as exc:
                raise AuthorityHistoryIncompleteError(f"AUTHORITY_DELTA:D1_QUOTA_EXCEEDED:{exc}") from exc
            except Exception as exc:
                raise AuthorityHistoryIncompleteError(f"AUTHORITY_DELTA_REQUEST_ERROR:{type(exc).__name__}") from exc
            if response.status_code != 200:
                raise AuthorityHistoryIncompleteError(_d1_failure("AUTHORITY_DELTA", response))
            data = response.json()
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                raise AuthorityHistoryIncompleteError("AUTHORITY_DELTA_RESPONSE_INVALID")
            for raw in data:
                row = _authority_row_from_projection(raw)
                try:
                    row_id = int(_row_cursor(row)[1])
                except Exception as exc:
                    raise AuthorityHistoryIncompleteError("AUTHORITY_DELTA_ID_NONINTEGER") from exc
                if row_id <= current_id:
                    raise AuthorityHistoryIncompleteError("AUTHORITY_DELTA_NON_MONOTONIC_ID")
                current_id = row_id
                collected.append(row)
            if len(data) < bounded_page_size:
                return collected
        raise AuthorityHistoryIncompleteError(f"AUTHORITY_DELTA_PAGE_CAP_HIT:{bounded_pages}")

    current = cursor
    for _ in range(bounded_pages):
        page = await _keyset_page(
            normalized or None, current, select=AUTHORITY_HISTORY_SELECT,
            limit=bounded_page_size, diagnostics_key="authority_delta_requests",
            error_prefix="AUTHORITY_DELTA",
        )
        for raw in page:
            row = _authority_row_from_projection(raw)
            key = _row_cursor(row)
            if not _cursor_gt(key, cursor):
                raise AuthorityHistoryIncompleteError("AUTHORITY_DELTA_NON_MONOTONIC")
            collected.append(row)
        if len(page) < bounded_page_size:
            return collected
        current = _row_cursor(page[-1])
    raise AuthorityHistoryIncompleteError(f"AUTHORITY_DELTA_PAGE_CAP_HIT:{bounded_pages}")


def _mutable_ids(rows: dict[str, dict[str, Any]]) -> list[str]:
    from .settlement_proof import is_proof_qualified
    ids: list[str] = []
    for row_id, row in rows.items():
        direction = str(row.get("prediction") or "").upper()
        if direction in {"LONG", "SHORT"} and not is_proof_qualified(row):
            ids.append(row_id)
    if len(ids) > AUTHORITY_MUTABLE_ID_MAX:
        raise AuthorityHistoryIncompleteError(
            f"AUTHORITY_MUTABLE_BACKLOG_CAP_HIT:{len(ids)}>{AUTHORITY_MUTABLE_ID_MAX}"
        )
    return ids


async def _refresh_mutable_rows(symbol: str | None, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    normalized = _normalize_symbol(symbol)
    c = _get_client()
    refreshed: list[dict[str, Any]] = []
    for start in range(0, len(ids), AUTHORITY_MUTABLE_ID_BATCH):
        batch = ids[start:start + AUTHORITY_MUTABLE_ID_BATCH]
        params = {
            "select": AUTHORITY_HISTORY_SELECT,
            "id": "in.(" + ",".join(batch) + ")",
            "limit": str(len(batch)),
        }
        if normalized:
            params["symbol"] = f"eq.{normalized}"
        _r7b_diagnostics["authority_mutable_lookup_requests"] += 1
        try:
            r = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
        except D1QuotaExceededError as exc:
            raise AuthorityHistoryIncompleteError(f"AUTHORITY_MUTABLE:D1_QUOTA_EXCEEDED:{exc}") from exc
        except Exception as exc:
            raise AuthorityHistoryIncompleteError(f"AUTHORITY_MUTABLE_REQUEST_ERROR:{type(exc).__name__}") from exc
        if r.status_code != 200:
            raise AuthorityHistoryIncompleteError(f"AUTHORITY_MUTABLE_HTTP_{r.status_code}")
        data = r.json()
        if not isinstance(data, list):
            raise AuthorityHistoryIncompleteError("AUTHORITY_MUTABLE_RESPONSE_NOT_LIST")
        returned = {str(row.get("id") or "") for row in data if isinstance(row, dict)}
        missing = set(batch) - returned
        if missing:
            raise AuthorityHistoryIncompleteError("AUTHORITY_MUTABLE_ROW_DISAPPEARED")
        refreshed.extend(_authority_row_from_projection(row) for row in data)
    return refreshed


async def fetch_authority_history(
    symbol: Optional[str] = None,
    *,
    page_size: int = 250,
    max_pages: int = AUTHORITY_HISTORY_MAX_PAGES,
) -> list[dict]:
    """Load a durable verified authority base, then apply bounded live deltas.

    Missing seals fail closed by default. A full bootstrap is possible only when
    SENEX_AUTHORITY_BOOTSTRAP_ALLOWED=1 and the durable per-scope bootstrap guard
    allows it. Corrupt/stale/provenance-mismatched seals are never auto-replaced.
    """
    scope = _state_key(symbol)
    identity = _runtime_identity()
    state = _authority_symbol_state.get(scope)

    if state is None:
        try:
            persisted = durable_seal.load_authority_state(
                scope,
                identity=identity,
                writer_contract=AUTHORITY_MUTATION_CONTRACT,
                max_age_s=AUTHORITY_SEAL_MAX_AGE_S,
            )
            rows = {str(row.get("id")): dict(row) for row in persisted["rows"]}
            cursor = _cursor_tuple(persisted.get("cursor"))
            state = {
                "rows": rows,
                "cursor": cursor,
                "created_at": persisted.get("created_at"),
            }
            state["seal"] = _seal_state(scope, rows, cursor)
            _validate_state_metadata(scope, state)
            _authority_symbol_state[scope] = state
            _r7b_diagnostics["durable_authority_loads"] += 1
        except durable_seal.AuthoritySealMissingError:
            try:
                durable_seal.assert_bootstrap_permitted(scope)
                durable_seal.record_bootstrap_attempt(scope)
                _r7b_diagnostics["bootstrap_guard_writes"] += 1
            except durable_seal.AuthoritySealError as exc:
                raise AuthorityHistoryIncompleteError(str(exc)) from exc
            bootstrap = await _bootstrap_authority_history(symbol, page_size=page_size, max_pages=max_pages)
            rows = {}
            cursor: tuple[str, str] | None = None
            for row in bootstrap:
                key = _row_cursor(row)
                rows[key[1]] = row
                if _cursor_gt(key, cursor):
                    cursor = key
            state = {"rows": rows, "cursor": cursor}
            state["seal"] = _seal_state(scope, rows, cursor)
            ordered = sorted(rows.values(), key=lambda row: _cursor_order_key(_row_cursor(row)))
            try:
                persisted = durable_seal.save_authority_state(
                    scope,
                    ordered,
                    _cursor_dict(cursor),
                    identity=identity,
                    writer_contract=AUTHORITY_MUTATION_CONTRACT,
                )
            except durable_seal.AuthoritySealError as exc:
                raise AuthorityHistoryIncompleteError(str(exc)) from exc
            state["created_at"] = persisted["created_at"]
            _authority_symbol_state[scope] = state
            _r7b_diagnostics["durable_authority_writes"] += 1
        except durable_seal.AuthoritySealError as exc:
            raise AuthorityHistoryIncompleteError(str(exc)) from exc
    else:
        _validate_state_metadata(scope, state)

    id_floor = _authority_id_floor(state["rows"])
    delta = await _fetch_authority_delta_raw(
        symbol,
        state.get("cursor"),
        page_size=page_size,
        max_pages=min(int(max_pages), AUTHORITY_DELTA_MAX_PAGES),
        id_floor=id_floor,
    )
    changed = False
    for row in delta:
        key = _row_cursor(row)
        state["rows"][key[1]] = row
        changed = True
    if delta:
        state["cursor"] = _authority_temporal_cursor(state["rows"])

    mutable = _mutable_ids(state["rows"])
    refreshed = await _refresh_mutable_rows(symbol, mutable)
    for row in refreshed:
        row_id = _row_cursor(row)[1]
        if state["rows"].get(row_id) != row:
            state["rows"][row_id] = row
            changed = True

    if changed:
        state["seal"] = _seal_state(scope, state["rows"], state.get("cursor"))
    _validate_state_metadata(scope, state)
    ordered = sorted(state["rows"].values(), key=lambda row: _cursor_order_key(_row_cursor(row)))
    try:
        persisted = durable_seal.save_authority_state(
            scope,
            ordered,
            _cursor_dict(state.get("cursor")),
            identity=identity,
            writer_contract=AUTHORITY_MUTATION_CONTRACT,
            created_at=state.get("created_at"),
        )
    except durable_seal.AuthoritySealError as exc:
        raise AuthorityHistoryIncompleteError(str(exc)) from exc
    state["created_at"] = persisted["created_at"]
    state["last_live_verification_at"] = persisted["verified_at"]
    _r7b_diagnostics["durable_authority_writes"] += 1
    return json.loads(json.dumps(ordered))


async def _tail_cursor(symbol: str | None = None) -> tuple[str, str] | None:
    c = _get_client()
    params = {"select": "id,ts", "order": "ts.desc,id.desc", "limit": "1"}
    normalized = _normalize_symbol(symbol)
    if normalized:
        params["symbol"] = f"eq.{normalized}"
    try:
        response = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
    except D1QuotaExceededError as exc:
        raise AuthorityHistoryIncompleteError(f"AUTHORITY_TAIL:D1_QUOTA_EXCEEDED:{exc}") from exc
    except Exception as exc:
        raise AuthorityHistoryIncompleteError(f"AUTHORITY_TAIL_REQUEST_ERROR:{type(exc).__name__}") from exc
    if response.status_code != 200:
        raise AuthorityHistoryIncompleteError(_d1_failure("AUTHORITY_TAIL", response))
    data = response.json()
    if not isinstance(data, list):
        raise AuthorityHistoryIncompleteError("AUTHORITY_TAIL_RESPONSE_NOT_LIST")
    if not data:
        return None
    if not isinstance(data[0], dict):
        raise AuthorityHistoryIncompleteError("AUTHORITY_TAIL_ROW_NOT_OBJECT")
    return _row_cursor(data[0])


async def _exact_count_tail_id_cursor() -> tuple[str, str] | None:
    """Return the current exact-count tail through the INTEGER PRIMARY KEY.

    ``id > 0 ORDER BY id DESC LIMIT 1`` is deliberate: production EXPLAIN
    proves it is an INTEGER PRIMARY KEY range search.  The active H011 writer
    never supplies ``id`` and the verified writer contract permits append-only
    inserts plus directional settlement mutations, with no deletes.
    """
    c = _get_client()
    params = {"select": "id,ts", "id": "gt.0", "order": "id.desc", "limit": "1"}
    try:
        response = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
    except D1QuotaExceededError as exc:
        raise ExactCountUnavailableError(f"EXACT_COUNT_TAIL:D1_QUOTA_EXCEEDED:{exc}") from exc
    except Exception as exc:
        raise ExactCountUnavailableError(f"EXACT_COUNT_TAIL_REQUEST_ERROR:{type(exc).__name__}") from exc
    if response.status_code != 200:
        raise ExactCountUnavailableError(_d1_failure("EXACT_COUNT_TAIL", response))
    data = response.json()
    if not isinstance(data, list):
        raise ExactCountUnavailableError("EXACT_COUNT_TAIL_RESPONSE_NOT_LIST")
    if not data:
        return None
    if not isinstance(data[0], dict):
        raise ExactCountUnavailableError("EXACT_COUNT_TAIL_ROW_NOT_OBJECT")
    key = _row_cursor(data[0])
    try:
        if int(key[1]) <= 0:
            raise ValueError("nonpositive")
    except Exception as exc:
        raise ExactCountUnavailableError("EXACT_COUNT_TAIL_ID_INVALID") from exc
    return key


async def _global_id_delta(cursor: tuple[str, str] | None) -> list[tuple[str, str]]:
    """Bounded exact-count delta using the INTEGER PRIMARY KEY only.

    The HOT production schema has no global (ts,id) index.  Exact-count state
    therefore advances by monotonically allocated ``id`` under the verified
    append/no-delete writer contract.  The query shape is a rowid range search
    (``id > ? ORDER BY id``), never a global timestamp sort/history scan.
    """
    try:
        floor_id = int(cursor[1]) if cursor is not None else 0
    except Exception as exc:
        raise ExactCountUnavailableError("EXACT_COUNT_ID_CURSOR_INVALID") from exc

    c = _get_client()
    collected: list[tuple[str, str]] = []
    current_id = floor_id
    for _ in range(AUTHORITY_DELTA_MAX_PAGES):
        params = {
            "select": "id,ts",
            "id": f"gt.{current_id}",
            "order": "id.asc",
            "limit": str(AUTHORITY_DELTA_PAGE_SIZE_MAX),
        }
        _r7b_diagnostics["exact_count_delta_requests"] += 1
        try:
            response = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
        except D1QuotaExceededError as exc:
            raise ExactCountUnavailableError(f"EXACT_COUNT_DELTA:D1_QUOTA_EXCEEDED:{exc}") from exc
        except Exception as exc:
            raise ExactCountUnavailableError(f"EXACT_COUNT_DELTA_REQUEST_ERROR:{type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ExactCountUnavailableError(_d1_failure("EXACT_COUNT_DELTA", response))
        data = response.json()
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ExactCountUnavailableError("EXACT_COUNT_DELTA_RESPONSE_INVALID")

        page: list[tuple[str, str]] = []
        for row in data:
            key = _row_cursor(row)
            try:
                row_id = int(key[1])
            except Exception as exc:
                raise ExactCountUnavailableError("EXACT_COUNT_DELTA_ID_NONINTEGER") from exc
            if row_id <= current_id:
                raise ExactCountUnavailableError("EXACT_COUNT_DELTA_NON_MONOTONIC_ID")
            current_id = row_id
            page.append(key)
        collected.extend(page)
        if len(data) < AUTHORITY_DELTA_PAGE_SIZE_MAX:
            return collected
        if not page:
            raise ExactCountUnavailableError("EXACT_COUNT_DELTA_CURSOR_STALLED")
    raise ExactCountUnavailableError(f"EXACT_COUNT_DELTA_PAGE_CAP_HIT:{AUTHORITY_DELTA_MAX_PAGES}")


async def _scoped_exact_count_tail_id_cursor(symbol: str) -> tuple[str, str] | None:
    normalized = _normalize_symbol(symbol)
    if not normalized:
        return await _exact_count_tail_id_cursor()
    c = _get_client()
    params = {"select": "id,ts", "id": "gt.0", "symbol": f"eq.{normalized}", "order": "id.desc", "limit": "1"}
    try:
        response = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
    except D1QuotaExceededError as exc:
        raise ExactCountUnavailableError(f"SCOPED_EXACT_COUNT_TAIL:D1_QUOTA_EXCEEDED:{exc}") from exc
    except Exception as exc:
        raise ExactCountUnavailableError(f"SCOPED_EXACT_COUNT_TAIL_REQUEST_ERROR:{type(exc).__name__}") from exc
    if response.status_code != 200:
        raise ExactCountUnavailableError(_d1_failure("SCOPED_EXACT_COUNT_TAIL", response))
    data = response.json()
    if not isinstance(data, list):
        raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_TAIL_RESPONSE_NOT_LIST")
    if not data:
        return None
    if not isinstance(data[0], dict):
        raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_TAIL_ROW_NOT_OBJECT")
    key = _row_cursor(data[0])
    try:
        if int(key[1]) <= 0:
            raise ValueError("nonpositive")
    except Exception as exc:
        raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_TAIL_ID_INVALID") from exc
    return key


async def _scoped_id_delta(symbol: str, cursor: tuple[str, str] | None) -> list[tuple[str, str]]:
    normalized = _normalize_symbol(symbol)
    if not normalized:
        return await _global_id_delta(cursor)
    try:
        floor_id = int(cursor[1]) if cursor is not None else 0
    except Exception as exc:
        raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_ID_CURSOR_INVALID") from exc
    c = _get_client()
    collected: list[tuple[str, str]] = []
    current_id = floor_id
    for _ in range(AUTHORITY_DELTA_MAX_PAGES):
        params = {
            "select": "id,ts",
            "id": f"gt.{current_id}",
            "symbol": f"eq.{normalized}",
            "order": "id.asc",
            "limit": str(AUTHORITY_DELTA_PAGE_SIZE_MAX),
        }
        _r7b_diagnostics["exact_count_delta_requests"] += 1
        try:
            response = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
        except D1QuotaExceededError as exc:
            raise ExactCountUnavailableError(f"SCOPED_EXACT_COUNT_DELTA:D1_QUOTA_EXCEEDED:{exc}") from exc
        except Exception as exc:
            raise ExactCountUnavailableError(f"SCOPED_EXACT_COUNT_DELTA_REQUEST_ERROR:{type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ExactCountUnavailableError(_d1_failure("SCOPED_EXACT_COUNT_DELTA", response))
        data = response.json()
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_DELTA_RESPONSE_INVALID")
        page: list[tuple[str, str]] = []
        for row in data:
            key = _row_cursor(row)
            try:
                row_id = int(key[1])
            except Exception as exc:
                raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_DELTA_ID_NONINTEGER") from exc
            if row_id <= current_id:
                raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_DELTA_NON_MONOTONIC_ID")
            current_id = row_id
            page.append(key)
        collected.extend(page)
        if len(data) < AUTHORITY_DELTA_PAGE_SIZE_MAX:
            return collected
        if not page:
            raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_DELTA_CURSOR_STALLED")
    raise ExactCountUnavailableError(f"SCOPED_EXACT_COUNT_DELTA_PAGE_CAP_HIT:{AUTHORITY_DELTA_MAX_PAGES}")


async def _bootstrap_scoped_exact_count_from_ids(symbol: str) -> tuple[int, tuple[str, str] | None]:
    normalized = _normalize_symbol(symbol)
    if not normalized:
        return await _bootstrap_exact_count_from_ids()
    tail_before = await _scoped_exact_count_tail_id_cursor(normalized)
    total = 0
    cursor: tuple[str, str] | None = None
    current_id = 0
    bounded_page_size = AUTHORITY_HISTORY_PAGE_SIZE_MAX
    _r7b_diagnostics["exact_count_bootstrap_calls"] += 1
    state = _count_state_for(normalized)
    state["bootstrap_count_calls"] += 1
    c = _get_client()
    for _ in range(AUTHORITY_HISTORY_MAX_PAGES):
        params = {
            "select": "id,ts",
            "id": f"gt.{current_id}",
            "symbol": f"eq.{normalized}",
            "order": "id.asc",
            "limit": str(bounded_page_size),
        }
        _r7b_diagnostics["exact_count_delta_requests"] += 1
        try:
            response = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
        except D1QuotaExceededError as exc:
            raise ExactCountUnavailableError(f"SCOPED_EXACT_COUNT_BOOTSTRAP:D1_QUOTA_EXCEEDED:{exc}") from exc
        except Exception as exc:
            raise ExactCountUnavailableError(f"SCOPED_EXACT_COUNT_BOOTSTRAP_REQUEST_ERROR:{type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ExactCountUnavailableError(_d1_failure("SCOPED_EXACT_COUNT_BOOTSTRAP", response))
        data = response.json()
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_BOOTSTRAP_RESPONSE_INVALID")
        page: list[tuple[str, str]] = []
        for row in data:
            key = _row_cursor(row)
            row_id = int(key[1])
            if row_id <= current_id:
                raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_BOOTSTRAP_NON_MONOTONIC_ID")
            current_id = row_id
            page.append(key)
        total += len(page)
        if page:
            cursor = page[-1]
        if len(data) < bounded_page_size:
            tail_after = await _scoped_exact_count_tail_id_cursor(normalized)
            if tail_before != tail_after:
                raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_BOOTSTRAP_RACE")
            if total == 0:
                if tail_after is not None:
                    raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_BOOTSTRAP_EMPTY_TAIL_MISMATCH")
                return 0, None
            if cursor != tail_after:
                raise ExactCountUnavailableError("SCOPED_EXACT_COUNT_BOOTSTRAP_CURSOR_MISMATCH")
            return total, cursor
    raise ExactCountUnavailableError(f"SCOPED_EXACT_COUNT_BOOTSTRAP_PAGE_CAP_HIT:{AUTHORITY_HISTORY_MAX_PAGES}")


async def _bootstrap_exact_count_from_ids() -> tuple[int, tuple[str, str] | None]:
    """One explicitly authorized INTEGER-PK traversal; never COUNT(*)/global ts sort."""
    tail_before = await _exact_count_tail_id_cursor()
    total = 0
    cursor: tuple[str, str] | None = None
    current_id = 0
    bounded_page_size = AUTHORITY_HISTORY_PAGE_SIZE_MAX
    _r7b_diagnostics["exact_count_bootstrap_calls"] += 1
    _exact_count_state["bootstrap_count_calls"] += 1
    c = _get_client()

    for _ in range(AUTHORITY_HISTORY_MAX_PAGES):
        params = {
            "select": "id,ts",
            "id": f"gt.{current_id}",
            "order": "id.asc",
            "limit": str(bounded_page_size),
        }
        _r7b_diagnostics["exact_count_delta_requests"] += 1
        try:
            response = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
        except D1QuotaExceededError as exc:
            raise ExactCountUnavailableError(f"EXACT_COUNT_BOOTSTRAP:D1_QUOTA_EXCEEDED:{exc}") from exc
        except Exception as exc:
            raise ExactCountUnavailableError(f"EXACT_COUNT_BOOTSTRAP_REQUEST_ERROR:{type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ExactCountUnavailableError(_d1_failure("EXACT_COUNT_BOOTSTRAP", response))
        data = response.json()
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ExactCountUnavailableError("EXACT_COUNT_BOOTSTRAP_RESPONSE_INVALID")

        page: list[tuple[str, str]] = []
        for row in data:
            key = _row_cursor(row)
            try:
                row_id = int(key[1])
            except Exception as exc:
                raise ExactCountUnavailableError("EXACT_COUNT_BOOTSTRAP_ID_NONINTEGER") from exc
            if row_id <= current_id:
                raise ExactCountUnavailableError("EXACT_COUNT_BOOTSTRAP_NON_MONOTONIC_ID")
            current_id = row_id
            page.append(key)
        total += len(page)
        if page:
            cursor = page[-1]

        if len(data) < bounded_page_size:
            tail_after = await _exact_count_tail_id_cursor()
            if tail_before != tail_after:
                raise ExactCountUnavailableError("EXACT_COUNT_BOOTSTRAP_RACE")
            if total == 0:
                if tail_after is not None:
                    raise ExactCountUnavailableError("EXACT_COUNT_BOOTSTRAP_EMPTY_TAIL_MISMATCH")
                return 0, None
            if cursor != tail_after:
                raise ExactCountUnavailableError("EXACT_COUNT_BOOTSTRAP_CURSOR_MISMATCH")
            return total, cursor
    raise ExactCountUnavailableError(f"EXACT_COUNT_BOOTSTRAP_PAGE_CAP_HIT:{AUTHORITY_HISTORY_MAX_PAGES}")


async def _count_predictions_exact_global() -> int:
    """Exact global row count from durable state plus bounded append deltas.

    No COUNT(*) or PostgREST count=exact request exists in this candidate. The
    first-ever count state is established only through an explicit guarded id/ts
    bootstrap and then survives process restarts in the non-D1 durable store.
    """
    identity = _runtime_identity()
    if not _exact_count_state["bootstrapped"]:
        try:
            persisted = durable_seal.load_count_state(
                identity=identity,
                writer_contract=AUTHORITY_MUTATION_CONTRACT,
                max_age_s=AUTHORITY_SEAL_MAX_AGE_S,
            )
            _exact_count_state.update({
                "bootstrapped": True,
                "count": int(persisted["row_count"]),
                "cursor": _cursor_tuple(persisted.get("cursor")),
                "created_at": persisted.get("created_at"),
            })
            _r7b_diagnostics["durable_count_loads"] += 1
        except durable_seal.AuthoritySealMissingError:
            try:
                durable_seal.assert_bootstrap_permitted("GLOBAL_EXACT_COUNT")
                durable_seal.record_bootstrap_attempt("GLOBAL_EXACT_COUNT")
                _r7b_diagnostics["bootstrap_guard_writes"] += 1
            except durable_seal.AuthoritySealError as exc:
                raise ExactCountUnavailableError(str(exc)) from exc
            total, cursor = await _bootstrap_exact_count_from_ids()
            try:
                persisted = durable_seal.save_count_state(
                    total,
                    _cursor_dict(cursor),
                    identity=identity,
                    writer_contract=AUTHORITY_MUTATION_CONTRACT,
                )
            except durable_seal.AuthoritySealError as exc:
                raise ExactCountUnavailableError(str(exc)) from exc
            _exact_count_state.update({
                "bootstrapped": True,
                "count": total,
                "cursor": cursor,
                "created_at": persisted.get("created_at"),
            })
            _r7b_diagnostics["durable_count_writes"] += 1
        except durable_seal.AuthoritySealError as exc:
            raise ExactCountUnavailableError(str(exc)) from exc

    delta = await _global_id_delta(_exact_count_state.get("cursor"))
    if delta:
        _exact_count_state["count"] = int(_exact_count_state["count"]) + len(delta)
        _exact_count_state["cursor"] = delta[-1]
    try:
        persisted = durable_seal.save_count_state(
            int(_exact_count_state["count"]),
            _cursor_dict(_exact_count_state.get("cursor")),
            identity=identity,
            writer_contract=AUTHORITY_MUTATION_CONTRACT,
            created_at=_exact_count_state.get("created_at"),
        )
    except durable_seal.AuthoritySealError as exc:
        raise ExactCountUnavailableError(str(exc)) from exc
    _exact_count_state["created_at"] = persisted.get("created_at")
    _r7b_diagnostics["durable_count_writes"] += 1
    return int(_exact_count_state["count"])


async def _count_predictions_exact_scoped(symbol: str) -> int:
    normalized = _normalize_symbol(symbol)
    if not normalized:
        return await _count_predictions_exact_global()
    state = _count_state_for(normalized)
    scope = _count_scope(normalized)
    identity = _runtime_identity()
    if not state["bootstrapped"]:
        try:
            persisted = durable_seal.load_count_state(
                identity=identity, writer_contract=AUTHORITY_MUTATION_CONTRACT,
                scope=scope, max_age_s=AUTHORITY_SEAL_MAX_AGE_S,
            )
            state.update({"bootstrapped": True, "count": int(persisted["row_count"]),
                          "cursor": _cursor_tuple(persisted.get("cursor")),
                          "created_at": persisted.get("created_at")})
            _r7b_diagnostics["durable_count_loads"] += 1
        except durable_seal.AuthoritySealMissingError:
            try:
                durable_seal.assert_bootstrap_permitted(scope)
                durable_seal.record_bootstrap_attempt(scope)
                _r7b_diagnostics["bootstrap_guard_writes"] += 1
            except durable_seal.AuthoritySealError as exc:
                raise ExactCountUnavailableError(str(exc)) from exc
            total, cursor = await _bootstrap_scoped_exact_count_from_ids(normalized)
            try:
                persisted = durable_seal.save_count_state(
                    total, _cursor_dict(cursor), identity=identity,
                    writer_contract=AUTHORITY_MUTATION_CONTRACT, scope=scope,
                )
            except durable_seal.AuthoritySealError as exc:
                raise ExactCountUnavailableError(str(exc)) from exc
            state.update({"bootstrapped": True, "count": total, "cursor": cursor,
                          "created_at": persisted.get("created_at")})
            _r7b_diagnostics["durable_count_writes"] += 1
        except durable_seal.AuthoritySealError as exc:
            raise ExactCountUnavailableError(str(exc)) from exc
    delta = await _scoped_id_delta(normalized, state.get("cursor"))
    if delta:
        state["count"] = int(state["count"]) + len(delta)
        state["cursor"] = delta[-1]
    try:
        persisted = durable_seal.save_count_state(
            int(state["count"]), _cursor_dict(state.get("cursor")),
            identity=identity, writer_contract=AUTHORITY_MUTATION_CONTRACT,
            scope=scope, created_at=state.get("created_at"),
        )
    except durable_seal.AuthoritySealError as exc:
        raise ExactCountUnavailableError(str(exc)) from exc
    state["created_at"] = persisted.get("created_at")
    _r7b_diagnostics["durable_count_writes"] += 1
    return int(state["count"])


async def count_predictions_exact(symbol: Optional[str] = None) -> int:
    normalized = _normalize_symbol(symbol)
    if normalized:
        return await _count_predictions_exact_scoped(normalized)
    return await _count_predictions_exact_global()


async def count_predictions() -> Optional[int]:
    """Best-effort count for diagnostics; UNKNOWN is None, never fabricated zero."""
    try:
        return await count_predictions_exact()
    except Exception as e:
        log.debug("supabase exact count unavailable: %s", e)
        return None


PENDING_SCAN_PAGE_SIZE_MAX = 100
PENDING_SCAN_MAX_PAGES = 2
_pending_scan_diagnostics: dict[str, Any] = {}


def reset_pending_scan_cursor() -> None:
    global _pending_scan_diagnostics
    _pending_scan_diagnostics = {}


def get_pending_scan_diagnostics() -> dict[str, Any]:
    return dict(_pending_scan_diagnostics)



def reset_r7b_incremental_state_for_tests() -> None:
    """Harness-only deterministic reset; never called by production runtime."""
    _authority_symbol_state.clear()
    _scoped_exact_count_state.clear()
    _exact_count_state.update({"bootstrapped": False, "count": None, "cursor": None, "bootstrap_count_calls": 0})
    for key in _r7b_diagnostics:
        _r7b_diagnostics[key] = 0
    _d1_quota_breaker.update({
        "opened_at": None,
        "open_until": None,
        "reason": None,
        "network_calls": 0,
        "suppressed_calls": 0,
    })
    reset_pending_scan_cursor()


async def fetch_pending_outcomes(
    older_than_seconds: int = 900,
    limit: int = 100,
    *,
    max_pages: int = PENDING_SCAN_MAX_PAGES,
) -> list[dict]:
    """Bounded NULL-outcome scan; deliberately no recurrent exact-count metric."""
    global _pending_scan_diagnostics
    try:
        from datetime import timedelta

        bounded_limit = max(1, min(int(limit), PENDING_SCAN_PAGE_SIZE_MAX))
        bounded_pages = max(1, min(int(max_pages), PENDING_SCAN_MAX_PAGES))
        fairness_bound_rows = bounded_limit * bounded_pages
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
        c = _get_client()
        base_params = {
            "select": "id,ts,symbol,prediction,confidence,price_now,exchange_used,audit",
            "outcome": "is.null",
            "prediction": "in.(LONG,SHORT)",
            "ts": f"lte.{cutoff}",
            "order": "ts.asc,id.asc",
        }
        oldest_params = {
            "select": "id,ts,symbol,prediction",
            "outcome": "is.null",
            "prediction": "in.(LONG,SHORT)",
            "ts": f"lte.{cutoff}",
            "order": "ts.asc,id.asc",
            "limit": "1",
        }
        oldest = None
        oldest_resp = await _d1_get(c, f"/{SUPABASE_TABLE}", params=oldest_params)
        if oldest_resp.status_code == 200:
            oldest_rows = oldest_resp.json() or []
            if isinstance(oldest_rows, list) and oldest_rows and isinstance(oldest_rows[0], dict):
                oldest = oldest_rows[0]

        collected: list[dict] = []
        cursor: tuple[str, str] | None = None
        pages_scanned = 0
        pass_complete = False
        error = None
        for _ in range(bounded_pages):
            params = dict(base_params)
            params["limit"] = str(bounded_limit)
            if cursor is not None:
                params["or"] = f"(ts.gt.{cursor[0]},and(ts.eq.{cursor[0]},id.gt.{cursor[1]}))"
            r = await _d1_get(c, f"/{SUPABASE_TABLE}", params=params)
            if r.status_code != 200:
                log.error("supabase fetch_pending_outcomes failed: %s %s", r.status_code, r.text[:200])
                error = f"HTTP_{r.status_code}"
                break
            page = r.json() or []
            page = page if isinstance(page, list) else []
            pages_scanned += 1
            collected.extend(page)
            if len(page) < bounded_limit:
                pass_complete = True
                break
            cursor = _row_cursor(page[-1])

        scan_cap_hit = (not pass_complete and error is None and pages_scanned >= bounded_pages)
        _pending_scan_diagnostics = {
            "eligible_directional_pending_count": None,
            "oldest_eligible_directional_pending_id": (oldest or {}).get("id"),
            "oldest_eligible_directional_pending_ts": (oldest or {}).get("ts"),
            "rows_scanned_last_pass": len(collected),
            "pages_scanned_last_pass": pages_scanned,
            "scan_cap_hit": scan_cap_hit,
            "cursor_before": None,
            "cursor_after": cursor,
            "pass_complete": pass_complete,
            "restart_safe_stateless": True,
            "fairness_bound_rows_per_invocation": fairness_bound_rows,
            "fairness_scope": "RESTART_SAFE_PREFIX_ONLY_COUNT_NOT_QUERIED",
            "error": error,
        }
        return collected
    except Exception as e:
        log.error("supabase fetch_pending_outcomes error: %s", e)
        _pending_scan_diagnostics = {
            "error": type(e).__name__,
            "rows_scanned_last_pass": 0,
            "pages_scanned_last_pass": 0,
            "restart_safe_stateless": True,
            "fairness_bound_rows_per_invocation": PENDING_SCAN_PAGE_SIZE_MAX * PENDING_SCAN_MAX_PAGES,
            "fairness_scope": "FAIL_CLOSED_ERROR",
        }
        return []


async def update_outcome_dual(
    prediction_id: int,
    outcome_15m: str,
    outcome_1h: str,
    price_15m_later: float,
    price_1h_later: float,
    primary_window: str = "1h",
    *,
    price_evidence_15m: dict[str, Any] | None = None,
    price_evidence_1h: dict[str, Any] | None = None,
) -> bool:
    """Baseline CAS settlement writer preserved byte-semantically by overlay."""
    try:
        import math
        from datetime import timedelta
        from .settlement_contract import (
            WINDOW_15M_S,
            WINDOW_1H_S,
            normalize_exchange,
            normalize_symbol,
            parse_utc,
            validate_price_evidence,
        )

        c = _get_client()
        r_get = await _d1_get(c, 
            f"/{SUPABASE_TABLE}",
            params={
                "select": "id,ts,symbol,prediction,price_now,exchange_used,audit,outcome",
                "id": f"eq.{prediction_id}",
                "limit": "1",
            },
        )
        if r_get.status_code != 200:
            return False
        existing_rows = r_get.json() or []
        if not isinstance(existing_rows, list) or not existing_rows:
            return False
        existing = existing_rows[0]
        if existing.get("outcome") is not None:
            return False
        direction = str(existing.get("prediction") or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return False

        existing_audit = existing.get("audit") or {}
        if not isinstance(existing_audit, dict):
            try:
                existing_audit = json.loads(existing_audit) if isinstance(existing_audit, str) else {}
            except Exception:
                return False
        origin = existing_audit.get("origin_price_v1")
        if not isinstance(origin, dict) or origin.get("version") != "origin-price-v1":
            return False
        expected_source = normalize_exchange(existing.get("exchange_used"))
        if expected_source is None or normalize_exchange(origin.get("source")) != expected_source:
            return False
        row_ts = existing.get("ts")
        row_dt = parse_utc(row_ts)
        if row_dt is None or parse_utc(origin.get("timestamp")) != row_dt:
            return False
        try:
            if not math.isclose(float(origin.get("price")), float(existing.get("price_now")), rel_tol=1e-9, abs_tol=1e-9):
                return False
        except (TypeError, ValueError):
            return False

        if not validate_price_evidence(
            price_evidence_15m,
            expected_exchange=expected_source,
            expected_symbol=normalize_symbol(existing.get("symbol")),
            expected_ts=row_ts,
            expected_window_seconds=WINDOW_15M_S,
        ):
            return False
        if not validate_price_evidence(
            price_evidence_1h,
            expected_exchange=expected_source,
            expected_symbol=normalize_symbol(existing.get("symbol")),
            expected_ts=row_ts,
            expected_window_seconds=WINDOW_1H_S,
        ):
            return False
        try:
            if not math.isclose(float(price_15m_later), float(price_evidence_15m["price"]), rel_tol=1e-9, abs_tol=1e-9):
                return False
            if not math.isclose(float(price_1h_later), float(price_evidence_1h["price"]), rel_tol=1e-9, abs_tol=1e-9):
                return False
        except (TypeError, ValueError, KeyError):
            return False
        observed_at = datetime.now(timezone.utc)
        if observed_at < row_dt + timedelta(seconds=WINDOW_1H_S):
            return False

        observed_iso = observed_at.isoformat()
        outcomes_dual = {
            "outcome_15m": outcome_15m,
            "outcome_1h": outcome_1h,
            "price_15m_later": float(price_15m_later),
            "price_1h_later": float(price_1h_later),
            "primary_window": primary_window,
            "settled_at": observed_iso,
            "settlement_contract_version": "aud063-v1",
            "price_evidence_v1": {
                "15m": dict(price_evidence_15m),
                "1h": dict(price_evidence_1h),
            },
            "settlement_observation_v1": {
                "version": "settlement-observation-v1",
                "observed_at": observed_iso,
                "writer": "SENEX_PRIMARY_DUAL_WINDOW_VERIFIER_V2",
                "availability_semantics": "PERSISTED_BY_COMPARE_AND_SET_AT_OR_AFTER_THIS_TIME",
            },
        }
        merged_audit = dict(existing_audit)
        merged_audit["outcomes_dual"] = outcomes_dual
        patch_body = {
            "outcome": outcome_1h,
            "price_15m_later": float(price_15m_later),
            "audit": merged_audit,
        }
        r = await c.patch(
            f"/{SUPABASE_TABLE}",
            params={
                "id": f"eq.{prediction_id}",
                "outcome": "is.null",
                "audit->outcomes_dual": "is.null",
            },
            json=patch_body,
        )
        if r.status_code not in (200, 204):
            return False
        try:
            body = r.json() if getattr(r, "content", b"") else []
        except Exception:
            body = []
        return isinstance(body, list) and len(body) > 0
    except Exception as e:
        log.error("supabase update_outcome_dual error: %s", e)
        return False


async def close() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
