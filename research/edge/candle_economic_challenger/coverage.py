"""Pre-label T0 eligibility audit. Read-only; never requests missing OHLCV."""
from __future__ import annotations
from collections import Counter
from .contracts import parse_snapshot, utc, EvidenceError

_BUCKETS=("00","15","30","45")

def report_alignment_coverage(snapshots):
    """Audit every supplied T0 by UTC decision minute, baseline side and regime.

    No market/settlement read or reconstruction is permitted. Incomplete metadata
    is counted as excluded, not assigned zero pattern events.
    """
    buckets={m:{"total":0,"eligible":0,"excluded":0,"reasons":{}} for m in _BUCKETS}
    side={}
    regime={}
    reasons=Counter()
    for row in snapshots:
        if not isinstance(row,dict):
            raise EvidenceError("non-object T0 coverage record")
        minute=utc(row.get("decision_ts")).minute
        if minute not in (0,15,30,45):
            raise EvidenceError("unexpected T0 cadence; requires separate prespecification")
        key=f"{minute:02d}"
        group=buckets[key]
        group["total"]+=1
        s=str(row.get("side") or "UNKNOWN")
        g=str(row.get("regime") or "UNKNOWN")
        for table,label in ((side,s),(regime,g)):
            obj=table.setdefault(label,{"total":0,"eligible":0,"excluded":0})
            obj["total"]+=1
        try:
            parse_snapshot(row)
            eligible=True
            reason=None
        except EvidenceError as exc:
            eligible=False
            reason=("HOUR_ALIGNMENT" if "boundary misaligned" in str(exc)
                    else "INSUFFICIENT_HISTORY" if "history" in str(exc)
                    else "CAUSAL_OR_INVALID_OHLCV")
        name="eligible" if eligible else "excluded"
        group[name]+=1
        side[s][name]+=1
        regime[g][name]+=1
        if reason:
            reasons[reason]+=1
            group["reasons"][reason]=group["reasons"].get(reason,0)+1
    return {"total_observed_t0":len(snapshots),
            "n_eligible":sum(b["eligible"] for b in buckets.values()),
            "n_excluded":sum(b["excluded"] for b in buckets.values()),
            "by_minute":buckets,"by_side":side,"by_regime":regime,
            "excluded_by_reason":dict(sorted(reasons.items())),
            "proposed_cohort":"PREREGISTERED_HOURLY_ONLY",
            "bars_needed_for_all_offset_15m_closes":19,
            "backfilled_bars":0,"outcomes_used":False,
            "sample_is_real_custody_verified":False}
