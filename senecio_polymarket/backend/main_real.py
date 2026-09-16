"""Production public runtime wiring — ORDER-070-R1 truth boundary.

Public FastAPI is observational only. Mutating/control routes live exclusively
in ``backend.admin:admin_app`` and are never mounted by the production launcher.
All authority-bearing public surfaces consume one cached atomic
``AuthoritySnapshot`` per symbol instead of independently reading Supabase.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import runtime_shared as shared
from . import oracle_runner
from .authority_snapshot import STORE as authority_store, normalize_symbol
from .authoritative_score import build_authoritative_score
from .boros_market_adapter import get_boros_adapter
from .kalshi_market_adapter import get_kalshi_adapter
from .paper_lock import safety_projection
from .paper_view import paper_state
from .polymarket_market_adapter import get_polymarket_adapter
from .runtime_provenance import runtime_provenance
from .readiness_contract import build_readiness_contract

log = logging.getLogger("senecio.main_real")
_poly = get_polymarket_adapter()
_boros = get_boros_adapter()
_kalshi = get_kalshi_adapter()

SAFE_PUBLIC_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_AUTHORITY_SYMBOL = "BTCUSDT"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _validate_public_symbol(value: str | None) -> str:
    normalized = normalize_symbol(value)
    if normalized != PUBLIC_AUTHORITY_SYMBOL:
        raise HTTPException(status_code=404, detail="PUBLIC_AUTHORITY_SYMBOL_NOT_ALLOWED")
    return normalized


def synthetic_demo_enabled() -> bool:
    return (os.environ.get("SENEX_ENABLE_SYNTHETIC_DEMO") or "").strip().lower() in {"1", "true", "yes", "on"}


def quarantine_legacy_outcome_backfill() -> None:
    """Disable obsolete historical WIN/LOSS rewrite path; no data mutation."""
    oracle_runner._state["bogus_backfill_done"] = True
    oracle_runner._state["bogus_backfill_count"] = 0
    oracle_runner._state["bogus_backfill_errors"] = 0
    oracle_runner._state["legacy_backfill_quarantined"] = True


@asynccontextmanager
async def real_lifespan(public_app: FastAPI):
    public_app.state.audit = shared._audit
    public_app.state.bus = shared._bus
    public_app.state.retriever = shared._retriever
    public_app.state.scanner_a = shared._scanner_a
    public_app.state.scanner_b = shared._scanner_b
    public_app.state.wallet_tracker = shared._wallet_tracker
    public_app.state.engine = shared._engine
    public_app.state.executor = shared._executor
    public_app.state.scheduler = shared._scheduler

    demo = synthetic_demo_enabled()
    if demo:
        log.warning("SENEX synthetic demo scheduler EXPLICITLY ENABLED")
        shared._scheduler.start()
    else:
        log.info("SENEX production mode: synthetic scheduler disabled")

    await asyncio.gather(_poly.start(), _boros.start(), _kalshi.start())
    quarantine_legacy_outcome_backfill()
    authority_store.clear()
    oracle_runner.start()

    # R4: establish and continuously revalidate authority during controlled
    # runtime lifecycle. Public readiness remains observational and never
    # triggers a refresh itself. Failed refreshes retain the immutable last-good
    # generation and are surfaced through refresh status.
    try:
        await _snapshot("BTCUSDT", force=True)
    except Exception as exc:
        log.warning("initial authority snapshot unavailable: %s", exc)
    authority_refresh_task = asyncio.create_task(_authority_refresh_loop("BTCUSDT"))

    log.info("SENEX public read-only runtime up")
    try:
        yield
    finally:
        authority_refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await authority_refresh_task
        await oracle_runner.stop()
        await asyncio.gather(_kalshi.stop(), _boros.stop(), _poly.stop())
        if demo:
            await shared._scheduler.stop()
        await shared._bus.close()


def _build_public_app() -> FastAPI:
    public = FastAPI(
        title="SENEX PUBLIC READ-ONLY",
        version="ORDER-070-R6",
        lifespan=real_lifespan,
    )
    if FRONTEND_DIR.exists():
        public.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
    return public


app = _build_public_app()


@app.get("/", include_in_schema=False)
async def dashboard_root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"error": "frontend not built"}, status_code=404)


@app.middleware("http")
async def public_method_guard(request: Request, call_next):
    if request.method.upper() not in SAFE_PUBLIC_METHODS:
        return JSONResponse(
            {"detail": "PUBLIC_READ_ONLY_METHOD_DENIED"},
            status_code=405,
            headers={"X-Senex-Public-Decision": "DENY_UNSAFE_METHOD"},
        )
    response = await call_next(request)
    response.headers.setdefault("X-Senex-Public-Decision", "ALLOW_READ_ONLY")
    return response


def _locked_gate_without_coordinator(score: dict[str, Any]) -> dict[str, Any]:
    return {
        "unlocked": False,
        "trade_mode": "PAPER",
        "live_capital_locked": True,
        "orders_enabled": False,
        "diagnostic_only": True,
        "effective_gate": "LOCKED_COORDINATOR_UNAVAILABLE",
        "failed_reasons": ["PORTFOLIO_COORDINATOR_UNAVAILABLE", "LIVE_CAPITAL_LOCKED_BY_PAPER_POLICY"],
        "verified": int(score.get("independent_1h_rows") or 0),
        "proof_qualified_rows_raw": int(score.get("proof_qualified_rows_raw") or 0),
        "authority_cohort": score.get("authority_cohort"),
        "authority_n_source": (score.get("authority_1h") or {}).get("n_source"),
    }


def _live_gate_from_score(score: dict[str, Any]) -> dict[str, Any]:
    coord = shared._get_coordinator()
    if coord is None:
        return _locked_gate_without_coordinator(score)
    state = shared._paper_locked_live_gate_from_score(coord, score)
    state["orders_enabled"] = False
    return state


def _authority_refresh_delay(interval_s: float, elapsed_s: float) -> float:
    """Keep refresh attempt cadence start-to-start so capture latency cannot consume TTL headroom."""
    return max(0.1, float(interval_s) - max(0.0, float(elapsed_s)))


async def _authority_refresh_loop(symbol: str = "BTCUSDT") -> None:
    interval = authority_store.refresh_interval_s()
    loop = asyncio.get_running_loop()
    # Startup already performs one forced capture; preserve the initial spacing.
    await asyncio.sleep(interval)
    while True:
        started = loop.time()
        try:
            await _snapshot(symbol, force=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The store records failure separately and retains last-known-good.
            log.warning("authority snapshot refresh failed for %s: %s", symbol, exc)
        elapsed = max(0.0, loop.time() - started)
        await asyncio.sleep(_authority_refresh_delay(interval, elapsed))


async def _snapshot(symbol: str = "BTCUSDT", *, force: bool = False):
    normalized = _validate_public_symbol(symbol)
    if force:
        return await authority_store.get(
            normalized,
            live_gate_builder=_live_gate_from_score,
            force=True,
        )
    snap, refresh = authority_store.observe(normalized)
    if snap is None:
        raise HTTPException(status_code=503, detail="NO_VALID_AUTHORITY_GENERATION")
    if refresh.get("snapshot_stale") or refresh.get("last_refresh_error") is not None:
        raise HTTPException(status_code=503, detail="AUTHORITY_GENERATION_STALE_OR_ERROR")
    return snap


# Compatibility callable retained for established unit tests. Public routing uses
# the shared snapshot function below.
async def authoritative_oracle_score(symbol: str | None = Query(default=None)):
    from . import supabase_client
    normalized_symbol = normalize_symbol(symbol) if symbol else None
    rows = await supabase_client.fetch_authority_history(symbol=normalized_symbol)
    score = build_authoritative_score(rows, oracle_runner.get_state(), symbol=normalized_symbol)
    score["authority_history_complete"] = True
    score["authority_history_rows"] = len(rows)
    return score


def _authority_observation_payload(snap, refresh: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Expose verified last-known-good authority with explicit freshness state."""
    stale = bool(refresh.get("snapshot_stale"))
    refresh_error = refresh.get("last_refresh_error")
    degraded = stale or refresh_error is not None
    error_text = str(refresh_error or "")
    error_upper = error_text.upper()
    if "D1_QUOTA_EXCEEDED" in error_upper or "DAILY ROW READ LIMIT" in error_upper:
        upstream_error_class = "D1_QUOTA_EXCEEDED"
    elif refresh_error is not None:
        upstream_error_class = error_text
    else:
        upstream_error_class = None
    return {
        **dict(payload),
        "degraded": degraded,
        "fresh": not degraded,
        "last_success_at": refresh.get("last_refresh_success_at"),
        "snapshot_age_s": refresh.get("snapshot_age_s"),
        "upstream_error_class": upstream_error_class,
        "authority_snapshot_id": snap.snapshot_id,
        "authority_generation": snap.generation,
        "authority_canonical_sha256": snap.canonical_sha256,
        "provenance": dict(snap.provenance),
        "safety": safety_projection(),
    }


