"""
SENECIO ORACLE — FastAPI Main App
==================================
Wires all 7 layers into a runnable service.

Run:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
    GET  /             — dashboard (static)
    GET  /api/health   — liveness probe
    GET  /api/stats    — bus + scheduler + audit stats
    GET  /api/audit?day=YYYY-MM-DD&limit=100  — recent audit events
    GET  /api/state    — current portfolio + scanner state
    GET  /api/catalog  — symbol catalog
    WS   /ws?type=...  — live event stream
    GET  /sse?type=... — SSE fallback
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
import json

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .audit_store import AuditStore
from .event_bus import EventBus
from .data_retriever import DataRetriever
from .scanner_a import ScannerA
from .scanner_b import ScannerB
from .wallet_tracker import WalletTracker
from .oracle_engine import OracleEngine
from .execution_simulator import ExecutionSimulator
from .scheduler import Scheduler
from .ws_server import make_router as make_ws_router
from . import oracle_runner
from .authoritative_score import build_authoritative_score
from .settlement_proof import filter_proof_qualified, is_proof_qualified
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("senecio.main")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# module-level singletons (created once, used by both lifespan and route handlers)
_audit = AuditStore(root="data/audit")
_bus = EventBus(audit=_audit)
_retriever = DataRetriever(mode="LIVE", seed=42)
_scanner_a = ScannerA()
_scanner_b = ScannerB()
_wallet_tracker = WalletTracker()
_engine = OracleEngine()
_executor = ExecutionSimulator()
_scheduler = Scheduler(
    bus=_bus, retriever=_retriever, scanner_a=_scanner_a, scanner_b=_scanner_b,
    wallet_tracker=_wallet_tracker, engine=_engine, executor=_executor,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.audit = _audit
    app.state.bus = _bus
    app.state.retriever = _retriever
    app.state.scanner_a = _scanner_a
    app.state.scanner_b = _scanner_b
    app.state.wallet_tracker = _wallet_tracker
    app.state.engine = _engine
    app.state.executor = _executor
    app.state.scheduler = _scheduler

    _scheduler.start()
    oracle_runner.start()
    log.info("SENECIO ORACLE backend up — layers: data, scanner_a, scanner_b, wallet, brain, exec, ws + REAL ORACLE")

    yield

    await oracle_runner.stop()
    await _scheduler.stop()
    await _bus.close()
    log.info("SENECIO ORACLE backend down")


app = FastAPI(title="SENECIO ORACLE", version="ACT-XXIX-systemic-antifragility", lifespan=lifespan)

# WebSocket / SSE router
app.include_router(make_ws_router(_bus))


# ---- REST endpoints ----
@app.get("/api/health")
async def health():
    """Real health check — includes oracle runner state."""
    oracle_state = oracle_runner.get_state()
    # Best-effort Supabase count (don't fail health if DB unreachable)
    sb_total = 0
    try:
        from . import supabase_client
        sb_total = await supabase_client.count_predictions()
    except Exception:
        pass
    return {
        "status": "ok",
        "version": "ACT-XXIX-systemic-antifragility",
        "oracle": {
            "started_at": oracle_state.get("started_at"),
            "last_prediction_ts": oracle_state.get("last_prediction_ts"),
            "last_prediction_symbol": oracle_state.get("last_prediction_symbol"),
            "predictions_count": oracle_state.get("predictions_count", 0),
            "cycles_run": oracle_state.get("cycles_run", 0),
            "cycles_failed": oracle_state.get("cycles_failed", 0),
            "last_error": oracle_state.get("last_error"),
            "last_cycle_at": oracle_state.get("last_cycle_at"),
            "next_cycle_at": oracle_state.get("next_cycle_at"),
            "exchange_used_last": oracle_state.get("exchange_used_last"),
            "supabase_total": sb_total,
        },
    }


@app.get("/api/oracle/state")
async def oracle_state():
    """Detailed oracle runner state + last prediction."""
    state = oracle_runner.get_state()
    return {
        **state,
        "last_prediction": state.get("last_prediction_result"),
    }


@app.get("/api/oracle/predictions")
async def oracle_predictions(limit: int = Query(default=20, le=200)):
    """Return last N predictions from predictions.jsonl (most recent first)."""
    from pathlib import Path
    pred_path = Path(__file__).resolve().parent.parent / "oracle" / "senecio_output" / "predictions.jsonl"
    if not pred_path.exists():
        return {"count": 0, "predictions": []}
    rows = []
    with open(pred_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    # most recent first
    rows.reverse()
    # strip _audit for slim view (client can request /api/oracle/predictions/full for it)
    slim = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows[:limit]]
    return {"count": len(slim), "total_in_file": len(rows), "predictions": slim}


@app.get("/api/oracle/predictions/db")
async def oracle_predictions_db(limit: int = Query(default=50, le=500), symbol: str | None = None):
    """Read predictions directly from Supabase — survives container redeploys."""
    from . import supabase_client
    rows = await supabase_client.fetch_predictions(limit=limit, symbol=symbol)
    total = await supabase_client.count_predictions()
    return {
        "source": "supabase",
        "count": len(rows),
        "total_in_db": total,
        "predictions": rows,
    }


@app.get("/api/oracle/score")
async def oracle_score():
    """Oracle accuracy score computed from Supabase (verified predictions only).

    ACT XXIII: now returns per-direction × per-window breakdown + gate states.
    The LONG vs SHORT asymmetry is a critical GO/NO-GO signal for live capital.

    Returns:
      - total_predictions: count of all rows in Supabase
      - verified: count of rows with WIN/LOSS outcome (= outcome_1h, the gating column)
      - wins/losses/win_rate_pct: aggregate across all verified
      - by_direction: per-direction breakdown using outcome_1h (primary column)
      - by_window: {15m: {LONG, SHORT, FLAT, global}, 1h: {...}} — 15m reads from
                   audit.outcomes_dual.outcome_15m, 1h reads primary `outcome` column
      - gates: {long_1h, short_1h, global_1h} with pass/fail + thresholds + n
      - short_only_paper_mode: True when SHORT passes 1h gate but LONG fails
      - trade_mode: "PAPER" (always, per ACT XXIII directive 5 — no live capital)
      - live_capital_locked: True (hard guard)
    """
    from . import supabase_client
    from . import oracle_runner
    # Get all predictions with outcome filled
    rows = await supabase_client.fetch_predictions(limit=500)
    verified = filter_proof_qualified(rows)
    wins = sum(1 for r in verified if r.get("outcome") == "WIN")
    losses = sum(1 for r in verified if r.get("outcome") == "LOSS")
    win_rate = (wins / len(verified) * 100) if verified else 0.0

    # Per-direction breakdown — surfaces edge asymmetry that the aggregate
    # win_rate hides (e.g. LONG 0% + SHORT 82% averages to 46% which looks
    # mediocre but actually means LONG is broken and SHORT is the alpha).
    by_direction: dict[str, dict] = {}
    for direction in ("LONG", "SHORT", "FLAT"):
        sub = [r for r in verified if (r.get("prediction") or "").upper() == direction]
        sub_w = sum(1 for r in sub if r.get("outcome") == "WIN")
        sub_l = sum(1 for r in sub if r.get("outcome") == "LOSS")
        sub_decided = sub_w + sub_l
        by_direction[direction] = {
            "verified": sub_decided,
            "wins": sub_w,
            "losses": sub_l,
            "win_rate_pct": round((sub_w / sub_decided * 100) if sub_decided > 0 else 0.0, 2),
        }

    # ACT XXIII: pull full state from oracle_runner (directional stats + gates)
    runner_state = oracle_runner.get_state()
    by_window = runner_state.get("directional_stats", {}).get("by_window", {})
    gates = runner_state.get("gates", {})
    short_only_paper_mode = runner_state.get("short_only_paper_mode", False)
    trade_mode = runner_state.get("trade_mode", "PAPER")
    live_capital_locked = runner_state.get("live_capital_locked", True)

    return {
        "version": "ACT-XXIX-systemic-antifragility",
        "total_predictions": len(rows),
        "verified": len(verified),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "by_direction": by_direction,           # 1h-window primary breakdown (backward compat)
        "by_window": by_window,                 # ACT XXIII: {15m: {...}, 1h: {...}}
        "gates": gates,                         # ACT XXIII: directional GO/NO-GO
        "short_only_paper_mode": short_only_paper_mode,
        "trade_mode": trade_mode,               # always "PAPER" per directive 5
        "live_capital_locked": live_capital_locked,
    }


@app.get("/api/stats")
async def stats():
    return {
        "scheduler": _scheduler.stats(),
        "bus": _bus.stats(),
        "audit": _audit.stats(),
        "executor": _executor.risk_state(),
        "wallet_tracker": _wallet_tracker.stats(),
        "cursors": _retriever.cursor_state(),
    }


# ---- ACT-XXV: Portfolio endpoints ----

def _get_coordinator():
    """Lazy access to the portfolio coordinator from oracle_runner."""
    try:
        return oracle_runner._get_portfolio_coordinator()
    except Exception:
        return None


def _paper_locked_live_gate_from_score(coord, score: dict) -> dict:
    """Evaluate one already-built authority score under the immutable PAPER lock."""
    status = coord.evaluate_live_gate(oracle_score=score)
    status.unlocked = False
    status.trade_mode = "PAPER"
    status.live_capital_locked = True
    policy_reason = "LIVE_CAPITAL_LOCKED_BY_PAPER_POLICY"
    if policy_reason not in status.failed_reasons:
        status.failed_reasons.append(policy_reason)

    state = status.to_dict()
    conditions = state.get("conditions") or {}
    state.update({
        "diagnostic_only": True,
        "effective_gate": "LOCKED_BY_PAPER_POLICY",
        "paper_only": True,
        "score_scope": score.get("score_scope"),
        "requested_symbol": score.get("requested_symbol"),
        "authority_cohort": score.get("authority_cohort"),
        "authority_n_source": (score.get("authority_1h") or {}).get("n_source"),
        "verified": int(score.get("independent_1h_rows") or 0),
        "proof_qualified_rows_raw": int(score.get("proof_qualified_rows_raw") or 0),
        "conditions_passed": sum(
            1 for item in conditions.values()
            if isinstance(item, dict) and item.get("pass")
        ),
        "conditions_total": len(conditions),
    })
    return state


def _paper_locked_live_gate_state(coord, rows: list[dict], *, symbol: str) -> dict:
    """Compatibility wrapper; public runtime builds score exactly once per snapshot."""
    score = build_authoritative_score(rows, symbol=symbol)
    return _paper_locked_live_gate_from_score(coord, score)


def _normalize_authority_symbol(symbol: str) -> str:
    """Normalize the instrument key used at the evidence-query boundary."""
    return str(symbol or "").upper().replace("/", "").replace("-", "").strip()


async def _symbol_scoped_paper_live_gate_state(coord, *, symbol: str) -> dict:
    """Fetch one symbol before the bounded limit, then apply the PAPER lock."""
    from . import supabase_client

    normalized_symbol = _normalize_authority_symbol(symbol)
    rows = []
    history_complete = False
    if normalized_symbol:
        try:
            rows = await supabase_client.fetch_authority_history(symbol=normalized_symbol)
            history_complete = True
        except Exception as e:
            log.warning("symbol-scoped complete authority fetch failed for %s: %s", normalized_symbol, e)
    state = _paper_locked_live_gate_state(
        coord,
        rows,
        symbol=normalized_symbol,
    )
    state["authority_history_complete"] = history_complete
    state["authority_history_rows"] = len(rows)
    if not history_complete:
        state["effective_gate"] = "LOCKED_BY_INCOMPLETE_AUTHORITY_HISTORY"
        failed = state.setdefault("failed_reasons", [])
        if "AUTHORITY_HISTORY_INCOMPLETE" not in failed:
            failed.append("AUTHORITY_HISTORY_INCOMPLETE")
    return state


@app.get("/api/portfolio/state")
async def portfolio_state():
    """ACT-XXV/XXVI: full portfolio subsystem snapshot (includes microstructure + regime_hmm)."""
    coord = _get_coordinator()
    if coord is None:
        return {"error": "portfolio coordinator not initialized", "version": "ACT-XXIX-systemic-antifragility"}
    return coord.get_state()


@app.get("/api/portfolio/analytics")
async def portfolio_analytics():
    """ACT-XXV: Sharpe / Sortino / PF / Expectancy / Recovery / Calmar / Kelly / MaxDD."""
    coord = _get_coordinator()
    if coord is None:
        return {"error": "portfolio coordinator not initialized"}
    return coord.get_analytics()


@app.get("/api/portfolio/trades")
async def portfolio_trades(limit: int = Query(default=50, le=500)):
    """ACT-XXV: recent closed trades from the journal."""
    coord = _get_coordinator()
    if coord is None:
        return {"error": "portfolio coordinator not initialized", "trades": []}
    return {"count": limit, "trades": coord.get_recent_trades(limit=limit)}


@app.get("/api/portfolio/audit")
async def portfolio_audit(limit: int = Query(default=50, le=500)):
    """ACT-XXV: recent execution audit events (order lifecycle)."""
    coord = _get_coordinator()
    if coord is None:
        return {"error": "portfolio coordinator not initialized", "events": []}
    return {"count": limit, "events": coord.get_audit_log(limit=limit)}


@app.get("/api/portfolio/shadow")
async def portfolio_shadow():
    """ACT-XXV: ShadowLive status + recent shadow trades."""
    coord = _get_coordinator()
    if coord is None:
        return {"error": "portfolio coordinator not initialized"}
    return {
        "status": coord.shadow_live.stats(),
        "report": coord.shadow_live.generate_report(),
        "recent_trades": coord.shadow_live.fetch_trades(limit=20),
    }


@app.get("/api/portfolio/microstructure")
async def portfolio_microstructure():
    """ACT-XXVI: microstructure intelligence snapshot.

    Returns the most recent MicrostructureReport with:
      - toxic_score (0..1 composite)
      - VPIN, OFI normalized, liquidation cluster proximity
      - funding/OI extremity flags
      - action recommendation (ALLOW / REDUCE / REJECT)
    """
    coord = _get_coordinator()
    if coord is None:
        return {"error": "portfolio coordinator not initialized"}
    return {
        "version": "ACT-XXIX-systemic-antifragility",
        "report": coord.get_microstructure_report(),
        "stats": coord.microstructure.stats(),
    }


@app.get("/api/portfolio/regime_hmm")
async def portfolio_regime_hmm():
    """ACT-XXVI: HMM regime overlay — probabilistic belief over BULL/BEAR/HIGH_VOL.

    Returns the most recent RegimeBelief with:
      - probabilities: {BULL, BEAR, HIGH_VOL} posterior
      - dominant state
      - entropy (uncertainty)
      - transition_risk_to_bear (prob of flipping bearish next step)
      - long_bias / short_bias / size_mult
    """
    coord = _get_coordinator()
    if coord is None:
        return {"error": "portfolio coordinator not initialized"}
    return {
        "version": "ACT-XXIX-systemic-antifragility",
        "belief": coord.get_regime_belief(),
        "stats": coord.regime_hmm.stats(),
    }


@app.get("/api/portfolio/meta_labeler")
async def portfolio_meta_labeler():
    """ACT-XXVI: meta-labeler (triple-barrier LONG-side filter) stats.

    Returns per-direction outcome counts + current loss streaks +
    ML-readiness flag (becomes True once 300+ LONG outcomes recorded).
    """
    coord = _get_coordinator()
    if coord is None:
        return {"error": "portfolio coordinator not initialized"}
    return {
        "version": "ACT-XXIX-systemic-antifragility",
        "stats": coord.meta_labeler.stats(),
    }


@app.get("/api/portfolio/live_gate")
async def portfolio_live_gate(symbol: str = Query(default="BTCUSDT")):
    """ACT-XXV: evaluate the 6 LIVE_GATE unlock conditions.

    Returns the current gate status. The gate stays LOCKED (PAPER mode)
    until ALL 6 conditions pass simultaneously:
      1. global_win_rate_pct >= 52
      2. verified >= 300
      3. profit_factor > 1.20
      4. max_drawdown_pct < 10
      5. shadow_live_passed = True
      6. execution_engine_verified = True
    """
    coord = _get_coordinator()
    if coord is None:
        return {"error": "portfolio coordinator not initialized"}
    return await _symbol_scoped_paper_live_gate_state(coord, symbol=symbol)


@app.post("/api/portfolio/kill_switch")
async def portfolio_kill_switch(reason: str = "manual API trigger"):
    """ACT-XXV: manually trip the kill switch (halts all new trades)."""
    coord = _get_coordinator()
    if coord is None:
        return {"error": "portfolio coordinator not initialized"}
    coord.trip_kill_switch(reason)
    return {"status": "kill_switch_tripped", "reason": reason}


@app.post("/api/portfolio/reset_kill_switch")
async def portfolio_reset_kill_switch(reason: str = "manual API reset"):
    """ACT-XXV: manually reset the kill switch (requires explicit action)."""
    coord = _get_coordinator()
    if coord is None:
        return {"error": "portfolio coordinator not initialized"}
    coord.reset_kill_switch(reason)
    return {"status": "kill_switch_reset", "reason": reason}


@app.get("/api/audit")
async def audit_events(
    day: str | None = Query(default=None),
    limit: int = Query(default=100, le=1000),
    tail: bool = Query(default=False),
    type: str | None = Query(default=None),
):
    """If tail=True, return the most recent `limit` events (chronologically reversed).
    If type=... is provided, filter by event_type."""
    if tail:
        # collect all, then take last N (with optional type filter)
        all_events = []
        for ev in _audit.iter_events(day=day):
            if type and ev.event_type != type:
                continue
            all_events.append(ev.model_dump())
        events = all_events[-limit:]
        return {"count": len(events), "total_in_log": len(all_events), "events": events}
    else:
        events = []
        for ev in _audit.iter_events(day=day):
            if type and ev.event_type != type:
                continue
            events.append(ev.model_dump())
            if len(events) >= limit:
                break
        return {"count": len(events), "events": events}


@app.get("/api/state")
async def state():
    return {
        "latest_ticks": {sym: t.payload for sym, t in _scheduler.latest_tick.items()},
        "open_positions": [
            {
                "symbol": p.symbol, "qty": p.qty, "entry_price": p.entry_price,
                "entry_ts": p.entry_ts, "stop": p.stop_price, "target": p.target_price,
            } for p in _executor.positions.values() if p.status == "OPEN"
        ],
        "scanner_b_sma": {k: round(v, 4) for k, v in _scanner_b.sma_cache.items()},
        "cursors": _retriever.cursor_state(),
    }


@app.get("/api/catalog")
async def catalog():
    return {"instruments": _retriever.catalog()}


@app.get("/api/replay/{day}")
async def replay(day: str):
    events = [ev.model_dump() for ev in _audit.iter_events(day=day)]
    return {"day": day, "count": len(events), "events": events}


# ---- static frontend ----
@app.get("/")
async def root():
    idx = FRONTEND_DIR / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return JSONResponse({"error": "frontend not built"}, status_code=404)


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
