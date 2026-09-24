from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from edge_lab.arq2_score_denominator_007 import (
    label_candidate as label_candidate_007,
    rank_ic as _rank_ic_007,
    verify_join_integrity,
)

REFERENCE_CORE_HEAD = "c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a"
FREEZE_TS_UTC = "2026-09-24T00:23:35Z"
EARLIEST_POSSIBLE_COMPLETE_READ_UTC = "2026-09-28T00:23:35Z"
TARGET_NEW_NONOVERLAP_N = 96
MIN_MATURE_AGE_S = 3600
NONOVERLAP_SPACING_S = 3600
PAGE_SIZE = 80
BOOTSTRAP_REPLICATES = 10000
BOOTSTRAP_SEED = 20260924
RETRO_DELTA_AUC_010 = 0.0080971659919028

TOP2_COMPONENT_TO_FEATURE = {
    "orderflow": "orderflow",
    "funding": "funding_signal",
    "oi": "oi_momentum",
}
OBSERVED_STATUSES = {"REAL_OBSERVED_ZERO", "REAL_NONZERO"}


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def collection_read_allowed(now: datetime) -> bool:
    return _dt(now) >= _dt(EARLIEST_POSSIBLE_COMPLETE_READ_UTC)


def assert_collection_read_allowed(now: datetime) -> None:
    if not collection_read_allowed(now):
        raise RuntimeError(
            "D1_EARLY_READ_BLOCKED:"
            f"{_dt(now).isoformat()}<"
            f"{_dt(EARLIEST_POSSIBLE_COMPLETE_READ_UTC).isoformat()}"
        )


def build_hot_query(cursor: int | None, *, page_size: int = PAGE_SIZE) -> str:
    if int(page_size) <= 0 or int(page_size) > PAGE_SIZE:
        raise ValueError("INVALID_PAGE_SIZE")
    where = "" if cursor is None else f" WHERE id < {int(cursor)}"
    return (
        "SELECT id,ts,symbol,prediction,confidence,ev,price_now,"
        "exchange_used,created_at,audit_digest,cold_payload_sha256 "
        "FROM oracle_predictions_hot"
        f"{where} ORDER BY id DESC LIMIT {int(page_size)};"
    )


def deterministic_nonoverlap(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        list(rows),
        key=lambda row: (
            _dt(row["ts"]),
            int(row.get("source_prediction_id", row.get("id", 0))),
            _canonical_json(row),
        ),
    )
    selected: list[dict[str, Any]] = []
    last_ts: datetime | None = None
    for row in ordered:
        ts = _dt(row["ts"])
        if last_ts is None or ts >= last_ts + timedelta(seconds=NONOVERLAP_SPACING_S):
            selected.append(row)
            last_ts = ts
    return selected