def _observe_authority_or_503(symbol: str):
    normalized = _validate_public_symbol(symbol)
    snap, refresh = authority_store.observe(normalized)
    if snap is None:
        raise HTTPException(status_code=503, detail="NO_VALID_AUTHORITY_GENERATION")
    return snap, refresh


@app.get("/api/oracle/score")
async def public_authoritative_oracle_score(symbol: str = Query(default="BTCUSDT")):
    snap, refresh = _observe_authority_or_503(symbol)
    return _authority_observation_payload(snap, refresh, dict(snap.score))


@app.get("/api/portfolio/live_gate")
async def public_live_gate(symbol: str = Query(default="BTCUSDT")):
    snap, refresh = _observe_authority_or_503(symbol)
    return _authority_observation_payload(snap, refresh, dict(snap.live_gate))


@app.get("/api/oracle/state")
async def public_oracle_state(symbol: str = Query(default="BTCUSDT")):
    snap, refresh = _observe_authority_or_503(symbol)
    state = oracle_runner.get_state()
    payload = {
        **state,
        "last_prediction": state.get("last_prediction_result"),
        "authority": {
            "symbol": snap.symbol,
            "independent_1h_rows": snap.score.get("independent_1h_rows"),
            "proof_qualified_rows_raw": snap.score.get("proof_qualified_rows_raw"),
            "authority_1h": snap.score.get("authority_1h"),
            "history_complete": snap.authority_history_complete,
            "history_rows": snap.authority_history_rows,
        },
        "exact_total_predictions": snap.exact_total_predictions,
        "exact_count_complete": snap.exact_count_complete,
    }
    return _authority_observation_payload(snap, refresh, payload)


