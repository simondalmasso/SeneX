"""
SENECIO ORACLE — Final Audit Orchestrator (ACT FINAL_AUDIT — A7+A8+A9)
=========================================================================

STRICT_ADDITIVE top-level orchestrator.

This module ties together:
  A7 — Statistical study suite      (statistical_study.run_full_study)
  A8 — Decision engine              (decision_engine.make_decision)
  A9 — Executive report             (executive_report.generate_executive_report)

Entry point: `run_final_audit_async()` — fetches verified rows from
Supabase, runs the full statistical study, evaluates patch candidates
against the A8 decision rules, and produces the A9 executive report.

NEVER raises. NEVER modifies trading logic. NEVER blocks the verifier.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("senecio.final_audit_orchestrator")


async def run_final_audit_async() -> dict:
    """Run the full A7+A8+A9 pipeline. Returns the executive report."""
    from . import statistical_study, decision_engine, executive_report
    from ..forensics import pipeline as forensics_pipeline
    from .. import evidence_tracker

    # 1. Fetch verified rows
    rows = await forensics_pipeline._fetch_verified(limit=1000)
    log.info("final_audit: fetched %d verified rows", len(rows))

    # 2. Run statistical study (A7) — sync, run in thread
    study = await asyncio.to_thread(statistical_study.run_full_study, rows)
    log.info("final_audit: statistical study complete (elapsed=%sms)",
             study.get("study_elapsed_ms"))

    # 3. Evidence progress (A6)
    evidence = evidence_tracker.get_progress({
        "n_verified": len(rows),
        "n_long": sum(1 for r in rows if (r.get("prediction") or "").upper() == "LONG"
                      and r.get("outcome") in ("WIN", "LOSS")),
        "n_short": sum(1 for r in rows if (r.get("prediction") or "").upper() == "SHORT"
                       and r.get("outcome") in ("WIN", "LOSS")),
    })

    # 4. Decision (A8)
    decision = decision_engine.make_decision(study, evidence)
    log.info("final_audit: decision=%s reason=%s",
             decision.get("decision"), decision.get("reason"))

    # 5. Forensics (latest)
    forensics_summary = forensics_pipeline.get_last_run_summary()
    forensics = {"summary": forensics_summary}

    # 6. Executive report (A9)
    report = executive_report.generate_executive_report(
        study=study,
        decision=decision,
        evidence=evidence,
        forensics=forensics,
    )
    log.info("final_audit: executive report generated verdict=%s go_no_go=%s confidence=%s",
             report.get("FINAL_VERDICT"),
             report.get("GO_NO_GO"),
             report.get("CONFIDENCE_SCORE"))

    return report


def run_final_audit_sync() -> dict:
    """Sync wrapper — runs the full audit in an event loop."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(run_final_audit_async())
