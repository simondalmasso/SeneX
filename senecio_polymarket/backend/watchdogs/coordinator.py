"""
SENECIO ORACLE — Watchdog Coordinator (ACT FINAL_AUDIT — A4)
==============================================================

STRICT_ADDITIVE anomaly detection layer.

Runs 8 watchdogs that scan the latest forensic report + Supabase state
and emit alerts when the trading edge deteriorates:

  WATCHDOG                TRIGGERS WHEN
  ----------------------- ------------------------------------------------
  LONG_WR_DROP            LONG rolling WR (w25) drops >10pp below cumulative
  SHORT_WR_DROP           SHORT rolling WR (w25) drops >10pp below cumulative
  CALIBRATION_DRIFT       Brier score > 0.25 OR ECE > 0.10
  FEATURE_DRIFT           PSI > 0.20 on any tracked feature
  EXECUTION_DRIFT         (placeholder — awaiting real execution data)
  CAPACITY_DRIFT          mean capacity_score drops > 20% in last 50 vs first 50
  MICROSTRUCTURE_DRIFT    mean spread_bps changes > 50% OR vpin > 0.40
  REGIME_SHIFT            dominant regime_4h changes between two windows

All alerts are appended to:
  senecio_polymarket/data/watchdogs/alerts.jsonl   (append-only)

And surfaced via the latest_state() function for the API layer.

NEVER raises. NEVER modifies trading logic.
"""
from __future__ import annotations

import json
import logging
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("senecio.watchdogs")

ALERTS_FILE = Path(__file__).resolve().parents[2] / "data" / "watchdogs" / "alerts.jsonl"
ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)