@app.get("/api/authority/snapshot")
async def public_authority_snapshot(symbol: str = Query(default="BTCUSDT")):
    snap, refresh = _observe_authority_or_503(symbol)
    payload = snap.to_dict(refresh)
    payload["degraded"] = bool(refresh.get("snapshot_stale") or refresh.get("last_refresh_error") is not None)
    payload["fresh"] = not payload["degraded"]
    payload["upstream_error_class"] = _authority_observation_payload(snap, refresh, {}).get("upstream_error_class")
    payload["safety"] = safety_projection()
    return payload


@app.get("/api/oracle/predictions/db")
async def public_predictions_db(
    limit: int = Query(default=50, ge=1, le=50),
    symbol: str = Query(default="BTCUSDT"),
):
    snap, refresh = _observe_authority_or_503(symbol)
    rows = authority_store.recent_predictions(snap.symbol, limit=limit)
    payload = {
        "source": "authority_snapshot_cache",
        "symbol": snap.symbol,
        "bounded": True,
        "limit": int(limit),
        "count": len(rows),
        "total_in_db": snap.exact_total_predictions,
        "exact_count_complete": snap.exact_count_complete,
        "predictions": rows,
    }
    return _authority_observation_payload(snap, refresh, payload)


@app.get("/api/runtime/provenance")
async def public_runtime_provenance():
    return runtime_provenance()


