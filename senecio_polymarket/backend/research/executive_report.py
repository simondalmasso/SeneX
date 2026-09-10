"""
SENECIO ORACLE — Executive Report Generator (ACT FINAL_AUDIT — A9)
=====================================================================

STRICT_ADDITIVE report generator.

Produces the executive report required by A9:

  - FINAL_VERDICT          (NO_CHANGE | PROPOSE_PATCH | DEFER)
  - ROOT_CAUSES_ORDERED    (ranked list with effect size + CI)
  - PATCH_RECOMMENDATIONS  (specific patches IF AND ONLY IF statistically supported)
  - CONFIDENCE_SCORE       (0-100, weighted by sample size + p-value + bootstrap stability)
  - GO_NO_GO               (GO = proceed to implement; NO_GO = hold for more evidence)

This generator NEVER proposes a patch unless the decision engine (A8)
emits PROPOSE_PATCH. Default output is NO_CHANGE / NO_GO.

NEVER modifies trading logic.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("senecio.executive_report")

REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "executive_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _rank_root_causes(study: dict) -> list:
    """Rank candidate root causes by effect size + statistical strength."""
    candidates = []

    # From counterfactual search: each candidate filter is a "potential cause"
    cf = study.get("tests", {}).get("counterfactual_search", {}) or {}
    baseline_wr = cf.get("baseline_wr", 0.0)
    for r in cf.get("results", []):
        f = r.get("filter", {})
        delta_pp = r.get("wr_delta_pp", 0.0)
        n_removed = r.get("n_removed", 0)
        n_kept = r.get("n_kept", 0)
        if n_removed < 5:
            continue
        candidates.append({
            "cause": f.get("name", "unknown"),
            "feature": f.get("feature"),
            "bucket": f.get("excluded_bucket"),
            "n_in_bucket": n_removed,
            "n_after_filter": n_kept,
            "wr_delta_pp": delta_pp,
            "wr_with_filter": r.get("wr_with_filter"),
            "wr_without_filter": r.get("wr_without_filter"),
            "effect_size": abs(delta_pp),
            "evidence_strength": "STRONG" if abs(delta_pp) >= 15 else
                                 "MODERATE" if abs(delta_pp) >= 10 else
                                 "WEAK",
        })

    # From feature importance (model-free)
    fi = study.get("tests", {}).get("feature_importance_model_free", {}) or {}
    for feat, info in fi.items():
        if not isinstance(info, dict):
            continue
        spread = info.get("wr_spread", 0.0)
        if spread >= 0.10:
            candidates.append({
                "cause": f"{feat}_bucket_spread",
                "feature": feat,
                "bucket": f"best={info.get('best_bucket', {}).get('bucket')} worst={info.get('worst_bucket', {}).get('bucket')}",
                "n_in_bucket": info.get("n_buckets"),
                "wr_delta_pp": round(spread * 100, 2),
                "effect_size": spread,
                "evidence_strength": "STRONG" if spread >= 0.20 else "MODERATE",
            })

    # From permutation tests (long vs short)
    perm_ls = study.get("tests", {}).get("permutation_long_vs_short", {}) or {}
    if perm_ls.get("p_value") is not None and perm_ls["p_value"] < 0.05:
        candidates.append({
            "cause": "direction_asymmetry_long_vs_short",
            "feature": "prediction",
            "bucket": "LONG",
            "wr_delta_pp": round(abs(perm_ls.get("observed_diff", 0)) * 100, 2),
            "effect_size": abs(perm_ls.get("observed_diff", 0)),
            "p_value": perm_ls["p_value"],
            "evidence_strength": "STRONG" if perm_ls["p_value"] < 0.01 else "MODERATE",
        })

    # Sort: strongest effect first
    candidates.sort(key=lambda x: -x.get("effect_size", 0))
    return candidates


def _compute_confidence_score(study: dict, decision: dict, evidence: dict) -> int:
    """0-100 confidence score.

    Weighted by:
      - Evidence sample size (40 pts max: 200 verified = full)
      - Statistical significance (30 pts max: p < 0.01 = full)
      - Bootstrap stability (15 pts max: CI low > 0 = full)
      - CPCV stability (15 pts max: PBO < 0.3 = full)
    """
    score = 0

    # Evidence (40 pts)
    cur_v = evidence.get("current", {}).get("verified", 0)
    score += min(40, int((cur_v / 200) * 40))

    # Statistical significance (30 pts)
    perm_ls = study.get("tests", {}).get("permutation_long_vs_short", {}) or {}
    p = perm_ls.get("p_value")
    if p is not None:
        if p < 0.01:
            score += 30
        elif p < 0.05:
            score += 20
        elif p < 0.10:
            score += 10

    # Bootstrap stability (15 pts)
    boot = study.get("tests", {}).get("bootstrap_by_direction", {}) or {}
    long_boot = boot.get("LONG", {}) or {}
    if long_boot.get("ci_low") is not None:
        if long_boot["ci_low"] > 0.50:
            score += 15
        elif long_boot["ci_low"] > 0.45:
            score += 10
        elif long_boot["ci_low"] > 0.40:
            score += 5

    # CPCV stability (15 pts)
    cpcv = study.get("tests", {}).get("cpcv", {}) or {}
    pbo = cpcv.get("pbo")
    if pbo is not None:
        if pbo < 0.30:
            score += 15
        elif pbo < 0.50:
            score += 10
        elif pbo < 0.70:
            score += 5

    return min(100, score)


def generate_executive_report(study: dict, decision: dict,
                              evidence: dict, forensics: dict = None) -> dict:
    """Generate the final A9 executive report.

    Args:
        study:       Output of statistical_study.run_full_study()
        decision:    Output of decision_engine.make_decision()
        evidence:    Output of evidence_tracker.get_progress()
        forensics:   Latest forensics pipeline output (optional)

    Returns:
        Dict with FINAL_VERDICT, ROOT_CAUSES_ORDERED, PATCH_RECOMMENDATIONS,
        CONFIDENCE_SCORE, GO_NO_GO.
    """
    ts = datetime.now(timezone.utc).isoformat()

    # FINAL_VERDICT
    if decision.get("decision") == "PROPOSE_PATCH":
        final_verdict = "PROPOSE_PATCH"
    elif decision.get("decision") == "DEFER":
        final_verdict = "NO_CHANGE"
    else:
        final_verdict = "NO_CHANGE"

    # ROOT_CAUSES_ORDERED
    root_causes = _rank_root_causes(study)

    # PATCH_RECOMMENDATIONS — only if decision says PROPOSE_PATCH
    patch_recs = []
    if final_verdict == "PROPOSE_PATCH":
        for cand in decision.get("candidate_evaluation", {}).get("top_proposed", []):
            cf = cand.get("candidate", {})
            patch_recs.append({
                "patch_id": f"PATCH_CF_{cf.get('feature', 'unknown')}_{cf.get('excluded_bucket', 'x')}",
                "description": (
                    f"Exclude {cf.get('feature')}={cf.get('excluded_bucket')} from "
                    f"LONG trade entries (saves {cand.get('n_removed')} trades, "
                    f"WR delta +{cand.get('wr_delta_pp')}pp)"
                ),
                "evidence": {
                    "delta_wr_pp": cand.get("wr_delta_pp"),
                    "n_removed": cand.get("n_removed"),
                    "n_kept": cand.get("n_kept"),
                    "checks": cand.get("checks"),
                },
                "implementation_hint": (
                    f"In signal_generation, add guard: if enriched.{cf.get('feature')} == "
                    f"'{cf.get('excluded_bucket')}': skip LONG entry"
                ),
                "WARNING": (
                    "Per FINAL_AUDIT directive A5, this patch is NOT to be applied. "
                    "It is recorded here as a candidate for human review only."
                ),
            })

    # CONFIDENCE_SCORE
    confidence = _compute_confidence_score(study, decision, evidence)

    # GO_NO_GO
    # GO requires: PROPOSE_PATCH verdict AND confidence >= 70 AND evidence targets met
    go_no_go = "GO" if (
        final_verdict == "PROPOSE_PATCH"
        and confidence >= 70
        and evidence.get("all_targets_met", False)
    ) else "NO_GO"

    report = {
        "report_generated_at_utc": ts,
        "version": "A9-2026-06-20-v1",
        "act": "FINAL_AUDIT_MYTHOS",
        "mode": "STRICT_ADDITIVE",
        "trade_mode": "PAPER_ONLY",
        "live_gate": "LOCKED",

        "FINAL_VERDICT": final_verdict,
        "ROOT_CAUSES_ORDERED": root_causes[:10],
        "PATCH_RECOMMENDATIONS": patch_recs,
        "CONFIDENCE_SCORE": confidence,
        "GO_NO_GO": go_no_go,

        "EVIDENCE_PROGRESS": evidence,
        "DECISION_DETAIL": decision,
        "STUDY_SUMMARY": {
            "n_rows": study.get("n_rows_input"),
            "study_elapsed_ms": study.get("study_elapsed_ms"),
            "tests_run": list(study.get("tests", {}).keys()),
        },
        "FORENSICS_SUMMARY": (forensics or {}).get("summary") if forensics else None,

        "DISCLAIMER": (
            "Per ACT FINAL_AUDIT (MYTHOS) STRICT_ADDITIVE directive, this report "
            "DOES NOT implement any patch. PROPOSE_PATCH recommendations are "
            "candidates for human review only. The trading system continues to "
            "operate in PAPER mode with LIVE_GATE locked until a human explicitly "
            "authorizes implementation."
        ),
    }

    # Persist report
    try:
        ts_file = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_file = REPORTS_DIR / f"executive_report_{ts_file}.json"
        out_file.write_text(json.dumps(report, default=str, indent=2))
        log.info("executive report saved: %s", out_file)
    except Exception as e:
        log.warning("failed to save executive report: %s", e)

    return report


def latest_report() -> Optional[dict]:
    """Return the most recent executive report, or None."""
    if not REPORTS_DIR.exists():
        return None
    files = sorted(REPORTS_DIR.glob("executive_report_*.json"), reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text())
    except Exception as e:
        log.warning("failed to read latest report: %s", e)
        return None
