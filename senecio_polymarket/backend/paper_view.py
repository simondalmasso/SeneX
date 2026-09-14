"""Observational PAPER execution view for the public read-only runtime.

Aggregates the existing ACT-XXV portfolio pipeline state (PortfolioCoordinator
-> RiskKernel -> ExecutionEngine -> TradeJournal -> ShadowLive) into one
truthful, bounded, read-only payload for /api/paper/* and the dashboard.

Design rules (ORDER-070-R1 + B8.1):
  * purely observational: no network/database I/O, no mutation;
  * reuses the single portfolio pipeline owned by oracle_runner — this module
    NEVER creates a second execution subsystem;
  * absent components surface as UNKNOWN/INDET, never zero, never OK;
  * the bankroll is labeled HYPOTHETICAL everywhere;
  * EDGE status is UNPROVEN unless a real, resolved, adequately-sized
    evidence window says otherwise (never the case inside a smoke sample).
"""
from __future__ import annotations

import json
from typing import Any

from .paper_lock import safety_projection


def _unknown(reason: str = "UNAVAILABLE") -> dict[str, Any]:
    return {"status": "UNKNOWN", "reason": reason, "value": None}


def _get_coordinator() -> Any | None:
    """Best-effort read of the lazily-initialized portfolio coordinator."""
    try:
        from . import oracle_runner
    except Exception:
        return None
    coordinator = getattr(oracle_runner, "_portfolio_coordinator", None)
    return coordinator


def _engine_view(engine: Any) -> dict[str, Any]:
    """Bounded stats view of the ACT-XXV ExecutionEngine (paper)."""
    try:
        stats = engine.stats()
    except Exception:
        stats = {}
    cfg = getattr(engine, "cfg", {}) or {}
    return {
        "trade_mode": stats.get("trade_mode") or cfg.get("trade_mode") or "UNKNOWN",
        "allow_live": bool(stats.get("allow_live", cfg.get("allow_live", False))),
        "cash": stats.get("cash"),
        "starting_cash": stats.get("starting_cash"),
        "open_positions": stats.get("open_positions"),
        "closed_positions": stats.get("closed_positions"),
        "total_orders": stats.get("total_orders"),
        "fill_simulator": stats.get("fill_simulator"),
        "taker_fee_bps": cfg.get("taker_fee_bps"),
        "maker_fee_bps": cfg.get("maker_fee_bps"),
        "hypothetical": True,
    }


