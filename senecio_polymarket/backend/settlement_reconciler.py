"""SENEX repair-only settlement reconciliation guard.

This module never performs NULL -> WIN/LOSS. It may add missing dual-window
metadata only to an already-settled row, and AUD-063 requires the same bounded,
same-source historical-price evidence as the primary verifier. Legacy rows
without a valid origin witness remain unchanged and non-authoritative.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx

from .settlement_contract import (
    WINDOW_15M_S,
    WINDOW_1H_S,
    directional_outcome,
    fetch_historical_price_evidence,
    normalize_exchange,
    normalize_symbol,
    parse_utc,
)
from .supabase_client import (
    build_supabase_headers,
    get_local_authority_rows,
)
from . import authority_seal as durable_seal

log = logging.getLogger("senex.settlement_reconciler")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_TABLE = os.environ.get("SUPABASE_TABLE", "oracle_predictions")
INTERVAL_S = int(os.environ.get("SETTLEMENT_RECONCILE_INTERVAL_SEC", "900"))
BATCH_LIMIT = int(os.environ.get("SETTLEMENT_RECONCILE_BATCH", "200"))
HEARTBEAT_FILE = Path(os.environ.get("SENEX_RECONCILER_HEARTBEAT_FILE", "/tmp/senex-reconciler-heartbeat"))
CURSOR_CONTRACT = "senex-settlement-reconciler-local-cursor-v1"


def _cursor_file() -> Path:
    return Path(
        os.environ.get("SENEX_RECONCILER_CURSOR_FILE")
        or (durable_seal.runtime_state_dir() / "settlement-reconciler-cursor.json")
    )


def _headers() -> dict[str, str]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be provided by the runtime environment")
    return build_supabase_headers(SUPABASE_KEY)


def _audit_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _cursor_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _load_cursor() -> int:
    cursor_file = _cursor_file()
    if not cursor_file.exists():
        return 0
    payload = json.loads(cursor_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("contract") != CURSOR_CONTRACT:
        raise RuntimeError("SETTLEMENT_RECONCILER_CURSOR_INVALID")
    supplied = str(payload.get("cursor_hash") or "")
    unsigned = dict(payload)
    unsigned.pop("cursor_hash", None)
    if supplied != _cursor_hash(unsigned):
        raise RuntimeError("SETTLEMENT_RECONCILER_CURSOR_HASH_MISMATCH")
    value = int(payload.get("last_id") or 0)
    if value < 0:
        raise RuntimeError("SETTLEMENT_RECONCILER_CURSOR_NEGATIVE")
    return value


def _save_cursor(last_id: int) -> None:
    cursor_file = _cursor_file()
    cursor_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "contract": CURSOR_CONTRACT,
        "last_id": int(last_id),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload["cursor_hash"] = _cursor_hash(payload)
    tmp = cursor_file.with_suffix(cursor_file.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, cursor_file)


async def _repair_row(client: httpx.AsyncClient, row: dict[str, Any]) -> str:
    """Return repaired/conflict/skipped/error without changing primary outcome."""
    stored_outcome = row.get("outcome")
    if stored_outcome not in {"WIN", "LOSS"}:
        return "skipped"
    direction = str(row.get("prediction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return "skipped"
    try:
        origin_price = float(row.get("price_now") or 0)
    except (TypeError, ValueError):
        return "skipped"
    if origin_price <= 0:
        return "skipped"

    audit = _audit_dict(row.get("audit"))
    if audit.get("outcomes_dual") is not None:
        return "skipped"
    origin = audit.get("origin_price_v1")
    source = normalize_exchange(row.get("exchange_used"))
    row_ts = row.get("ts")
    if (
        not isinstance(origin, dict)
        or origin.get("version") != "origin-price-v1"
        or source is None
        or normalize_exchange(origin.get("source")) != source
        or parse_utc(origin.get("timestamp")) != parse_utc(row_ts)
    ):
        return "skipped"

    symbol = normalize_symbol(row.get("symbol"))
    ev15 = await asyncio.to_thread(
        fetch_historical_price_evidence, source, symbol, row_ts, WINDOW_15M_S
    )
    ev1h = await asyncio.to_thread(
        fetch_historical_price_evidence, source, symbol, row_ts, WINDOW_1H_S
    )
    if not ev15 or not ev1h:
        return "error"
    p15 = float(ev15["price"])
    p1h = float(ev1h["price"])
    o15 = directional_outcome(direction, origin_price, p15)
    o1h = directional_outcome(direction, origin_price, p1h)
    if not o15 or not o1h:
        return "skipped"

    # Repair-only means a disagreement is evidence of a historical conflict, not
    # permission to rewrite the settled outcome or fabricate authority.
    if stored_outcome != o1h:
        return "conflict"

    observed_at = datetime.now(timezone.utc).isoformat()
    merged = dict(audit)
    merged["outcomes_dual"] = {
        "outcome_15m": o15,
        "outcome_1h": o1h,
        "price_15m_later": p15,
        "price_1h_later": p1h,
        "primary_window": "1h",
        "settlement_contract_version": "aud063-v1",
        "price_evidence_v1": {"15m": ev15, "1h": ev1h},
        "reconciled_by": "SENEX-SCORE-002-REPAIR-ONLY",
        "reconciled_at": observed_at,
        "settlement_observation_v1": {
            "version": "settlement-observation-v1",
            "observed_at": observed_at,
            "writer": "SENEX_SCORE_002_RECONCILER_REPAIR_ONLY",
            "availability_semantics": "PERSISTED_BY_COMPARE_AND_SET_AT_OR_AFTER_THIS_TIME",
        },
    }
    response = await client.patch(
        f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}",
        params={
            "id": f"eq.{row['id']}",
            "outcome": f"eq.{stored_outcome}",
            "audit->outcomes_dual": "is.null",
        },
        json={"price_15m_later": p15, "audit": merged},
    )
    try:
        body = response.json() if response.content else []
    except Exception:
        body = []
    repaired = response.status_code in (200, 204) and isinstance(body, list) and bool(body)
    if not repaired:
        return "error"
    # The uvicorn authority loop owns the durable seal. The reconciler is a
    # separate process, so it never writes that seal; the next bounded exact-ID
    # mutable refresh imports this D1 repair without a cross-process lost update.
    return "repaired"


async def reconcile_once() -> dict[str, int]:
    """Repair a bounded local candidate batch; never scan D1 for backlog."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be provided by the runtime environment")
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=WINDOW_1H_S)
    counters = {
        "scanned": 0,
        "d1_rows_read": 0,
        "local_rows_considered": 0,
        "repaired": 0,
        "skipped": 0,
        "errors": 0,
        "conflicts": 0,
        "scan_cap_hit": 0,
    }
    try:
        cursor_id = _load_cursor()
    except Exception:
        counters["errors"] += 1
        log.exception("settlement reconciler cursor invalid; failing closed")
        return counters

    rows = get_local_authority_rows(require_current_identity=True)
    counters["local_rows_considered"] = len(rows)
    eligible: list[dict[str, Any]] = []
    for row in rows:
        try:
            row_id = int(row.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if row_id <= cursor_id:
            continue
        if row.get("outcome") not in {"WIN", "LOSS"}:
            continue
        audit = _audit_dict(row.get("audit"))
        if audit.get("outcomes_dual") is not None:
            continue
        row_dt = parse_utc(row.get("ts"))
        if row_dt is None or row_dt > cutoff:
            continue
        eligible.append(dict(row))
    eligible.sort(key=lambda row: int(row.get("id") or 0))
    candidates = eligible[: max(1, BATCH_LIMIT)]
    counters["scan_cap_hit"] = int(len(eligible) > len(candidates))

    async with httpx.AsyncClient(timeout=20.0, headers=_headers()) as client:
        for row in candidates:
            counters["scanned"] += 1
            result = await _repair_row(client, row)
            key = {
                "repaired": "repaired",
                "conflict": "conflicts",
                "skipped": "skipped",
                "error": "errors",
            }[result]
            counters[key] += 1
            if result == "error":
                break
            cursor_id = int(row["id"])
            _save_cursor(cursor_id)

    log.info("settlement reconciliation complete: %s", counters)
    return counters


async def daemon() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SENEX-SCORE-002 reconciler requires SUPABASE_URL and SUPABASE_KEY")
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    while True:
        result = await reconcile_once()
        HEARTBEAT_FILE.touch()
        log.info("SENEX-SCORE-002 reconciliation heartbeat updated: %s", result)
        await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(daemon())