from __future__ import annotations

import math
from typing import Any


def _wilson(wins: int, n: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p=wins/n
    denom=1+z*z/n
    centre=(p+z*z/(2*n))/denom
    radius=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/denom
    return max(0.0,centre-radius), min(1.0,centre+radius)


def _bucket(rows: list[dict[str, Any]], direction: str) -> dict[str, Any]:
    selected=[r for r in rows if str(r.get("final_prediction") or "").upper()==direction]
    wins=sum(str(r.get("native_directional_outcome") or "").upper()=="WIN" for r in selected)
    losses=sum(str(r.get("native_directional_outcome") or "").upper()=="LOSS" for r in selected)
    n=wins+losses
    lo,hi=_wilson(wins,n)
    return {
        "n":n,
        "wins":wins,
        "losses":losses,
        "win_rate":(wins/n if n else None),
        "wilson_95":[lo,hi] if lo is not None else None,
    }


def analyze_long_short(records: list[dict[str, Any]]) -> dict[str, Any]:
    historical=[r for r in records if r.get("cohort_status")=="DIAGNOSTIC_ONLY_HISTORICAL"]
    authority=[r for r in records if r.get("cohort_status")=="AUTHORITY_CANDIDATE"]
    excluded=[r for r in records if str(r.get("cohort_status") or "").startswith("EXCLUDED_")]
    return {
        "version":"hyp002-native-directional-v1",
        "historical_diagnostic":{
            "LONG":_bucket(historical,"LONG"),
            "SHORT":_bucket(historical,"SHORT"),
            "diagnostic_only":True,
        },
        "prospective_authority_candidates":{
            "LONG":_bucket(authority,"LONG"),
            "SHORT":_bucket(authority,"SHORT"),
            "authority_candidate_only":True,
        },
        "excluded_rows":len(excluded),
        "authority_candidate_n":len(authority),
        "edge_status":"UNPROVEN",
        "brier_logloss_allowed":False,
        "verdict":"INCONCLUSIVE" if not authority else "PROSPECTIVE_EVIDENCE_ACCUMULATING",
    }
