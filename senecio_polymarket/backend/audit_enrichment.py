"""
SENECIO ORACLE — Audit Enrichment (ACT FINAL_AUDIT — A2)
=========================================================

STRICT_ADDITIVE observability layer.

Purpose
-------
Takes a prediction dict (as produced by predict_only.run_prediction)
and ENRICHES its `_audit` JSONB with a new sub-dict called `enriched`
containing 30+ derived fields required by downstream forensic /
watchdog / statistical pipelines.

Crucially, this module:
  - Does NOT modify the prediction itself (symbol, prediction, confidence,
    ev, price_now, outcome, action, side, size — all UNTOUCHED).
  - Does NOT modify any existing _audit sub-dict (action_vector / pipeline /
    execution_state / candle_ts / outcomes_dual).
  - Only ADDS a new `enriched` sub-dict to _audit (and a top-level
    `_enrichment_version` for forward-compat).

This is purely metadata for analysis — the trading path never reads it.

Required new fields (per ACT FINAL_AUDIT A2 spec):
  Time:        hour_utc, weekday, month, session_asia, session_europe,
               session_us, market_phase
  Microstructure: funding, open_interest, spread_bps, book_imbalance,
               vpin, ofi, liquidity_score
  Regime:      regime_4h, regime_hint, hmm_state
  EV:          ev_adjusted, expected_value
  Signal:      signal_strength, signal_confidence,
               confidence_before_meta, confidence_after_meta, meta_label
  Risk:        capacity_score, stress_score
  Execution:   execution_model, latency_ms, slippage_bps
  Provenance:  prediction_hash, feature_hash, model_version, commit_hash

All fields default to None when source data is missing — never raises.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

log = logging.getLogger("senecio.audit_enrichment")

ENRICHMENT_VERSION = "A2-2026-06-20-v1"
MODEL_VERSION = "senecio-oracle-v1-act-xxix"

# Cache commit hash at import time (cheap, doesn't change during process life)
_COMMIT_HASH_CACHE: Optional[str] = None


def _git_commit_hash() -> str:
    global _COMMIT_HASH_CACHE
    if _COMMIT_HASH_CACHE is not None:
        return _COMMIT_HASH_CACHE
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=3,
        )
        _COMMIT_HASH_CACHE = (r.stdout.strip() or "unknown")[:12]
    except Exception:
        _COMMIT_HASH_CACHE = "unknown"
    return _COMMIT_HASH_CACHE


# ─────────────────────────────────────────────────────────────────────
# Helpers — pure functions, never raise
# ─────────────────────────────────────────────────────────────────────

def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def _safe_str(x: Any, default: str = "") -> str:
    try:
        if x is None:
            return default
        return str(x)
    except Exception:
        return default


def _stable_hash(obj: Any) -> str:
    """Stable SHA-256 of a JSON-serializable object (sorted keys)."""
    try:
        payload = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except Exception:
        return "hash_error"


def _classify_session(hour_utc: int) -> dict:
    """Overlap-aware FX-style session classifier (UTC hours)."""
    return {
        "session_asia": 0 <= hour_utc < 9,         # 00:00-09:00 UTC Tokyo
        "session_europe": 7 <= hour_utc < 17,      # 07:00-17:00 UTC London
        "session_us": 13 <= hour_utc < 22,         # 13:00-22:00 UTC New York
    }


def _classify_market_phase(hour_utc: int, weekday: int) -> str:
    """Coarse market phase label."""
    if weekday >= 5:  # Sat/Sun
        return "WEEKEND"
    if 0 <= hour_utc < 7:
        return "ASIA_PRIMARY"
    if 7 <= hour_utc < 13:
        return "EUROPE_OPEN"
    if 13 <= hour_utc < 17:
        return "US_EUROPE_OVERLAP"
    if 17 <= hour_utc < 22:
        return "US_PRIMARY"
    return "LATE_US_OFF_ASIA"


# ─────────────────────────────────────────────────────────────────────
# Source extractors — read existing _audit fields safely
# ─────────────────────────────────────────────────────────────────────

def _extract_step1(pipeline: dict) -> dict:
    s1 = pipeline.get("step1_market", {}) if isinstance(pipeline, dict) else {}
    if not isinstance(s1, dict):
        return {}
    return s1


def _extract_step2(pipeline: dict) -> dict:
    s2 = pipeline.get("step2_features", {}) if isinstance(pipeline, dict) else {}
    if not isinstance(s2, dict):
        return {}
    return s2


def _extract_step3(pipeline: dict) -> dict:
    s3 = pipeline.get("step3_risk", {}) if isinstance(pipeline, dict) else {}
    if not isinstance(s3, dict):
        return {}
    return s3


def _extract_step4(pipeline: dict) -> dict:
    s4 = pipeline.get("step4_ev", {}) if isinstance(pipeline, dict) else {}
    if not isinstance(s4, dict):
        return {}
    return s4


def _extract_step5(pipeline: dict) -> dict:
    s5 = pipeline.get("step5_feasibility", {}) if isinstance(pipeline, dict) else {}
    if not isinstance(s5, dict):
        return {}
    return s5


# ─────────────────────────────────────────────────────────────────────
# Main enrichment entry point
# ─────────────────────────────────────────────────────────────────────

def enrich_prediction(prediction: dict, *, runtime_meta: Optional[dict] = None) -> dict:
    """Return the prediction with an enriched `audit.enriched` sub-dict.

    Args:
        prediction: Oracle output dict (must have `_audit`).
        runtime_meta: Optional dict of runtime-only fields supplied by
            the caller (e.g. latency_ms, slippage_bps, execution_model).
            These cannot be reconstructed from the prediction alone.

    Returns:
        The SAME prediction object (mutated in place + returned for
        convenience). Existing fields are NEVER modified.

    This function NEVER raises. On any error it returns the prediction
    unchanged (with `_enrichment_error` set if something went wrong).
    """
    try:
        audit = prediction.get("_audit")
        if not isinstance(audit, dict):
            # Defensive: should never happen, but never break the caller.
            prediction["_audit"] = audit = {}

        # If already enriched (idempotent re-call), refresh in place.
        # We rebuild from scratch each time so callers can pass updated
        # runtime_meta on a re-enrichment pass.
        pipeline = audit.get("pipeline") if isinstance(audit.get("pipeline"), dict) else {}
        action_vector = audit.get("action_vector") if isinstance(audit.get("action_vector"), dict) else {}
        execution_state = audit.get("execution_state") if isinstance(audit.get("execution_state"), dict) else {}

        s1 = _extract_step1(pipeline)
        s2 = _extract_step2(pipeline)
        s3 = _extract_step3(pipeline)
        s4 = _extract_step4(pipeline)
        s5 = _extract_step5(pipeline)

        # ── Time ──
        ts_str = prediction.get("timestamp") or s1.get("candle_ts_iso")
        ts_dt = None
        try:
            if isinstance(ts_str, str):
                ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        except Exception:
            ts_dt = None

        if ts_dt is None:
            ts_dt = datetime.now(timezone.utc)

        hour_utc = ts_dt.hour
        weekday = ts_dt.weekday()  # Mon=0..Sun=6
        month = ts_dt.month
        sessions = _classify_session(hour_utc)
        market_phase = _classify_market_phase(hour_utc, weekday)

        # ── Microstructure ──
        # Pull from step1_market and step2_features; fields may live under various names.
        ticker = s1.get("ticker", {}) if isinstance(s1.get("ticker"), dict) else {}
        pressures = s2.get("pressures", {}) if isinstance(s2.get("pressures"), dict) else {}
        microstruct = s2.get("microstructure", {}) if isinstance(s2.get("microstructure"), dict) else {}

        bid = _safe_float(ticker.get("bid"))
        ask = _safe_float(ticker.get("ask"))
        spread_bps = None
        if bid is not None and ask is not None and bid > 0:
            spread_bps = round(((ask - bid) / bid) * 10000.0, 4)

        book_imbalance = _safe_float(
            microstruct.get("book_imbalance")
            or pressures.get("bidask_imbalance")
        )
        vpin = _safe_float(microstruct.get("vpin") or s2.get("vpin"))
        ofi = _safe_float(microstruct.get("ofi") or s2.get("ofi"))
        funding = _safe_float(s1.get("funding_rate") or s2.get("funding"))
        open_interest = _safe_float(s1.get("open_interest") or s2.get("open_interest"))
        liquidity_score = _safe_float(s1.get("liquidity_quality") or s3.get("liquidity_quality"))

        # ── Regime ──
        regime_4h = _safe_str(s2.get("regime_4h") or s1.get("regime_4h"))
        regime_hint = _safe_str(s2.get("regime_hint") or s1.get("regime_hint"))
        hmm_state = _safe_str(s2.get("hmm_state") or s1.get("hmm_state"))

        # ── EV / signal ──
        ev_adjusted = _safe_float(s4.get("adjusted_ev"))
        expected_value = _safe_float(s4.get("expected_value") or prediction.get("ev"))
        signal_strength = _safe_float(s2.get("signal_strength") or s2.get("edge_score"))
        signal_confidence = _safe_float(s2.get("signal_confidence") or s2.get("confidence"))
        confidence_before_meta = _safe_float(s2.get("confidence_before_meta") or s2.get("raw_confidence"))
        confidence_after_meta = _safe_float(s2.get("confidence_after_meta") or prediction.get("confidence"))
        meta_label = _safe_str(s2.get("meta_label") or s2.get("meta_pred"))

        # ── Risk / capacity / stress ──
        capacity_score = _safe_float(s3.get("capacity_score") or s5.get("capacity_score"))
        stress_score = _safe_float(s3.get("stress_score") or s5.get("stress_score"))

        # ── Execution ──
        runtime_meta = runtime_meta or {}
        execution_model = _safe_str(
            runtime_meta.get("execution_model")
            or execution_state.get("execution_model")
            or "PAPER"
        )
        latency_ms = _safe_float(
            runtime_meta.get("latency_ms")
            or execution_state.get("latency_ms")
        )
        slippage_bps = _safe_float(
            runtime_meta.get("slippage_bps")
            or execution_state.get("slippage_bps")
        )

        # ── Provenance ──
        # Hashes are computed over stable JSON encodings of the relevant
        # sub-dicts. This lets us detect drift / replay later.
        feature_hash = _stable_hash({
            "step1_market": s1,
            "step2_features": s2,
        })
        prediction_hash = _stable_hash({
            "symbol": prediction.get("symbol"),
            "prediction": prediction.get("prediction"),
            "confidence": prediction.get("confidence"),
            "ev": prediction.get("ev"),
            "price_now": prediction.get("price_now"),
            "timestamp": prediction.get("timestamp"),
            "feature_hash": feature_hash,
        })
        model_version = MODEL_VERSION
        commit_hash = _git_commit_hash()

        # ── Build enriched sub-dict (all keys must exist, even if None) ──
        enriched = {
            "_enrichment_version": ENRICHMENT_VERSION,
            "_enriched_at_utc": datetime.now(timezone.utc).isoformat(),

            # Time
            "hour_utc": hour_utc,
            "weekday": weekday,
            "weekday_name": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday]
                           if 0 <= weekday <= 6 else None,
            "month": month,
            "session_asia": sessions["session_asia"],
            "session_europe": sessions["session_europe"],
            "session_us": sessions["session_us"],
            "market_phase": market_phase,

            # Microstructure
            "funding": funding,
            "open_interest": open_interest,
            "spread_bps": spread_bps,
            "book_imbalance": book_imbalance,
            "vpin": vpin,
            "ofi": ofi,
            "liquidity_score": liquidity_score,

            # Regime
            "regime_4h": regime_4h or None,
            "regime_hint": regime_hint or None,
            "hmm_state": hmm_state or None,

            # EV
            "ev_adjusted": ev_adjusted,
            "expected_value": expected_value,

            # Signal
            "signal_strength": signal_strength,
            "signal_confidence": signal_confidence,
            "confidence_before_meta": confidence_before_meta,
            "confidence_after_meta": confidence_after_meta,
            "meta_label": meta_label or None,

            # Risk / capacity / stress
            "capacity_score": capacity_score,
            "stress_score": stress_score,

            # Execution
            "execution_model": execution_model,
            "latency_ms": latency_ms,
            "slippage_bps": slippage_bps,

            # Provenance
            "prediction_hash": prediction_hash,
            "feature_hash": feature_hash,
            "model_version": model_version,
            "commit_hash": commit_hash,
        }

        # Idempotent: always overwrite the `enriched` sub-dict.
        audit["enriched"] = enriched

        # Top-level marker (also additive — does not collide with existing keys).
        prediction["_enrichment_version"] = ENRICHMENT_VERSION

        return prediction

    except Exception as e:
        # NEVER break the caller. Record the error and return.
        try:
            prediction["_enrichment_error"] = f"{type(e).__name__}: {e}"
        except Exception:
            pass
        log.exception("audit_enrichment failed (prediction returned unchanged): %s", e)
        return prediction


# ─────────────────────────────────────────────────────────────────────
# Verification helpers
# ─────────────────────────────────────────────────────────────────────

REQUIRED_FIELDS = [
    # Time
    "hour_utc", "weekday", "month",
    "session_asia", "session_europe", "session_us", "market_phase",
    # Microstructure
    "funding", "open_interest", "spread_bps", "book_imbalance",
    "vpin", "ofi", "liquidity_score",
    # Regime
    "regime_4h", "regime_hint", "hmm_state",
    # EV
    "ev_adjusted", "expected_value",
    # Signal
    "signal_strength", "signal_confidence",
    "confidence_before_meta", "confidence_after_meta", "meta_label",
    # Risk
    "capacity_score", "stress_score",
    # Execution
    "execution_model", "latency_ms", "slippage_bps",
    # Provenance
    "prediction_hash", "feature_hash", "model_version", "commit_hash",
]


def verify_enrichment(prediction: dict) -> dict:
    """Return a verification report: which required fields are present/missing."""
    enriched = (prediction.get("_audit") or {}).get("enriched") or {}
    present = [f for f in REQUIRED_FIELDS if f in enriched]
    missing = [f for f in REQUIRED_FIELDS if f not in enriched]
    non_null = [f for f in REQUIRED_FIELDS if enriched.get(f) is not None]
    return {
        "ok": len(missing) == 0,
        "present_count": len(present),
        "missing_count": len(missing),
        "missing": missing,
        "non_null_count": len(non_null),
        "enrichment_version": enriched.get("_enrichment_version"),
    }