def paper_state(*, last_prices: dict[str, float] | None = None) -> dict[str, Any]:
    """Assemble the public PAPER execution + model-quality view."""
    last_prices = last_prices or {}
    coord = _get_coordinator()
    safety = safety_projection()

    if coord is None:
        return {
            "status": "NO_PORTFOLIO_PIPELINE",
            "safety": safety,
            "execution": _unknown("PORTFOLIO_COORDINATOR_NOT_INITIALIZED"),
            "state": _unknown("PORTFOLIO_COORDINATOR_NOT_INITIALIZED"),
            "recent_trades": {"status": "UNKNOWN", "rows": []},
            "analytics": _unknown("PORTFOLIO_COORDINATOR_NOT_INITIALIZED"),
            "shadow_live": _unknown("PORTFOLIO_COORDINATOR_NOT_INITIALIZED"),
            "model_quality": _model_quality_view(),
            "edge": _edge_view(),
            "hypothetical": True,
        }

    # Coordinator state (positions/exposure/kill-switch/regime view).
    try:
        state = coord.get_state()
    except Exception:
        state = _unknown("COORDINATOR_STATE_ERROR")

    engine = getattr(coord, "execution_engine", None)
    engine_stats = _engine_view(engine) if engine is not None else _unknown()

    positions: list[dict[str, Any]] = []
    try:
        positions = engine.get_open_positions() if engine is not None else []
    except Exception:
        positions = []

    exits: list[dict[str, Any]] = []
    try:
        exits = engine.get_recent_exits(limit=20) if engine is not None else []
    except Exception:
        exits = []

    try:
        recent_trades = coord.get_recent_trades(limit=20)
    except Exception:
        recent_trades = []

    try:
        analytics = {"status": "OK", "value": coord.get_analytics()}
    except Exception:
        analytics = _unknown("ANALYTICS_ERROR")

    try:
        shadow = {"status": "OK", "value": coord.get_shadow_report()}
    except Exception:
        shadow = _unknown("SHADOW_LIVE_ERROR")

    equity: float | None = None
    equity_status: dict[str, Any] = {
        "status": "UNKNOWN",
        "reason": "EXECUTION_ENGINE_UNAVAILABLE",
        "missing_price_symbols": [],
    }
    try:
        if engine is not None and hasattr(engine, "equity_state"):
            state_view = engine.equity_state(last_prices)
            equity = state_view.get("equity")
            equity_status = {
                "status": state_view.get("status") or "UNKNOWN",
                "reason": None if state_view.get("status") == "OK" else "MISSING_MARKET_PRICE",
                "missing_price_symbols": list(state_view.get("missing_price_symbols") or []),
            }
        elif engine is not None and hasattr(engine, "equity"):
            equity = engine.equity(last_prices)
            equity_status = {"status": "OK", "reason": None, "missing_price_symbols": []}
    except Exception:
        equity = None
        equity_status = {"status": "UNKNOWN", "reason": "EQUITY_ERROR", "missing_price_symbols": []}

    return {
        "status": "OK",
        "safety": safety,
        "execution": engine_stats,
        "state": state,
        "equity": equity,
        "equity_status": equity_status,
        "positions": {"status": "OK" if positions else "EMPTY", "rows": positions},
        "recent_exits": {"status": "OK" if exits else "EMPTY", "rows": exits},
        "recent_trades": {"status": "OK" if recent_trades else "EMPTY", "rows": recent_trades},
        "analytics": analytics,
        "shadow_live": shadow,
        "model_quality": _model_quality_view(),
        "edge": _edge_view(),
        "hypothetical": True,
    }


def _last_raw_prediction(predictions_path: str | None = None) -> dict[str, Any] | None:
    """Last line of predictions.jsonl (source of truth), read-only, bounded.

    Reads only the final line by seeking from the end — O(1) regardless of
    file size. Returns None when unavailable (surfaced as UNKNOWN).
    """
    from . import oracle_runner
    path = predictions_path or str(oracle_runner.PREDICTIONS_PATH)
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            window = min(size, 65_536)
            handle.seek(max(0, size - window))
            tail = handle.read().decode("utf-8", errors="replace")
        lines = [line for line in tail.splitlines() if line.strip()]
        if not lines:
            return None
        return json.loads(lines[-1])
    except Exception:
        return None


def _tail_prediction_rows(max_rows: int = 512) -> list[dict[str, Any]]:
    """Last ``max_rows`` prediction rows (bounded read from the end)."""
    from . import oracle_runner
    path = oracle_runner.PREDICTIONS_PATH
    rows: list[dict[str, Any]] = []
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            window = min(size, 1 << 20)  # 1 MiB tail window
            handle.seek(max(0, size - window))
            tail = handle.read().decode("utf-8", errors="replace")
        lines = [line for line in tail.splitlines() if line.strip()]
        for line in lines[-int(max_rows):]:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return rows


