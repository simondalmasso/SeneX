from __future__ import annotations

import collections
import hashlib
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

FREEZE_TS_UTC = "2026-09-24T00:23:35Z"
EARLIEST_POSSIBLE_COMPLETE_READ_UTC = "2026-09-28T00:23:35Z"
REFERENCE_CORE_HEAD = "c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a"
TARGET_NEW_NONOVERLAP_N = 96
MIN_MATURE_AGE_S = 3600
NONOVERLAP_SPACING_S = 3600
PAGE_SIZE = 80
REPLICATES = 10000
BOOTSTRAP_SEED = 20260924
RETRO_DELTA_AUC_010 = 0.0080971659919028
ALGEBRA_TOLERANCE = 1e-6

TOP2_COMPONENT_FEATURE = {
    "orderflow": "orderflow",
    "funding": "funding",
    "oi": "oi_momentum",
}

HOT_COLUMNS = (
    "id,ts,symbol,prediction,confidence,ev,price_now,outcome,exchange_used,"
    "created_at,audit_digest,cold_payload_sha256"
)


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
        f"SELECT {HOT_COLUMNS} FROM oracle_predictions_hot"
        f"{where} ORDER BY id DESC LIMIT {int(page_size)};"
    )


def initialize_manifest() -> dict[str, Any]:
    return {
        "num_order": "ARQ2-TOP2-PROSPECTIVE-011",
        "mode": "PROSPECTIVE_RESEARCH_ONLY",
        "freeze_ts_utc": FREEZE_TS_UTC,
        "earliest_possible_complete_read_utc": EARLIEST_POSSIBLE_COMPLETE_READ_UTC,
        "reference_core_head": REFERENCE_CORE_HEAD,
        "target_new_nonoverlap_n": TARGET_NEW_NONOVERLAP_N,
        "min_mature_age_s": MIN_MATURE_AGE_S,
        "nonoverlap_spacing_s": NONOVERLAP_SPACING_S,
        "label_semantics": "SENEX_NATIVE_STRICT_UP_T_PLUS_3600",
        "top2_components": ["orderflow", "funding", "oi"],
        "bootstrap": {
            "replicates": REPLICATES,
            "seed": BOOTSTRAP_SEED,
            "paired_indices": True,
        },
        "no_early_look": True,
        "top1_tested": False,
        "top3_tested": False,
        "strategy_curator": "BACKLOG_ONLY",
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
        "scan_high_water_ts": None,
        "next_desc_cursor_id": None,
        "last_collection_attempt_utc": None,
        "last_collection_result": "NOT_STARTED_EARLY_GATE",
        "d1_reads": 0,
        "d1_writes": 0,
    }


def should_stop_at_known_high_water(
    row_id: int,
    progress: dict[str, Any],
) -> bool:
    high_water = progress.get("scan_high_water_id")
    if high_water is None:
        return False
    return int(row_id) <= int(high_water)