def select_frozen_cohort(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = deterministic_nonoverlap(rows)
    return selected[:TARGET_NEW_NONOVERLAP_N]


def row_is_eligible(
    row: dict[str, Any],
    *,
    evaluation_time: datetime,
) -> tuple[bool, str]:
    if str(row.get("symbol") or "").upper() != "BTCUSDT":
        return False, "SYMBOL_MISMATCH"

    ts = _dt(row.get("ts"))
    if ts <= _dt(FREEZE_TS_UTC):
        return False, "NOT_POST_FREEZE"
    if ts > _dt(evaluation_time) - timedelta(seconds=MIN_MATURE_AGE_S):
        return False, "NOT_MATURE"

    audit = row.get("audit")
    if not isinstance(audit, dict):
        return False, "AUDIT_MISSING"
    replay = audit.get("decision_replay_v1")
    provenance = replay.get("runtime_provenance") if isinstance(replay, dict) else None
    if not isinstance(provenance, dict):
        return False, "RUNTIME_PROVENANCE_MISSING"
    if provenance.get("exact") is not True:
        return False, "RUNTIME_PROVENANCE_NOT_EXACT"
    if provenance.get("source_commit") != REFERENCE_CORE_HEAD:
        return False, "RUNTIME_PROVENANCE_COMMIT_MISMATCH"

    return True, "PASS"


def _step2(audit: dict[str, Any]) -> dict[str, Any]:
    pipeline = audit.get("pipeline")
    step2 = pipeline.get("step2_features") if isinstance(pipeline, dict) else None
    if not isinstance(step2, dict):
        raise ValueError("STEP2_FEATURES_MISSING")
    return step2


def effective_top2_component(
    *,
    component: str,
    audit: dict[str, Any],
) -> dict[str, Any]:
    if component not in TOP2_COMPONENT_TO_FEATURE:
        raise ValueError("UNKNOWN_TOP2_COMPONENT")

    pipeline = audit.get("pipeline")
    if not isinstance(pipeline, dict):
        raise ValueError("NULL_SEMANTICS_UNPROVEN:PIPELINE")
    step1 = pipeline.get("step1_market")
    step2 = pipeline.get("step2_features")
    if not isinstance(step1, dict) or not isinstance(step2, dict):
        raise ValueError("NULL_SEMANTICS_UNPROVEN:PIPELINE_STAGES")

    pressures = step2.get("pressures")
    if not isinstance(pressures, dict):
        raise ValueError("NULL_SEMANTICS_UNPROVEN:PRESSURES")

    value = pressures.get(component)
    if value is not None:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("NULL_SEMANTICS_UNPROVEN:NONFINITE")
        return {
            "effective_value": parsed,
            "persisted_value": parsed,
            "recovered_by_runtime_semantics": False,
        }

    feature = TOP2_COMPONENT_TO_FEATURE[component]
    availability = step1.get("feature_availability_v1")
    state = availability.get(feature) if isinstance(availability, dict) else None
    if not isinstance(state, dict):
        raise ValueError("NULL_SEMANTICS_UNPROVEN:AVAILABILITY")
    status = state.get("status")
    if status is None or status in OBSERVED_STATUSES:
        raise ValueError("NULL_SEMANTICS_UNPROVEN:AVAILABILITY")
    if state.get("fallback_value") is None or float(state.get("fallback_value")) != 0.0:
        raise ValueError("NULL_SEMANTICS_UNPROVEN:AVAILABILITY")

    mask = step2.get("missing_input_mask_v1")
    masked = mask.get("masked_features") if isinstance(mask, dict) else None
    if not isinstance(masked, list) or feature not in masked:
        raise ValueError("NULL_SEMANTICS_UNPROVEN:MASK")

    replay = audit.get("decision_replay_v1")
    provenance = replay.get("runtime_provenance") if isinstance(replay, dict) else None
    if (
        not isinstance(provenance, dict)
        or provenance.get("exact") is not True
        or provenance.get("source_commit") != REFERENCE_CORE_HEAD
    ):
        raise ValueError("NULL_SEMANTICS_UNPROVEN:RUNTIME_PROVENANCE")

    numeric_values = [
        float(v)
        for v in pressures.values()
        if isinstance(v, (int, float)) and math.isfinite(float(v))
    ]
    total = step2.get("total_pressure")
    if total is None or not math.isfinite(float(total)):
        raise ValueError("NULL_SEMANTICS_UNPROVEN:TOTAL_PRESSURE")
    if abs(float(total) - sum(numeric_values)) > 1e-6:
        raise ValueError("NULL_SEMANTICS_UNPROVEN:ALGEBRA")

    return {
        "effective_value": 0.0,
        "persisted_value": None,
        "recovered_by_runtime_semantics": True,
        "feature": feature,
        "availability_status": status,
        "fallback_value": 0.0,
        "masked": True,
        "algebra_match": True,
    }


def frozen_top2_scores(audit: dict[str, Any]) -> dict[str, Any]:
    step2 = _step2(audit)
    total = step2.get("total_pressure")
    if total is None or not math.isfinite(float(total)):
        raise ValueError("TOTAL_PRESSURE_MISSING")

    parts = {
        name: effective_top2_component(component=name, audit=audit)
        for name in ("orderflow", "funding", "oi")
    }
    top2 = sum(float(parts[name]["effective_value"]) for name in parts)
    full = float(total)
    return {
        "full_score": full,
        "top2_pressure": top2,
        "no_top2_score": full - top2,
        "micro_only_score": top2,
        "component_semantics": parts,
    }


def initialize_manifest() -> dict[str, Any]:
    return {
        "num_order": "ARQ2-TOP2-PROSPECTIVE-011",
        "mode": "PROSPECTIVE_RESEARCH_ONLY",
        "reference_core_head": REFERENCE_CORE_HEAD,
        "freeze_ts_utc": FREEZE_TS_UTC,
        "earliest_possible_complete_read_utc": EARLIEST_POSSIBLE_COMPLETE_READ_UTC,
        "target_new_nonoverlap_n": TARGET_NEW_NONOVERLAP_N,
        "min_mature_age_s": MIN_MATURE_AGE_S,
        "nonoverlap_spacing_s": NONOVERLAP_SPACING_S,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "retro_delta_auc_010": RETRO_DELTA_AUC_010,
        "no_early_look": True,
        "top1_tested": False,
        "top3_tested": False,
        "individual_component_tests": 0,
        "parameters_tuned": 0,
        "d1_writes": 0,
        "edge": "UNPROVEN",
    }


def initialize_progress() -> dict[str, Any]:
    return {
        "num_order": "ARQ2-TOP2-PROSPECTIVE-011",
        "status": "COLLECTING",
        "progress_n": 0,
        "eligible_full_n": 0,
        "excluded_n": 0,
        "data_integrity": "PASS",
        "no_early_look": "PASS",
        "scan_high_water_id": None,
        "scan_low_water_id": None,
        "last_read_at_utc": None,
        "next_normal_read_not_before_utc": EARLIEST_POSSIBLE_COMPLETE_READ_UTC,
        "d1_writes": 0,
    }


def should_stop_at_known_high_water(
    current_id: int,
    progress: dict[str, Any],
) -> bool:
    high = progress.get("scan_high_water_id")
    if high is None:
        return False
    return int(current_id) <= int(high)


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _average_ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(float(v) for v in values), key=lambda pair: pair[1])
    ranks = [0.0] * len(indexed)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        avg = (start + 1 + end) / 2.0
        for pos in range(start, end):
            ranks[indexed[pos][0]] = avg
        start = end
    return ranks


