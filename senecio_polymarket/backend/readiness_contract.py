"""Pure H011 readiness contract for SENEX ORDER076-R8 candidate."""
from __future__ import annotations

from typing import Any

from .paper_lock import safety_projection as _safety_projection


def _obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def classify_d1_quota_state(refresh: dict[str, Any] | None) -> dict[str, Any]:
    state = refresh or {}
    error = str(state.get("last_refresh_error") or "")
    upper = error.upper()
    if "D1_QUOTA_EXCEEDED" in upper or "DAILY ROW READ LIMIT" in upper or "QUOTA" in upper:
        return {
            "status": "EXHAUSTED",
            "degraded": True,
            "reason": "D1_QUOTA_EXCEEDED",
            "last_error": error or None,
        }
    if error:
        d1ish = any(token in upper for token in ("AUTHORITY_", "EXACT_COUNT", "D1", "HTTP_", "REQUEST_ERROR"))
        return {
            "status": "DEGRADED" if d1ish else "UNKNOWN_ERROR",
            "degraded": True,
            "reason": error,
            "last_error": error,
        }
    return {"status": "OK", "degraded": False, "reason": None, "last_error": None}


def market_data_freshness(adapters: dict[str, Any] | None) -> dict[str, Any]:
    source = adapters or {}
    out: dict[str, Any] = {}
    fresh_any = False
    for name in ("polymarket", "kalshi", "boros"):
        snap = source.get(name) if isinstance(source, dict) else None
        snap = snap if isinstance(snap, dict) else {}
        status = str(snap.get("status") or snap.get("state") or "UNKNOWN").upper()
        stale = bool(snap.get("stale", False))
        fresh = (status in {"LIVE", "LIVE_WS", "LIVE_REST", "OK", "READY", "CONNECTED"}) and not stale
        fresh_any = fresh_any or fresh
        out[name] = {
            "status": status,
            "fresh": fresh,
            "last_update": snap.get("last_update") or snap.get("last_update_at") or snap.get("updated_at"),
        }
    return {
        "status": "FRESH" if fresh_any else "UNKNOWN_OR_STALE",
        "fresh": fresh_any,
        "required_for_authority_readiness": False,
        "adapters": out,
    }


def build_readiness_contract(
    snapshot: Any,
    refresh: dict[str, Any] | None,
    *,
    oracle_started: bool,
    adapters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    refresh = dict(refresh or {})
    d1 = classify_d1_quota_state(refresh)
    market = market_data_freshness(adapters)

    if snapshot is None:
        authority_history_complete = False
        exact_count_complete = False
        provenance_exact = False
        paper_lock = True
        orders_disabled = True
        snapshot_id = generation = canonical_sha256 = provenance = None
    else:
        authority_history_complete = bool(_obj_get(snapshot, "authority_history_complete", False))
        exact_count_complete = bool(_obj_get(snapshot, "exact_count_complete", False))
        provenance = _obj_get(snapshot, "provenance", {}) or {}
        provenance_exact = bool(_obj_get(provenance, "exact", False))
        live_gate = _obj_get(snapshot, "live_gate", {}) or {}
        paper_lock = _obj_get(live_gate, "trade_mode") == "PAPER" and bool(_obj_get(live_gate, "live_capital_locked", False))
        orders_disabled = _obj_get(live_gate, "orders_enabled", False) is False
        snapshot_id = _obj_get(snapshot, "snapshot_id")
        generation = _obj_get(snapshot, "generation")
        canonical_sha256 = _obj_get(snapshot, "canonical_sha256")

    snapshot_fresh = snapshot is not None and refresh.get("snapshot_stale") is False
    last_refresh_ok = refresh.get("last_refresh_error") is None
    authority_ready = all((
        snapshot is not None,
        authority_history_complete,
        provenance_exact,
        snapshot_fresh,
        last_refresh_ok,
        not d1["degraded"],
    ))

    checks = {
        "authority_history_complete": authority_history_complete,
        "exact_count_complete": exact_count_complete,
        "exact_count_required_for_readiness": False,
        "provenance_exact": provenance_exact,
        "oracle_started": bool(oracle_started),
        "paper_lock": paper_lock,
        "orders_disabled": orders_disabled,
        "snapshot_fresh": snapshot_fresh,
        "last_refresh_ok": last_refresh_ok,
    }
    ready = authority_ready and bool(oracle_started) and paper_lock and orders_disabled
    if d1["status"] == "EXHAUSTED":
        reason = "D1_QUOTA_EXCEEDED"
    elif not authority_ready:
        reason = refresh.get("last_refresh_error") or "AUTHORITY_NOT_READY"
    elif not oracle_started:
        reason = "ORACLE_NOT_STARTED"
    elif not paper_lock or not orders_disabled:
        reason = "SAFETY_CONTRACT_VIOLATION"
    else:
        reason = None

    return {
        "status": "ready" if ready else "not_ready",
        "probe": "readiness",
        "reason": reason,
        "checks": checks,
        "components": {
            "liveness": {"status": "ALIVE", "ready": True},
            "authority_readiness": {
                "status": "READY" if authority_ready else "DEGRADED",
                "ready": authority_ready,
                "fresh_authoritative_score_allowed": authority_ready,
            },
            "market_data_freshness": market,
            "d1_quota_state": d1,
            "collector_liveness": {
                "status": "EXTERNAL_NOT_PROBED_BY_H011",
                "ready": None,
                "required_for_h011_authority_readiness": False,
            },
        },
        "authority_snapshot_id": snapshot_id,
        "generation": generation,
        "canonical_sha256": canonical_sha256,
        "provenance": provenance,
        "safety": _safety_projection(),
        **refresh,
    }