@app.get("/healthz")
async def healthz():
    """Pure process liveness; external dependencies do not redefine liveness."""
    return {
        "status": "alive",
        "probe": "liveness",
        "safety": safety_projection(),
        "provenance": runtime_provenance(),
    }


@app.get("/api/health")
async def compatibility_health():
    return await healthz()


def _readiness_payload(snap, refresh: dict[str, Any]) -> dict[str, Any]:
    """Canonical observational readiness; performs no network/database I/O."""
    runner = oracle_runner.get_state()
    adapters = {
        "polymarket": _poly.snapshot(),
        "kalshi": _kalshi.snapshot(),
        "boros": _boros.snapshot(),
    }
    return build_readiness_contract(
        snap,
        refresh,
        oracle_started=bool(runner.get("started_at")),
        adapters=adapters,
    )


@app.get("/readyz")
async def readyz(symbol: str = Query(default="BTCUSDT")):
    """Observational fail-closed readiness over the current shared generation."""
    normalized = _validate_public_symbol(symbol)
    try:
        snap, refresh = authority_store.observe(normalized)
    except Exception as exc:
        return JSONResponse(
            {"status": "not_ready", "probe": "readiness", "reason": type(exc).__name__},
            status_code=503,
        )
    if snap is None:
        return JSONResponse(
            {
                "status": "not_ready", "probe": "readiness",
                "reason": "NO_VALID_AUTHORITY_GENERATION",
                "authority_snapshot_id": None, "generation": None, "canonical_sha256": None,
                "safety": safety_projection(),
                **refresh,
            },
            status_code=503,
        )
    payload = _readiness_payload(snap, refresh)
    return payload if payload["status"] == "ready" else JSONResponse(payload, status_code=503)


@app.get("/api/market-context")
async def market_context(symbol: str = Query(default="BTCUSDT")):
    snap, refresh = _observe_authority_or_503(symbol)
    readiness = _readiness_payload(snap, refresh)
    payload = {
        "readiness": readiness,
        "mode": "REAL_PLUS_EXPLICIT_DEMO" if synthetic_demo_enabled() else "REAL_ONLY",
        "synthetic_demo_enabled": synthetic_demo_enabled(),
        "polymarket": _poly.snapshot(),
        "kalshi": _kalshi.snapshot(),
        "boros": _boros.snapshot(),
        "oracle": oracle_runner.get_state(),
        "authority": {
            "symbol": snap.symbol,
            "authority_1h": snap.score.get("authority_1h"),
            "score_status": snap.score.get("score_status"),
        },
        "safety": {
            **safety_projection(),
            "allow_live": False,
            "read_only_market_adapters": True,
        },
    }
    observed = _authority_observation_payload(snap, refresh, payload)
    observed["safety"] = {
        **safety_projection(),
        "allow_live": False,
        "read_only_market_adapters": True,
    }
    return observed


# ---------- B8.1 PAPER execution view (observational, read-only) ----------


@app.get("/api/paper/state")
async def public_paper_state():
    """Hypothetical PAPER execution + model-quality view. No I/O, no mutation."""
    return paper_state()


@app.get("/api/paper/trades")
async def public_paper_trades(limit: int = Query(default=20, ge=1, le=50)):
    """Bounded recent paper trades from the shared portfolio journal."""
    payload = paper_state()
    rows = payload.get("recent_trades", {}).get("rows", [])[: int(limit)]
    return {
        "source": "paper_portfolio_journal",
        "bounded": True,
        "limit": int(limit),
        "count": len(rows),
        "hypothetical": True,
        "safety": payload.get("safety"),
        "trades": rows,
    }