def collect_hot_bounded(
    *,
    now: datetime,
    progress: dict[str, Any],
    runner,
    max_pages: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    assert_collection_read_allowed(now)

    local_progress = json.loads(json.dumps(progress))
    cursor = None
    collected: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    newest_seen_id: int | None = None
    newest_seen_ts: str | None = None

    for page_no in range(1, int(max_pages) + 1):
        sql = build_hot_query(cursor, page_size=PAGE_SIZE)
        rows, meta, page_hash = runner(sql)

        if int(meta.get("rows_written") or 0) != 0 or bool(meta.get("changed_db")):
            raise RuntimeError("D1_WRITE_DETECTED")

        evidence.append(
            {
                "page": page_no,
                "sha256": page_hash,
                "rows": len(rows),
                "rows_written": meta.get("rows_written"),
                "changed_db": meta.get("changed_db"),
            }
        )
        local_progress["d1_reads"] = int(local_progress.get("d1_reads") or 0) + 1

        if not rows:
            break

        stop = False
        for row in rows:
            row_id = int(row["id"])
            if newest_seen_id is None or row_id > newest_seen_id:
                newest_seen_id = row_id
                newest_seen_ts = row.get("ts")

            if should_stop_at_known_high_water(row_id, progress):
                stop = True
                break
            collected.append(row)

        if stop:
            break

        cursor = min(int(row["id"]) for row in rows)
        local_progress["next_desc_cursor_id"] = cursor

        if len(rows) < PAGE_SIZE:
            break

    if newest_seen_id is not None:
        old_high = local_progress.get("scan_high_water_id")
        if old_high is None or newest_seen_id > int(old_high):
            local_progress["scan_high_water_id"] = newest_seen_id
            local_progress["scan_high_water_ts"] = newest_seen_ts

    local_progress["last_collection_attempt_utc"] = _dt(now).isoformat()
    local_progress["last_collection_result"] = "BOUNDED_HOT_READ_COMPLETE"
    return collected, local_progress, evidence


def _runtime_provenance(audit: dict[str, Any]) -> dict[str, Any] | None:
    replay = audit.get("decision_replay_v1")
    if not isinstance(replay, dict):
        return None
    provenance = replay.get("runtime_provenance")
    return provenance if isinstance(provenance, dict) else None


def row_is_eligible(
    row: dict[str, Any],
    *,
    evaluation_time: datetime,
) -> tuple[bool, str]:
    if str(row.get("symbol") or "").upper() != "BTCUSDT":
        return False, "SYMBOL_MISMATCH"

    try:
        ts = _dt(row.get("ts"))
    except Exception:
        return False, "TIMESTAMP_INVALID"

    if ts <= _dt(FREEZE_TS_UTC):
        return False, "NOT_STRICTLY_POST_FREEZE"
    if ts > _dt(evaluation_time) - timedelta(seconds=MIN_MATURE_AGE_S):
        return False, "NOT_MATURE"

    audit = row.get("audit")
    if not isinstance(audit, dict):
        return False, "AUDIT_MISSING"

    provenance = _runtime_provenance(audit)
    if not isinstance(provenance, dict):
        return False, "RUNTIME_PROVENANCE_MISSING"
    if provenance.get("exact") is not True:
        return False, "RUNTIME_PROVENANCE_NOT_EXACT"
    if provenance.get("source_commit") != REFERENCE_CORE_HEAD:
        return False, "RUNTIME_PROVENANCE_COMMIT_MISMATCH"

    return True, "PASS"


def deterministic_nonoverlap(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    stable = sorted(
        list(rows),
        key=lambda row: (
            _dt(row.get("ts")).timestamp(),
            int(row.get("source_prediction_id", row.get("id", 0)) or 0),
            _canonical_json(row),
        ),
    )
    selected: list[dict[str, Any]] = []
    last_ts: datetime | None = None
    for row in stable:
        ts = _dt(row.get("ts"))
        if (
            last_ts is None
            or ts >= last_ts + timedelta(seconds=NONOVERLAP_SPACING_S)
        ):
            selected.append(row)
            last_ts = ts
    return selected


def select_frozen_cohort(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected = deterministic_nonoverlap(rows)
    return selected[:TARGET_NEW_NONOVERLAP_N]


def _numeric_pressure_sum(pressures: dict[str, Any]) -> float:
    total = 0.0
    for value in pressures.values():
        if isinstance(value, (int, float)):
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ValueError("NULL_SEMANTICS_UNPROVEN:NONFINITE_PRESSURE")
            total += parsed
    return total


def effective_top2_component(
    *,
    component: str,
    audit: dict[str, Any],
) -> dict[str, Any]:
    if component not in TOP2_COMPONENT_FEATURE:
        raise ValueError(f"UNKNOWN_TOP2_COMPONENT:{component}")

    provenance = _runtime_provenance(audit)
    if (
        not isinstance(provenance, dict)
        or provenance.get("exact") is not True
        or provenance.get("source_commit") != REFERENCE_CORE_HEAD
    ):
        raise ValueError("NULL_SEMANTICS_UNPROVEN:RUNTIME_PROVENANCE")

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
    persisted = pressures.get(component)

    if persisted is not None:
        parsed = float(persisted)
        if not math.isfinite(parsed):
            raise ValueError("NULL_SEMANTICS_UNPROVEN:NONFINITE")
        return {
            "effective_value": parsed,
            "persisted_value": parsed,
            "recovered_by_runtime_semantics": False,
        }

    feature = TOP2_COMPONENT_FEATURE[component]
    availability = step1.get("feature_availability_v1")
    state = (
        availability.get(feature)
        if isinstance(availability, dict)
        else None
    )
    if not isinstance(state, dict) or state.get("status") != "MISSING":
        raise ValueError("NULL_SEMANTICS_UNPROVEN:AVAILABILITY")
    try:
        fallback = float(state.get("fallback_value"))
    except (TypeError, ValueError) as exc:
        raise ValueError("NULL_SEMANTICS_UNPROVEN:FALLBACK") from exc
    if fallback != 0.0:
        raise ValueError("NULL_SEMANTICS_UNPROVEN:FALLBACK")

    mask = step2.get("missing_input_mask_v1")
    masked = mask.get("masked_features") if isinstance(mask, dict) else None
    if not isinstance(masked, list) or feature not in masked:
        raise ValueError("NULL_SEMANTICS_UNPROVEN:MASK")

    total_pressure = step2.get("total_pressure")
    if total_pressure is None:
        raise ValueError("NULL_SEMANTICS_UNPROVEN:TOTAL")
    total = float(total_pressure)
    if not math.isfinite(total):
        raise ValueError("NULL_SEMANTICS_UNPROVEN:TOTAL")

    numeric_sum = _numeric_pressure_sum(pressures)
    if abs(total - numeric_sum) > ALGEBRA_TOLERANCE:
        raise ValueError("NULL_SEMANTICS_UNPROVEN:ALGEBRA")

    return {
        "effective_value": 0.0,
        "persisted_value": None,
        "recovered_by_runtime_semantics": True,
        "feature": feature,
        "status": state.get("status"),
        "fallback_value": fallback,
        "numeric_pressures_sum": numeric_sum,
        "total_pressure": total,
    }


def frozen_top2_scores(audit: dict[str, Any]) -> dict[str, Any]:
    pipeline = audit.get("pipeline")
    step2 = pipeline.get("step2_features") if isinstance(pipeline, dict) else None
    if not isinstance(step2, dict):
        raise ValueError("STEP2_FEATURES_MISSING")
    total = step2.get("total_pressure")
    if total is None:
        raise ValueError("TOTAL_PRESSURE_MISSING")
    full = float(total)
    if not math.isfinite(full):
        raise ValueError("TOTAL_PRESSURE_INVALID")

    parts = {
        component: effective_top2_component(component=component, audit=audit)
        for component in ("orderflow", "funding", "oi")
    }
    top2 = sum(float(parts[name]["effective_value"]) for name in parts)
    return {
        "full_score": full,
        "top2_pressure": top2,
        "no_top2_score": full - top2,
        "micro_only_score": top2,
        "components": parts,
    }


def _average_ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(
        enumerate(float(value) for value in values),
        key=lambda item: item[1],
    )
    ranks = [0.0] * len(indexed)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        average = (start + 1 + end) / 2.0
        for position in range(start, end):
            ranks[indexed[position][0]] = average
        start = end
    return ranks


def _rank_ic(pairs: Sequence[tuple[float, int]]) -> float | None:
    if len(pairs) < 2:
        return None
    scores = [float(score) for score, _ in pairs]
    labels = [float(label) for _, label in pairs]
    score_ranks = _average_ranks(scores)
    label_ranks = _average_ranks(labels)
    sm = sum(score_ranks) / len(score_ranks)
    lm = sum(label_ranks) / len(label_ranks)
    numerator = sum(
        (s - sm) * (l - lm)
        for s, l in zip(score_ranks, label_ranks)
    )
    ssum = sum((s - sm) ** 2 for s in score_ranks)
    lsum = sum((l - lm) ** 2 for l in label_ranks)
    if ssum <= 0 or lsum <= 0:
        return None
    return numerator / math.sqrt(ssum * lsum)


def _percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    x = (len(ordered) - 1) * p
    lo = int(math.floor(x))
    hi = int(math.ceil(x))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - x) + ordered[hi] * (x - lo)


def final_analysis(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    material = list(rows)
    if len(material) != TARGET_NEW_NONOVERLAP_N:
        raise ValueError(
            f"NO_EARLY_LOOK:REQUIRES_EXACT_N_{TARGET_NEW_NONOVERLAP_N}:"
            f"GOT_{len(material)}"
        )

    # Lazy import is deliberately after the N=96 hard gate.
    from edge_lab.arq2_score_readback_004 import roc_auc

    required = (
        "full_score",
        "no_top2_score",
        "micro_only_score",
        "top2_pressure",
        "y_up",
    )
    for row in material:
        missing = [key for key in required if row.get(key) is None]
        if missing:
            raise ValueError(f"FINAL_ROW_INCOMPLETE:{missing}")

    full_pairs = [
        (float(row["full_score"]), int(row["y_up"]))
        for row in material
    ]
    no_top2_pairs = [
        (float(row["no_top2_score"]), int(row["y_up"]))
        for row in material
    ]
    micro_pairs = [
        (float(row["micro_only_score"]), int(row["y_up"]))
        for row in material
    ]
    auc_full = roc_auc(full_pairs)
    auc_no_top2 = roc_auc(no_top2_pairs)
    auc_micro = roc_auc(micro_pairs)
    if auc_full is None or auc_no_top2 is None:
        raise ValueError("PRIMARY_AUC_UNDEFINED")

    rng = random.Random(BOOTSTRAP_SEED)
    delta_samples: list[float] = []
    full_samples: list[float] = []
    for _ in range(REPLICATES):
        indices = [rng.randrange(len(material)) for _ in range(len(material))]
        labels = {int(material[index]["y_up"]) for index in indices}
        if len(labels) < 2:
            continue
        full_sample = [
            (
                float(material[index]["full_score"]),
                int(material[index]["y_up"]),
            )
            for index in indices
        ]
        no_top2_sample = [
            (
                float(material[index]["no_top2_score"]),
                int(material[index]["y_up"]),
            )
            for index in indices
        ]
        boot_full = roc_auc(full_sample)
        boot_no_top2 = roc_auc(no_top2_sample)
        if boot_full is None or boot_no_top2 is None:
            continue
        full_samples.append(float(boot_full))
        delta_samples.append(float(boot_full - boot_no_top2))

    delta_ci = [
        _percentile(delta_samples, 0.025),
        _percentile(delta_samples, 0.975),
    ]
    full_ci = [
        _percentile(full_samples, 0.025),
        _percentile(full_samples, 0.975),
    ]
    if any(value is None for value in delta_ci + full_ci):
        raise ValueError("BOOTSTRAP_CI_UNDEFINED")

    low, high = float(delta_ci[0]), float(delta_ci[1])
    if low > 0:
        verdict = "POSITIVE_CONTRIBUTION"
    elif high < 0:
        verdict = "NEGATIVE_CONTRIBUTION"
    else:
        verdict = "INCONCLUSIVE"

    full_rank_ic = _rank_ic(full_pairs)
    micro_rank_ic = _rank_ic(micro_pairs)
    sign_agreement = sum(
        1
        for row in material
        if (
            (float(row["top2_pressure"]) > 0)
            == (float(row["full_score"]) > 0)
        )
    ) / len(material)
    sign_flip = sum(
        1
        for row in material
        if (
            (float(row["full_score"]) > 0)
            != (float(row["no_top2_score"]) > 0)
        )
    ) / len(material)

    directional_rows = [
        row
        for row in material
        if str(row.get("step2_direction") or "").upper()
        in {"LONG", "SHORT"}
    ]
    directional_correct = sum(
        1
        for row in directional_rows
        if (
            str(row["step2_direction"]).upper() == "LONG"
            and int(row["y_up"]) == 1
        )
        or (
            str(row["step2_direction"]).upper() == "SHORT"
            and int(row["y_up"]) == 0
        )
    )
    raw_direction_accuracy = (
        directional_correct / len(directional_rows)
        if directional_rows
        else None
    )

    prospective_delta = float(auc_full - auc_no_top2)
    return {
        "status": "COMPLETE",
        "new_nonoverlap_n": len(material),
        "auc_full": float(auc_full),
        "auc_full_ci95": [float(full_ci[0]), float(full_ci[1])],
        "auc_no_top2": float(auc_no_top2),
        "delta_auc_top2": prospective_delta,
        "delta_auc_ci95": [low, high],
        "bootstrap_requested_replicates": REPLICATES,
        "bootstrap_valid_replicates": len(delta_samples),
        "bootstrap_seed": BOOTSTRAP_SEED,
        "paired_indices": True,
        "auc_micro_only": auc_micro,
        "rank_ic_full": full_rank_ic,
        "rank_ic_micro_only": micro_rank_ic,
        "top2_sign_agreement_rate": sign_agreement,
        "top2_sign_flip_rate": sign_flip,
        "raw_direction_accuracy": raw_direction_accuracy,
        "retro_delta_auc_010": RETRO_DELTA_AUC_010,
        "prospective_delta_auc_011": prospective_delta,
        "delta_direction_consistent": (
            (RETRO_DELTA_AUC_010 > 0 and prospective_delta > 0)
            or (RETRO_DELTA_AUC_010 < 0 and prospective_delta < 0)
            or (RETRO_DELTA_AUC_010 == 0 and prospective_delta == 0)
        ),
        "top2_prospective_verdict": verdict,
    }


def rows_artifact_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()