def _append_alert(alert: dict) -> None:
    """Append an alert to the JSONL log (never raises)."""
    try:
        alert["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
        with open(ALERTS_FILE, "a") as f:
            f.write(json.dumps(alert, default=str) + "\n")
    except Exception as e:
        log.warning("failed to append watchdog alert: %s", e)


# ─────────────────────────────────────────────────────────────────────
# Individual watchdogs
# ─────────────────────────────────────────────────────────────────────

def _safe_float_list(values: list) -> list:
    out = []
    for v in values:
        try:
            if v is not None:
                out.append(float(v))
        except Exception:
            pass
    return out


def wd_long_wr_drop(forensics: dict, history: list) -> Optional[dict]:
    """LONG_WR_DROP: rolling w25 WR > 10pp below cumulative WR."""
    db = forensics.get("analyses", {}).get("direction_breakdown", {})
    long_cum_wr = db.get("LONG", {}).get("win_rate")
    long_n = db.get("LONG", {}).get("n", 0)
    rolling = forensics.get("analyses", {}).get("rolling_wr", {}).get("LONG", {})
    w25 = rolling.get("w25")
    if not w25 or w25.get("current") is None:
        return None
    if long_cum_wr is None or long_n < 25:
        return None
    delta = w25["current"] - long_cum_wr
    if delta < -0.10:  # more than 10pp drop
        return {
            "watchdog": "LONG_WR_DROP",
            "severity": "HIGH" if delta < -0.20 else "MEDIUM",
            "long_cumulative_wr": long_cum_wr,
            "long_rolling_w25_wr": w25["current"],
            "delta_pp": round(delta * 100, 2),
            "n_long": long_n,
            "message": f"LONG WR dropped {abs(delta)*100:.1f}pp in last 25 trades",
        }
    return None


def wd_short_wr_drop(forensics: dict, history: list) -> Optional[dict]:
    """SHORT_WR_DROP: rolling w25 WR > 10pp below cumulative WR."""
    db = forensics.get("analyses", {}).get("direction_breakdown", {})
    short_cum_wr = db.get("SHORT", {}).get("win_rate")
    short_n = db.get("SHORT", {}).get("n", 0)
    rolling = forensics.get("analyses", {}).get("rolling_wr", {}).get("SHORT", {})
    w25 = rolling.get("w25")
    if not w25 or w25.get("current") is None:
        return None
    if short_cum_wr is None or short_n < 25:
        return None
    delta = w25["current"] - short_cum_wr
    if delta < -0.10:
        return {
            "watchdog": "SHORT_WR_DROP",
            "severity": "HIGH" if delta < -0.20 else "MEDIUM",
            "short_cumulative_wr": short_cum_wr,
            "short_rolling_w25_wr": w25["current"],
            "delta_pp": round(delta * 100, 2),
            "n_short": short_n,
            "message": f"SHORT WR dropped {abs(delta)*100:.1f}pp in last 25 trades",
        }
    return None


def wd_calibration_drift(forensics: dict, history: list) -> Optional[dict]:
    """CALIBRATION_DRIFT: Brier > 0.25 OR ECE > 0.10."""
    cal = forensics.get("analyses", {}).get("calibration", {})
    brier = cal.get("brier")
    ece = cal.get("ece")
    if brier is None and ece is None:
        return None
    reasons = []
    if brier is not None and brier > 0.25:
        reasons.append(f"brier={brier} > 0.25")
    if ece is not None and ece > 0.10:
        reasons.append(f"ece={ece} > 0.10")
    if not reasons:
        return None
    return {
        "watchdog": "CALIBRATION_DRIFT",
        "severity": "HIGH" if (brier or 0) > 0.30 else "MEDIUM",
        "brier": brier,
        "ece": ece,
        "n": cal.get("n"),
        "message": "Calibration degraded: " + ", ".join(reasons),
    }


def wd_feature_drift(forensics: dict, history: list) -> Optional[dict]:
    """FEATURE_DRIFT: PSI > 0.20 on any tracked feature."""
    drift = forensics.get("analyses", {}).get("drift", {})
    features = drift.get("features", {})
    worst = []
    for feat, info in features.items():
        psi = info.get("psi")
        if psi is not None and psi > 0.20:
            worst.append((feat, psi, info.get("ks_p")))
    if not worst:
        return None
    worst.sort(key=lambda x: -x[1])
    return {
        "watchdog": "FEATURE_DRIFT",
        "severity": "HIGH" if worst[0][1] > 0.40 else "MEDIUM",
        "worst_features": [
            {"feature": f, "psi": p, "ks_p": kp}
            for f, p, kp in worst[:5]
        ],
        "message": f"PSI drift on {len(worst)} features; worst={worst[0][0]} psi={worst[0][1]:.3f}",
    }


def wd_execution_drift(forensics: dict, history: list) -> Optional[dict]:
    """EXECUTION_DRIFT: placeholder — needs real execution telemetry.

    Will activate once the portfolio execution_engine records latency_ms
    and slippage_bps into _audit.enriched. For now, returns None.
    """
    # Intentionally inert. Implemented as a stub so the watchdog registry
    # has a slot for it; will be wired when execution telemetry exists.
    return None


def wd_capacity_drift(forensics: dict, history: list, rows: list = None) -> Optional[dict]:
    """CAPACITY_DRIFT: mean capacity_score drops > 20% in recent vs baseline."""
    if not rows or len(rows) < 100:
        return None
    sorted_rows = sorted(rows, key=lambda r: r.get("ts") or "")
    half = len(sorted_rows) // 2
    baseline = sorted_rows[:half]
    recent = sorted_rows[half:]

    def mean_capacity(group):
        vals = []
        for r in group:
            audit = r.get("audit") or {}
            if isinstance(audit, str):
                try:
                    audit = json.loads(audit)
                except Exception:
                    audit = {}
            if not isinstance(audit, dict):
                audit = {}
            enriched = audit.get("enriched") or {}
            v = enriched.get("capacity_score")
            try:
                if v is not None:
                    vals.append(float(v))
            except Exception:
                pass
        return sum(vals) / len(vals) if vals else None

    b = mean_capacity(baseline)
    r = mean_capacity(recent)
    if b is None or r is None or b == 0:
        return None
    drop = (b - r) / b  # positive drop = bad
    if drop > 0.20:
        return {
            "watchdog": "CAPACITY_DRIFT",
            "severity": "HIGH" if drop > 0.40 else "MEDIUM",
            "baseline_capacity": round(b, 4),
            "recent_capacity": round(r, 4),
            "drop_pct": round(drop * 100, 2),
            "message": f"Capacity score dropped {drop*100:.1f}% (baseline={b:.3f} recent={r:.3f})",
        }
    return None


def wd_microstructure_drift(forensics: dict, history: list, rows: list = None) -> Optional[dict]:
    """MICROSTRUCTURE_DRIFT: spread_bps doubles OR vpin > 0.40 in recent window."""
    if not rows or len(rows) < 100:
        return None
    sorted_rows = sorted(rows, key=lambda r: r.get("ts") or "")
    half = len(sorted_rows) // 2
    baseline = sorted_rows[:half]
    recent = sorted_rows[half:]

    def micro_stats(group):
        spreads = []
        vpins = []
        for r in group:
            audit = r.get("audit") or {}
            if isinstance(audit, str):
                try:
                    audit = json.loads(audit)
                except Exception:
                    audit = {}
            if not isinstance(audit, dict):
                audit = {}
            enriched = audit.get("enriched") or {}
            s = enriched.get("spread_bps")
            v = enriched.get("vpin")
            try:
                if s is not None:
                    spreads.append(float(s))
                if v is not None:
                    vpins.append(float(v))
            except Exception:
                pass
        return {
            "spread_mean": sum(spreads) / len(spreads) if spreads else None,
            "vpin_mean": sum(vpins) / len(vpins) if vpins else None,
            "vpin_max": max(vpins) if vpins else None,
        }

    b = micro_stats(baseline)
    r = micro_stats(recent)
    reasons = []
    if (b.get("spread_mean") and r.get("spread_mean")
            and b["spread_mean"] > 0
            and r["spread_mean"] / b["spread_mean"] > 2.0):
        reasons.append(
            f"spread_bps doubled: {b['spread_mean']:.2f} → {r['spread_mean']:.2f}"
        )
    if r.get("vpin_mean") is not None and r["vpin_mean"] > 0.40:
        reasons.append(f"vpin mean {r['vpin_mean']:.3f} > 0.40")
    if not reasons:
        return None
    return {
        "watchdog": "MICROSTRUCTURE_DRIFT",
        "severity": "HIGH" if r.get("vpin_mean", 0) > 0.50 else "MEDIUM",
        "baseline": b,
        "recent": r,
        "message": "Microstructure degraded: " + "; ".join(reasons),
    }


def wd_regime_shift(forensics: dict, history: list, rows: list = None) -> Optional[dict]:
    """REGIME_SHIFT: dominant regime_4h changes between baseline and recent."""
    if not rows or len(rows) < 100:
        return None
    sorted_rows = sorted(rows, key=lambda r: r.get("ts") or "")
    half = len(sorted_rows) // 2
    baseline = sorted_rows[:half]
    recent = sorted_rows[half:]

    def dominant_regime(group):
        regimes = []
        for r in group:
            audit = r.get("audit") or {}
            if isinstance(audit, str):
                try:
                    audit = json.loads(audit)
                except Exception:
                    audit = {}
            if not isinstance(audit, dict):
                audit = {}
            enriched = audit.get("enriched") or {}
            v = enriched.get("regime_4h")
            if v:
                regimes.append(str(v))
        if not regimes:
            return None
        return Counter(regimes).most_common(1)[0]
    b_dom = dominant_regime(baseline)
    r_dom = dominant_regime(recent)
    if b_dom is None or r_dom is None:
        return None
    if b_dom[0] != r_dom[0]:
        return {
            "watchdog": "REGIME_SHIFT",
            "severity": "MEDIUM",
            "baseline_dominant_regime": b_dom[0],
            "baseline_dominant_count": b_dom[1],
            "recent_dominant_regime": r_dom[0],
            "recent_dominant_count": r_dom[1],
            "message": f"Regime shifted from {b_dom[0]} → {r_dom[0]}",
        }
    return None


# ─────────────────────────────────────────────────────────────────────
# Coordinator
# ─────────────────────────────────────────────────────────────────────

WATCHDOGS = [
    ("LONG_WR_DROP", wd_long_wr_drop),
    ("SHORT_WR_DROP", wd_short_wr_drop),
    ("CALIBRATION_DRIFT", wd_calibration_drift),
    ("FEATURE_DRIFT", wd_feature_drift),
    ("EXECUTION_DRIFT", wd_execution_drift),
    ("CAPACITY_DRIFT", wd_capacity_drift),
    ("MICROSTRUCTURE_DRIFT", wd_microstructure_drift),
    ("REGIME_SHIFT", wd_regime_shift),
]


def run_all_watchdogs(forensics: dict, rows: list = None) -> dict:
    """Run all 8 watchdogs. Returns a summary + list of fired alerts.

    Args:
        forensics: Output of forensics.pipeline.run_pipeline()
        rows: The verified prediction rows (needed for some watchdogs)

    Returns:
        {
          "ran_at_utc": ...,
          "n_watchdogs": 8,
          "n_fired": ...,
          "alerts": [ ... ],
          "status": "GREEN" | "YELLOW" | "RED",
        }
    """
    ran_at = datetime.now(timezone.utc).isoformat()
    history = []  # reserved for future cross-run trend detection
    alerts = []
    for name, fn in WATCHDOGS:
        try:
            alert = fn(forensics, history, rows) if name in (
                "CAPACITY_DRIFT", "MICROSTRUCTURE_DRIFT", "REGIME_SHIFT"
            ) else fn(forensics, history)
            if alert:
                alert["watchdog"] = name
                alert["fired_at_utc"] = ran_at
                alerts.append(alert)
                _append_alert(alert)
        except Exception as e:
            log.warning("watchdog %s failed: %s", name, e)
            alerts.append({
                "watchdog": name,
                "fired_at_utc": ran_at,
                "error": str(e),
            })

    # Status: RED if any HIGH severity fired, YELLOW if any MEDIUM, else GREEN
    severities = [a.get("severity") for a in alerts if a.get("severity")]
    if "HIGH" in severities:
        status = "RED"
    elif "MEDIUM" in severities:
        status = "YELLOW"
    else:
        status = "GREEN"

    return {
        "ran_at_utc": ran_at,
        "n_watchdogs": len(WATCHDOGS),
        "n_fired": len(alerts),
        "alerts": alerts,
        "status": status,
    }


def latest_alerts(limit: int = 50) -> list:
    """Read the most recent alerts from the append-only JSONL log."""
    if not ALERTS_FILE.exists():
        return []
    try:
        with open(ALERTS_FILE, "r") as f:
            lines = f.readlines()[-limit:]
        return [json.loads(l) for l in lines if l.strip()]
    except Exception as e:
        log.warning("failed to read alerts: %s", e)
        return []