def _model_quality_view() -> dict[str, Any]:
    """Read-only model-quality view with BOUNDED work per call.

    observation_count comes from the oracle runner's incrementally maintained
    counter (authoritative). directional/abstentions/resolved are counted over
    a bounded tail window of the journal and labeled as such. Brier / log
    score / calibration are computed ONLY over resolved rows and are
    NOT_COMPUTED until then (never zero).
    """
    from . import oracle_runner

    state = oracle_runner.get_state()
    observation_count = state.get("predictions_count")
    tail_rows = _tail_prediction_rows(max_rows=512)
    directional = sum(
        1 for row in tail_rows
        if str(row.get("prediction") or "").upper() in ("LONG", "SHORT")
    )
    abstentions = sum(
        1 for row in tail_rows
        if str(row.get("prediction") or "").upper() not in ("LONG", "SHORT")
    )
    resolved = sum(
        1 for row in tail_rows
        if row.get("outcome") not in (None, "", "PENDING")
    )
    # Fresh-import robustness: the runner's counter is authoritative once
    # started; before that, the bounded tail window is the honest floor.
    if observation_count is None or int(observation_count or 0) < len(tail_rows):
        observation_count = len(tail_rows)
    return {
        "status": "OK" if observation_count is not None else "UNKNOWN",
        "observation_count": observation_count,
        "directional": directional,
        "abstentions": abstentions,
        "resolved": resolved,
        "counting_window": "BOUNDED_TAIL_512_ROWS",
        "brier_score": "NOT_COMPUTED_UNTIL_OUTCOMES_RESOLVE" if resolved == 0 else None,
        "log_score": "NOT_COMPUTED_UNTIL_OUTCOMES_RESOLVE" if resolved == 0 else None,
        "calibration": "NOT_COMPUTED",
        "sample_label": "SMOKE_SAMPLE" if (observation_count or 0) < 500 else "WINDOW",
        "note": "accuracy alone is insufficient; scoring requires resolved outcomes",
    }


def _edge_view() -> dict[str, Any]:
    """Incremental-edge diagnostic vs the observable Polymarket prior.

    Reuses the oracle pipeline's own audit fields:
      - p_senex = pipeline.step2_features.up_prob (model up-probability,
        semantics UNVALIDATED as a calibrated probability);
      - p_market = external_markets_v1.polymarket.up_probability captured at
        decision time, with the live adapter snapshot as a fallback view.

    SENEX earns an EDGE claim only with incremental predictive value beyond
    the market prior after costs and uncertainty over a real evidence window.
    Until such a window exists this is UNPROVEN by construction.
    """
    p_market_decision_time = None
    p_senex = None
    prediction_label = None
    last = _last_raw_prediction()
    if isinstance(last, dict):
        prediction_label = str(last.get("prediction") or "UNKNOWN").upper()
        audit = last.get("_audit") if isinstance(last.get("_audit"), dict) else {}
        pipeline = audit.get("pipeline") if isinstance(audit.get("pipeline"), dict) else {}
        step2 = pipeline.get("step2_features") if isinstance(pipeline, dict) else {}
        up_prob = step2.get("up_prob") if isinstance(step2, dict) else None
        if isinstance(up_prob, (int, float)) and 0.0 <= float(up_prob) <= 1.0:
            p_senex = float(up_prob)
        external = audit.get("external_markets_v1") if isinstance(audit.get("external_markets_v1"), dict) else {}
        poly = external.get("polymarket") if isinstance(external.get("polymarket"), dict) else {}
        up_probability = poly.get("up_probability")
        if isinstance(up_probability, (int, float)) and 0.0 <= float(up_probability) <= 1.0:
            p_market_decision_time = float(up_probability)

    # Live adapter fallback (labeled as observation-time, not decision-time)
    p_market_live = None
    try:
        from . import polymarket_market_adapter
        snap = polymarket_market_adapter.get_polymarket_snapshot()
        up = snap.get("up") if isinstance(snap, dict) else None
        if isinstance(up, dict):
            price = up.get("price")
            if isinstance(price, (int, float)) and 0.0 < float(price) < 1.0:
                p_market_live = float(price)
    except Exception:
        p_market_live = None

    p_market = p_market_decision_time if p_market_decision_time is not None else p_market_live
    incremental = None
    if p_market is not None and p_senex is not None:
        incremental = round(p_senex - p_market, 4)

    return {
        "status": "UNPROVEN",
        "reason": "NO_RESOLVED_EVIDENCE_WINDOW",
        "p_market": p_market,
        "p_market_source": "DECISION_TIME_AUDIT" if p_market_decision_time is not None else (
            "OBSERVATION_TIME_ADAPTER" if p_market_live is not None else "UNAVAILABLE"
        ),
        "p_senex": p_senex,
        "p_senex_semantics": "UNVALIDATED_MODEL_UP_PROB",
        "prediction": prediction_label,
        "incremental_edge": incremental,
        "representation": "up_prob - market_up_probability (probability space)",
        "note": "EDGE=UNPROVEN until a resolved, adequately-sized, fresh window proves incremental value net of costs",
    }
