"""Shared PAPER-locked live-gate projection for legacy and public apps."""
from __future__ import annotations
from typing import Any

def paper_locked_live_gate_from_score(coord: Any, score: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a score under the immutable PAPER policy without unlocking it."""
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
        "diagnostic_only": True, "effective_gate": "LOCKED_BY_PAPER_POLICY",
        "paper_only": True, "score_scope": score.get("score_scope"),
        "requested_symbol": score.get("requested_symbol"),
        "authority_cohort": score.get("authority_cohort"),
        "authority_n_source": (score.get("authority_1h") or {}).get("n_source"),
        "verified": int(score.get("independent_1h_rows") or 0),
        "proof_qualified_rows_raw": int(score.get("proof_qualified_rows_raw") or 0),
        "conditions_passed": sum(1 for item in conditions.values() if isinstance(item, dict) and item.get("pass")),
        "conditions_total": len(conditions),
    })
    return state