def _percentile(values: Sequence[float], p: float) -> float:
    ordered = sorted(float(v) for v in values)
    if not ordered:
        raise ValueError("EMPTY_PERCENTILE")
    x = (len(ordered) - 1) * p
    lo = int(math.floor(x))
    hi = int(math.ceil(x))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - x) + ordered[hi] * (x - lo)


def _bootstrap_auc_ci(
    pairs: Sequence[tuple[float, int]],
    *,
    replicates: int,
    seed: int,
) -> list[float]:
    material = list(pairs)
    if len(material) != TARGET_NEW_NONOVERLAP_N:
        raise ValueError("NO_EARLY_LOOK:BOOTSTRAP_REQUIRES_EXACT_N_96")
    from edge_lab.arq2_score_readback_004 import roc_auc
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(replicates):
        indices = [rng.randrange(len(material)) for _ in range(len(material))]
        labels = {int(material[i][1]) for i in indices}
        if len(labels) < 2:
            continue
        auc = roc_auc([material[i] for i in indices])
        if auc is not None:
            values.append(float(auc))
    return [_percentile(values, 0.025), _percentile(values, 0.975)]


def _paired_delta(
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    if len(rows) != TARGET_NEW_NONOVERLAP_N:
        raise ValueError("NO_EARLY_LOOK:DELTA_REQUIRES_EXACT_N_96")
    from edge_lab.arq2_score_readback_004 import roc_auc

    full_pairs = [(float(r["full_score"]), int(r["y_up"])) for r in rows]
    no_pairs = [(float(r["no_top2_score"]), int(r["y_up"])) for r in rows]
    auc_full = roc_auc(full_pairs)
    auc_no = roc_auc(no_pairs)
    if auc_full is None or auc_no is None:
        raise ValueError("AUC_UNDEFINED")

    rng = random.Random(BOOTSTRAP_SEED)
    deltas: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        indices = [rng.randrange(len(rows)) for _ in range(len(rows))]
        labels = {int(rows[i]["y_up"]) for i in indices}
        if len(labels) < 2:
            continue
        b_full = roc_auc([
            (float(rows[i]["full_score"]), int(rows[i]["y_up"]))
            for i in indices
        ])
        b_no = roc_auc([
            (float(rows[i]["no_top2_score"]), int(rows[i]["y_up"]))
            for i in indices
        ])
        if b_full is not None and b_no is not None:
            deltas.append(float(b_full - b_no))

    ci = [_percentile(deltas, 0.025), _percentile(deltas, 0.975)]
    if ci[0] > 0:
        verdict = "POSITIVE_CONTRIBUTION"
    elif ci[1] < 0:
        verdict = "NEGATIVE_CONTRIBUTION"
    else:
        verdict = "INCONCLUSIVE"

    return {
        "auc_full": float(auc_full),
        "auc_no_top2": float(auc_no),
        "delta_auc_top2": float(auc_full - auc_no),
        "delta_auc_ci95": ci,
        "bootstrap_valid_replicates": len(deltas),
        "top2_prospective_verdict": verdict,
    }


def final_analysis(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) != TARGET_NEW_NONOVERLAP_N:
        raise ValueError(
            f"NO_EARLY_LOOK:REQUIRES_EXACT_N_{TARGET_NEW_NONOVERLAP_N}"
        )
    if any(row.get("y_up") not in (0, 1) for row in rows):
        raise ValueError("LABELS_INCOMPLETE")

    from edge_lab.arq2_score_readback_004 import roc_auc

    primary = _paired_delta(rows)
    full_pairs = [(float(r["full_score"]), int(r["y_up"])) for r in rows]
    micro_pairs = [(float(r["micro_only_score"]), int(r["y_up"])) for r in rows]

    direction_rows = [
        r for r in rows
        if str(r.get("step2_direction") or "").upper() in {"LONG", "SHORT"}
    ]
    direction_correct = sum(
        1
        for r in direction_rows
        if (
            str(r["step2_direction"]).upper() == "LONG"
            and int(r["y_up"]) == 1
        )
        or (
            str(r["step2_direction"]).upper() == "SHORT"
            and int(r["y_up"]) == 0
        )
    )
    sign_agreement = sum(
        1
        for r in rows
        if _sign(float(r["top2_pressure"])) == _sign(float(r["full_score"]))
    ) / len(rows)
    sign_flip = sum(
        1
        for r in rows
        if _sign(float(r["full_score"])) != _sign(float(r["no_top2_score"]))
    ) / len(rows)

    prospective_delta = float(primary["delta_auc_top2"])
    return {
        **primary,
        "auc_full_ci95": _bootstrap_auc_ci(
            full_pairs,
            replicates=BOOTSTRAP_REPLICATES,
            seed=BOOTSTRAP_SEED,
        ),
        "auc_micro_only": roc_auc(micro_pairs),
        "rank_ic_full": _rank_ic_007(full_pairs),
        "rank_ic_micro_only": _rank_ic_007(micro_pairs),
        "top2_sign_agreement_rate": sign_agreement,
        "top2_sign_flip_rate": sign_flip,
        "raw_direction_accuracy": (
            direction_correct / len(direction_rows) if direction_rows else None
        ),
        "retro_delta_auc_010": RETRO_DELTA_AUC_010,
        "prospective_delta_auc_011": prospective_delta,
        "delta_direction_consistent": (
            "YES"
            if _sign(RETRO_DELTA_AUC_010) == _sign(prospective_delta)
            else "NO"
        ),
        "n": len(rows),
    }


def collect_hot_bounded(
    *,
    now: datetime,
    progress: dict[str, Any],
    runner,
    max_pages: int,
) -> dict[str, Any]:
    assert_collection_read_allowed(now)
    if int(max_pages) <= 0:
        raise ValueError("INVALID_MAX_PAGES")

    known_high = progress.get("scan_high_water_id")
    cursor: int | None = None
    collected: list[dict[str, Any]] = []
    page_evidence: list[dict[str, Any]] = []
    newest_seen: int | None = None
    lowest_seen: int | None = None

    for page_no in range(1, int(max_pages) + 1):
        sql = build_hot_query(cursor, page_size=PAGE_SIZE)
        rows, meta, page_hash = runner(sql)
        if int(meta.get("rows_written") or 0) != 0 or bool(meta.get("changed_db")):
            raise RuntimeError("D1_WRITE_DETECTED")
        if len(rows) > PAGE_SIZE:
            raise RuntimeError("HOT_PAGE_OVERBOUND")

        page_evidence.append(
            {
                "page": page_no,
                "rows": len(rows),
                "rows_read": meta.get("rows_read"),
                "rows_written": meta.get("rows_written"),
                "changed_db": meta.get("changed_db"),
                "sha256": page_hash,
            }
        )
        if not rows:
            break

        ids = [int(row["id"]) for row in rows]
        page_max = max(ids)
        page_min = min(ids)
        newest_seen = page_max if newest_seen is None else max(newest_seen, page_max)
        lowest_seen = page_min if lowest_seen is None else min(lowest_seen, page_min)

        stop = False
        for row in rows:
            row_id = int(row["id"])
            if known_high is not None and row_id <= int(known_high):
                stop = True
                continue
            collected.append(row)

        if stop:
            break
        cursor = page_min

    next_progress = dict(progress)
    if newest_seen is not None:
        next_progress["scan_high_water_id"] = (
            newest_seen
            if known_high is None
            else max(int(known_high), newest_seen)
        )
    if lowest_seen is not None:
        next_progress["scan_low_water_id"] = lowest_seen
    next_progress["last_read_at_utc"] = _dt(now).isoformat()
    next_progress["d1_read_attempts"] = int(
        next_progress.get("d1_read_attempts") or 0
    ) + len(page_evidence)
    next_progress["d1_rows_read"] = int(
        next_progress.get("d1_rows_read") or 0
    ) + sum(int(item.get("rows_read") or 0) for item in page_evidence)
    next_progress["d1_writes"] = 0

    return {
        "rows": collected,
        "progress": next_progress,
        "page_evidence": page_evidence,
    }


def prepare_candidate_from_hot_cold(
    hot: dict[str, Any],
    cold: dict[str, Any],
    *,
    evaluation_time: datetime,
) -> dict[str, Any]:
    audit = verify_join_integrity(hot, cold)
    gate_row = {
        "id": int(hot["id"]),
        "source_prediction_id": int(hot["id"]),
        "ts": hot.get("ts"),
        "symbol": hot.get("symbol"),
        "audit": audit,
    }
    eligible, reason = row_is_eligible(
        gate_row,
        evaluation_time=evaluation_time,
    )
    if not eligible:
        raise ValueError(f"ROW_INELIGIBLE:{reason}")

    origin = audit.get("origin_price_v1")
    if not isinstance(origin, dict):
        raise ValueError("ORIGIN_PRICE_PROOF_MISSING")

    scores = build_scores(audit)
    pipeline = audit.get("pipeline") or {}
    step2 = pipeline.get("step2_features") or {}
    action_vector = audit.get("action_vector") or {}
    return {
        "source_prediction_id": int(hot["id"]),
        "ts": _dt(hot.get("ts")).isoformat(),
        "exchange_used": str(hot.get("exchange_used") or "").strip().lower(),
        "final_prediction": str(hot.get("prediction") or "").upper(),
        "action": str(action_vector.get("action") or "").upper(),
        "step2_direction": str(step2.get("direction") or "").upper(),
        "origin_price": origin.get("price"),
        "origin_price_v1": dict(origin),
        **scores,
    }


def build_scores(audit: dict[str, Any]) -> dict[str, Any]:
    return frozen_top2_scores(audit)


def label_action_independent(
    candidate: dict[str, Any],
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    label_input = {
        key: value
        for key, value in candidate.items()
        if key not in {"action", "final_prediction"}
    }
    labeled = label_candidate_007(label_input, evidence)
    labeled["source_prediction_id"] = candidate.get("source_prediction_id")
    labeled["action"] = candidate.get("action")
    labeled["final_prediction"] = candidate.get("final_prediction")
    labeled["step2_direction"] = candidate.get("step2_direction")
    labeled["full_score"] = candidate.get("full_score")
    labeled["no_top2_score"] = candidate.get("no_top2_score")
    labeled["top2_pressure"] = candidate.get("top2_pressure")
    labeled["micro_only_score"] = candidate.get("micro_only_score")
    return labeled


def rows_artifact_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
