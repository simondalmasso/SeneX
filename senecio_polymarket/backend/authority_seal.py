"""Durable non-D1 authority seals for SENEX ORDER076-R8.

Candidate-only. The store is a local filesystem bundle and performs no network I/O.
Normal runtime never bootstraps from D1 unless the explicit one-shot bootstrap flag
is present and the per-scope cooldown permits it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SEAL_CONTRACT = "senex-authority-durable-seal-v1"
COUNT_CONTRACT = "senex-authority-durable-count-v1"
DEFAULT_MAX_AGE_S = 7 * 24 * 3600
DEFAULT_BOOTSTRAP_COOLDOWN_S = 6 * 3600
FUTURE_SKEW_S = 300


class AuthoritySealError(RuntimeError):
    pass


class AuthoritySealMissingError(AuthoritySealError):
    pass


class AuthoritySealCorruptError(AuthoritySealError):
    pass


class AuthoritySealStaleError(AuthoritySealError):
    pass


class AuthorityBootstrapRateLimitedError(AuthoritySealError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise AuthoritySealCorruptError("AUTHORITY_SEAL_TIMESTAMP_MISSING")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception as exc:
        raise AuthoritySealCorruptError("AUTHORITY_SEAL_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        raise AuthoritySealCorruptError("AUTHORITY_SEAL_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def _sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _running_from_app_root() -> bool:
    normalized = str(Path(__file__).resolve()).replace("\\", "/")
    return normalized.startswith("/app/")


def _root() -> Path:
    configured = str(os.environ.get("SENEX_AUTHORITY_SEAL_DIR") or "").strip()
    if configured:
        return Path(configured)
    # Northflank H011 has a bound persistent volume at /app/polymarket/results.
    # Prefer it automatically so container replacement/redeploy does not lose
    # the authority seal/count/bootstrap guard when an explicit env override is
    # absent. If a deployed /app runtime ever loses that mount, fail closed
    # instead of silently falling back to the container's ephemeral filesystem.
    persistent_results = Path("/app/polymarket/results")
    if persistent_results.is_dir():
        return persistent_results / "authority_seals"
    if _running_from_app_root():
        raise AuthoritySealError("AUTHORITY_SEAL_PERSISTENT_MOUNT_MISSING")
    return Path(__file__).resolve().parents[1] / "data" / "authority_seals"


def _safe_scope(scope: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(scope or "").strip())
    if not cleaned:
        raise AuthoritySealCorruptError("AUTHORITY_SEAL_SCOPE_EMPTY")
    return cleaned


def authority_path(scope: str) -> Path:
    return _root() / f"authority-{_safe_scope(scope)}.json"


def count_path() -> Path:
    return _root() / "count-global.json"


def guard_path(scope: str) -> Path:
    return _root() / f"bootstrap-guard-{_safe_scope(scope)}.json"


def _row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    ts = str(row.get("ts") or "")
    raw_id = row.get("id")
    try:
        rid: Any = (0, int(raw_id))
    except Exception:
        rid = (1, str(raw_id or ""))
    return ts, rid


def _cursor_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    last = rows[-1]
    return {"ts": str(last.get("ts") or ""), "id": str(last.get("id") or "")}


def _identity_fields(identity: dict[str, Any]) -> dict[str, str]:
    """Durable seals bind only to the canonical internal artifact identity."""
    return {
        "source_commit": str(identity.get("source_commit") or ""),
        "source_tree": str(identity.get("source_tree") or ""),
        "build_digest": str(identity.get("build_digest") or ""),
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical_json(payload) + b"\n"
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def _validate_time_window(payload: dict[str, Any], *, now: datetime | None, max_age_s: int | None) -> None:
    current = (now or _utcnow()).astimezone(timezone.utc)
    created = _parse_utc(payload.get("created_at"))
    verified = _parse_utc(payload.get("verified_at"))
    if verified < created:
        raise AuthoritySealCorruptError("AUTHORITY_SEAL_VERIFIED_BEFORE_CREATED")
    if (created - current).total_seconds() > FUTURE_SKEW_S or (verified - current).total_seconds() > FUTURE_SKEW_S:
        raise AuthoritySealCorruptError("AUTHORITY_SEAL_TIMESTAMP_IN_FUTURE")
    age_limit = DEFAULT_MAX_AGE_S if max_age_s is None else int(max_age_s)
    if age_limit >= 0 and (current - verified).total_seconds() > age_limit:
        raise AuthoritySealStaleError("AUTHORITY_SEAL_STALE")


def save_authority_state(
    scope: str,
    rows: list[dict[str, Any]],
    cursor: dict[str, Any] | None,
    *,
    identity: dict[str, Any],
    writer_contract: str,
    created_at: str | None = None,
    verified_at: str | None = None,
) -> dict[str, Any]:
    ordered = sorted((dict(row) for row in rows), key=_row_key)
    expected_cursor = _cursor_for_rows(ordered)
    if cursor != expected_cursor:
        raise AuthoritySealCorruptError("AUTHORITY_SEAL_CURSOR_NOT_LAST_ROW")
    now = _utcnow()
    payload: dict[str, Any] = {
        "contract": SEAL_CONTRACT,
        **_identity_fields(identity),
        "scope": str(scope),
        "writer_contract": str(writer_contract),
        "cursor": cursor,
        "row_count": len(ordered),
        "rows_hash": _sha(ordered),
        "created_at": created_at or _iso(now),
        "verified_at": verified_at or _iso(now),
        "rows": ordered,
    }
    payload["seal_hash"] = _sha(payload)
    _atomic_write(authority_path(scope), payload)
    return payload


def load_authority_state(
    scope: str,
    *,
    identity: dict[str, Any],
    writer_contract: str,
    now: datetime | None = None,
    max_age_s: int | None = None,
) -> dict[str, Any]:
    path = authority_path(scope)
    if not path.exists():
        raise AuthoritySealMissingError("AUTHORITY_DURABLE_SEAL_MISSING")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_SEAL_JSON_INVALID") from exc
    if not isinstance(payload, dict) or payload.get("contract") != SEAL_CONTRACT:
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_SEAL_CONTRACT_INVALID")
    if payload.get("scope") != str(scope):
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_SEAL_SCOPE_MISMATCH")
    for key, value in _identity_fields(identity).items():
        if payload.get(key) != value:
            raise AuthoritySealCorruptError(f"AUTHORITY_DURABLE_SEAL_{key.upper()}_MISMATCH")
    if payload.get("writer_contract") != str(writer_contract):
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_SEAL_WRITER_CONTRACT_MISMATCH")
    rows = payload.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_SEAL_ROWS_INVALID")
    ordered = sorted((dict(row) for row in rows), key=_row_key)
    if ordered != rows:
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_SEAL_ROWS_NOT_CANONICAL")
    if int(payload.get("row_count", -1)) != len(rows):
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_SEAL_ROW_COUNT_MISMATCH")
    if payload.get("rows_hash") != _sha(rows):
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_SEAL_ROWS_HASH_MISMATCH")
    if payload.get("cursor") != _cursor_for_rows(rows):
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_SEAL_CURSOR_MISMATCH")
    supplied = payload.get("seal_hash")
    unsigned = dict(payload)
    unsigned.pop("seal_hash", None)
    if supplied != _sha(unsigned):
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_SEAL_HASH_MISMATCH")
    _validate_time_window(payload, now=now, max_age_s=max_age_s)
    return payload


def save_count_state(
    row_count: int,
    cursor: dict[str, Any] | None,
    *,
    identity: dict[str, Any],
    writer_contract: str,
    created_at: str | None = None,
    verified_at: str | None = None,
) -> dict[str, Any]:
    now = _utcnow()
    payload: dict[str, Any] = {
        "contract": COUNT_CONTRACT,
        **_identity_fields(identity),
        "scope": "GLOBAL_EXACT_COUNT",
        "writer_contract": str(writer_contract),
        "cursor": cursor,
        "row_count": int(row_count),
        "created_at": created_at or _iso(now),
        "verified_at": verified_at or _iso(now),
    }
    if payload["row_count"] < 0:
        raise AuthoritySealCorruptError("AUTHORITY_COUNT_NEGATIVE")
    payload["seal_hash"] = _sha(payload)
    _atomic_write(count_path(), payload)
    return payload


def load_count_state(
    *,
    identity: dict[str, Any],
    writer_contract: str,
    now: datetime | None = None,
    max_age_s: int | None = None,
) -> dict[str, Any]:
    path = count_path()
    if not path.exists():
        raise AuthoritySealMissingError("AUTHORITY_DURABLE_COUNT_MISSING")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_COUNT_JSON_INVALID") from exc
    if not isinstance(payload, dict) or payload.get("contract") != COUNT_CONTRACT:
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_COUNT_CONTRACT_INVALID")
    for key, value in _identity_fields(identity).items():
        if payload.get(key) != value:
            raise AuthoritySealCorruptError(f"AUTHORITY_DURABLE_COUNT_{key.upper()}_MISMATCH")
    if payload.get("writer_contract") != str(writer_contract):
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_COUNT_WRITER_CONTRACT_MISMATCH")
    try:
        count = int(payload.get("row_count"))
    except Exception as exc:
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_COUNT_VALUE_INVALID") from exc
    if count < 0:
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_COUNT_NEGATIVE")
    supplied = payload.get("seal_hash")
    unsigned = dict(payload)
    unsigned.pop("seal_hash", None)
    if supplied != _sha(unsigned):
        raise AuthoritySealCorruptError("AUTHORITY_DURABLE_COUNT_HASH_MISMATCH")
    _validate_time_window(payload, now=now, max_age_s=max_age_s)
    return payload


def bootstrap_enabled() -> bool:
    return str(os.environ.get("SENEX_AUTHORITY_BOOTSTRAP_ALLOWED") or "0").strip() == "1"


def assert_bootstrap_permitted(scope: str, *, now: datetime | None = None) -> None:
    if not bootstrap_enabled():
        raise AuthorityBootstrapRateLimitedError("AUTHORITY_BOOTSTRAP_NOT_EXPLICITLY_ENABLED")
    path = guard_path(scope)
    current = (now or _utcnow()).astimezone(timezone.utc)
    cooldown = max(0, int(os.environ.get("SENEX_AUTHORITY_BOOTSTRAP_COOLDOWN_SEC", str(DEFAULT_BOOTSTRAP_COOLDOWN_S))))
    if path.exists():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(prior, dict):
                raise AuthoritySealCorruptError("AUTHORITY_BOOTSTRAP_GUARD_NOT_OBJECT")
            if prior.get("contract") != "senex-authority-bootstrap-guard-v1":
                raise AuthoritySealCorruptError("AUTHORITY_BOOTSTRAP_GUARD_CONTRACT_INVALID")
            if prior.get("scope") != str(scope):
                raise AuthoritySealCorruptError("AUTHORITY_BOOTSTRAP_GUARD_SCOPE_MISMATCH")
            supplied = prior.get("guard_hash")
            unsigned = dict(prior)
            unsigned.pop("guard_hash", None)
            if supplied != _sha(unsigned):
                raise AuthoritySealCorruptError("AUTHORITY_BOOTSTRAP_GUARD_HASH_MISMATCH")
            attempted = _parse_utc(prior.get("attempted_at"))
            if (attempted - current).total_seconds() > FUTURE_SKEW_S:
                raise AuthoritySealCorruptError("AUTHORITY_BOOTSTRAP_GUARD_TIMESTAMP_IN_FUTURE")
        except AuthoritySealError:
            raise
        except Exception as exc:
            raise AuthoritySealCorruptError("AUTHORITY_BOOTSTRAP_GUARD_INVALID") from exc
        if (current - attempted).total_seconds() < cooldown:
            raise AuthorityBootstrapRateLimitedError("AUTHORITY_BOOTSTRAP_RATE_LIMITED")



def record_bootstrap_attempt(scope: str, *, now: datetime | None = None) -> None:
    current = (now or _utcnow()).astimezone(timezone.utc)
    payload = {
        "contract": "senex-authority-bootstrap-guard-v1",
        "scope": str(scope),
        "attempted_at": _iso(current),
    }
    payload["guard_hash"] = _sha(payload)
    _atomic_write(guard_path(scope), payload)
