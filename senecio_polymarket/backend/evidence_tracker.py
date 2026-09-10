"""
SENECIO ORACLE — Evidence Tracker (ACT FINAL_AUDIT — A6)
==========================================================

STRICT_ADDITIVE evidence-accumulation tracker.

Per ACT FINAL_AUDIT A6:
  continue_until:
    verified_predictions >= 200
    long_trades          >= 100
    short_trades         >= 100

This module reads the latest forensic summary and produces a progress
report against those targets. Used to decide when sufficient evidence
exists to run the auto-decision engine (A8) and produce the executive
report (A9).

NEVER modifies trading logic. NEVER blocks.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("senecio.evidence_tracker")

# Targets (per A6 spec)
TARGET_VERIFIED = 200
TARGET_LONG = 100
TARGET_SHORT = 100


def get_progress(forensics_summary: Optional[dict] = None) -> dict:
    """Return progress vs targets.

    Args:
        forensics_summary: Output of forensics.pipeline.get_last_run_summary().
            If None, will try to load from state.

    Returns:
        {
          "targets": {"verified": 200, "long": 100, "short": 100},
          "current": {"verified": N, "long": N, "short": N},
          "pct": {"verified": 0-100, "long": 0-100, "short": 0-100},
          "remaining": {"verified": N, "long": N, "short": N},
          "all_targets_met": bool,
          "as_of_utc": iso,
        }
    """
    if forensics_summary is None:
        try:
            from .forensics import pipeline as fp
            forensics_summary = fp.get_last_run_summary()
        except Exception:
            forensics_summary = {}

    cur_verified = int(forensics_summary.get("n_verified") or 0)
    cur_long = int(forensics_summary.get("n_long") or 0)
    cur_short = int(forensics_summary.get("n_short") or 0)

    return {
        "targets": {"verified": TARGET_VERIFIED, "long": TARGET_LONG, "short": TARGET_SHORT},
        "current": {"verified": cur_verified, "long": cur_long, "short": cur_short},
        "pct": {
            "verified": round(100 * cur_verified / TARGET_VERIFIED, 1),
            "long": round(100 * cur_long / TARGET_LONG, 1),
            "short": round(100 * cur_short / TARGET_SHORT, 1),
        },
        "remaining": {
            "verified": max(0, TARGET_VERIFIED - cur_verified),
            "long": max(0, TARGET_LONG - cur_long),
            "short": max(0, TARGET_SHORT - cur_short),
        },
        "all_targets_met": (
            cur_verified >= TARGET_VERIFIED
            and cur_long >= TARGET_LONG
            and cur_short >= TARGET_SHORT
        ),
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
    }
