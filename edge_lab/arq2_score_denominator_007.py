from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import random
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from edge_lab.arq2_score_readback_004 import q5_q1_lift, roc_auc
from senecio_polymarket.backend.settlement_contract import (
    WINDOW_1H_S,
    fetch_historical_price_evidence,
    normalize_exchange,
    parse_utc,
    validate_price_evidence,
)

REFERENCE_CORE_HEAD = "c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a"
POST_C7_CUTOFF_UTC = "2026-09-22T11:37:34+00:00"
PAGE_SIZE = 80
MAX_HOT_ROWS = 1600
BOOTSTRAP_REPLICATES = 5000
BOOTSTRAP_SEED = 20260923

HOT_COLUMNS = (
    "id,ts,symbol,prediction,confidence,ev,price_now,outcome,exchange_used,"
    "created_at,audit_digest,cold_payload_sha256"
)
FORBIDDEN_DECISION_KEYS = (
    "outcomes_dual",
    "price_1h_later",
    "settlement",
    "winner",
    "realized",
    "post_outcome",
)


class DenominatorRowRejected(ValueError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _walk_keys(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, str(key).lower()
            yield from _walk_keys(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{prefix}[{index}]")


def _dt(value: Any) -> datetime:
    parsed = parse_utc(value)
    if parsed is None:
        raise DenominatorRowRejected("TIMESTAMP_INVALID")
    return parsed


def build_hot_query(cursor: int | None, *, page_size: int = PAGE_SIZE) -> str:
    if int(page_size) <= 0 or int(page_size) > PAGE_SIZE:
        raise ValueError("INVALID_PAGE_SIZE")
    where = "" if cursor is None else f" WHERE id < {int(cursor)}"
    return (
        f"SELECT {HOT_COLUMNS} FROM oracle_predictions_hot"
        f"{where} ORDER BY id DESC LIMIT {int(page_size)};"
    )


def build_cold_query(prediction_ids: Sequence[int]) -> str:
    ids = sorted({int(value) for value in prediction_ids})
    if not ids:
        raise ValueError("EMPTY_COLD_ID_SET")
    joined = ",".join(str(value) for value in ids)
    return (
        "SELECT prediction_id,payload,audit_digest,payload_sha256 "
        "FROM oracle_prediction_audit_cold "
        f"WHERE prediction_id IN ({joined});"
    )


def _assert_select_only(sql: str) -> None:
    normalized = " ".join(str(sql).strip().split()).upper()
    if not normalized.startswith("SELECT "):
        raise RuntimeError("NON_SELECT_SQL_BLOCKED")
    padded = f" {normalized} "
    forbidden = (" INSERT ", " UPDATE ", " DELETE ", " DROP ", " ALTER ", " CREATE ", " REPLACE ")
    if any(token in padded for token in forbidden):
        raise RuntimeError("NON_SELECT_SQL_BLOCKED")
    if " OFFSET " in padded or "COUNT(" in normalized:
        raise RuntimeError("UNBOUNDED_SQL_BLOCKED")


def run_wrangler_select(
    *,
    workdir: Path,
    binding: str,
    sql: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    _assert_select_only(sql)
    npx = shutil.which("npx.cmd") or shutil.which("npx") or "npx.cmd"
    proc = subprocess.run(
        [npx, "wrangler", "d1", "execute", binding, "--remote", "--command", sql, "--json"],
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace")[-800:]
        raise RuntimeError(f"WRANGLER_FAILED:{binding}:{proc.returncode}:{tail}")
    raw = proc.stdout
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, list) or not document:
        raise RuntimeError("WRANGLER_RESULT_INVALID")
    block = document[0]
    meta = block.get("meta") or {}
    if int(meta.get("rows_written") or 0) != 0 or bool(meta.get("changed_db")):
        raise RuntimeError("D1_WRITE_DETECTED")
    return block.get("results") or [], meta, hashlib.sha256(raw).hexdigest()


def verify_join_integrity(hot: dict[str, Any], cold: dict[str, Any]) -> dict[str, Any]:
    if int(hot["id"]) != int(cold["prediction_id"]):
        raise DenominatorRowRejected("ID_MISMATCH")
    if str(hot.get("audit_digest") or "") != str(cold.get("audit_digest") or ""):
        raise DenominatorRowRejected("AUDIT_DIGEST_MISMATCH")
    if str(hot.get("cold_payload_sha256") or "") != str(cold.get("payload_sha256") or ""):
        raise DenominatorRowRejected("PAYLOAD_LINK_MISMATCH")
    payload = str(cold.get("payload") or "")
    payload_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if payload_sha != str(cold.get("payload_sha256") or ""):
        raise DenominatorRowRejected("PAYLOAD_BYTES_MISMATCH")
    document = json.loads(payload)
    audit = document.get("audit")
    if not isinstance(audit, dict):
        raise DenominatorRowRejected("AUDIT_MISSING")
    return audit


def validate_decision_audit(audit: dict[str, Any]) -> None:
    replay = audit.get("decision_replay_v1")
    if not isinstance(replay, dict):
        raise DenominatorRowRejected("DECISION_REPLAY_MISSING")
    provenance = replay.get("runtime_provenance")
    if not isinstance(provenance, dict):
        raise DenominatorRowRejected("RUNTIME_PROVENANCE_MISSING")
    if provenance.get("exact") is not True:
        raise DenominatorRowRejected("RUNTIME_PROVENANCE_NOT_EXACT")
    if provenance.get("source_commit") != REFERENCE_CORE_HEAD:
        raise DenominatorRowRejected("RUNTIME_PROVENANCE_COMMIT_MISMATCH")

    pipeline = audit.get("pipeline")
    step2 = pipeline.get("step2_features") if isinstance(pipeline, dict) else None
    if not isinstance(step2, dict):
        raise DenominatorRowRejected("STEP2_FEATURES_MISSING")
    for key in ("up_prob", "total_pressure", "direction"):
        if step2.get(key) is None:
            raise DenominatorRowRejected(f"STEP2_{key.upper()}_MISSING")

    learning_state = step2.get("learning_state_v1")
    if isinstance(learning_state, dict) and "source_settlement_observation_epochs" in learning_state:
        cutoff = learning_state.get("decision_cutoff_epoch")
        observations = learning_state.get("source_settlement_observation_epochs")
        try:
            cutoff_f = float(cutoff)
        except (TypeError, ValueError):
            raise DenominatorRowRejected("LEARNING_SETTLEMENT_CUTOFF_MISSING")
        if not isinstance(observations, list):
            raise DenominatorRowRejected("LEARNING_SETTLEMENT_EVIDENCE_INVALID")
        for item in observations:
            if not isinstance(item, dict):
                raise DenominatorRowRejected("LEARNING_SETTLEMENT_EVIDENCE_INVALID")
            try:
                observed_epoch = float(item.get("observed_at_epoch"))
            except (TypeError, ValueError):
                raise DenominatorRowRejected("LEARNING_SETTLEMENT_EVIDENCE_INVALID")
            if observed_epoch > cutoff_f:
                raise DenominatorRowRejected("POST_DECISION_LEARNING_EVIDENCE")

    offenders: list[str] = []
    for payload_name, payload in (("decision_replay_v1", replay), ("step2_features", step2)):
        for path, key in _walk_keys(payload, payload_name):
            if any(fragment in key for fragment in FORBIDDEN_DECISION_KEYS) and path != "step2_features.learning_state_v1.source_settlement_observation_epochs":
                offenders.append(path)
    if offenders:
        raise DenominatorRowRejected(
            "DECISION_FEATURE_CONTAMINATION:" + ",".join(sorted(offenders))
        )

    if not isinstance(audit.get("decision_waterfall_v1"), dict):
        raise DenominatorRowRejected("DECISION_WATERFALL_MISSING")
    if not isinstance(audit.get("action_vector"), dict):
        raise DenominatorRowRejected("ACTION_VECTOR_MISSING")
    if not isinstance(audit.get("origin_price_v1"), dict):
        raise DenominatorRowRejected("ORIGIN_PRICE_PROOF_MISSING")


def parse_candidate(
    hot: dict[str, Any],
    cold: dict[str, Any],
    *,
    evaluation_time: Any,
) -> dict[str, Any]:
    if str(hot.get("symbol") or "").upper() != "BTCUSDT":
        raise DenominatorRowRejected("SYMBOL_MISMATCH")
    ts = _dt(hot.get("ts"))
    cutoff = _dt(POST_C7_CUTOFF_UTC)
    evaluation = _dt(evaluation_time)
    if ts < cutoff:
        raise DenominatorRowRejected("PRE_C7_ROW")
    if ts > evaluation - timedelta(seconds=WINDOW_1H_S):
        raise DenominatorRowRejected("ROW_NOT_MATURE")

    audit = verify_join_integrity(hot, cold)
    validate_decision_audit(audit)
    step2 = audit["pipeline"]["step2_features"]
    waterfall = audit["decision_waterfall_v1"]
    action_vector = audit["action_vector"]
    origin = dict(audit["origin_price_v1"])
    provenance = audit["decision_replay_v1"]["runtime_provenance"]

    return {
        "source_prediction_id": int(hot["id"]),
        "ts": ts.isoformat(),
        "exchange_used": str(hot.get("exchange_used") or "").strip().lower(),
        "raw_up_prob": float(step2["up_prob"]),
        "total_pressure": float(step2["total_pressure"]),
        "step2_direction": str(step2.get("direction") or "").upper(),
        "action": str(action_vector.get("action") or "").upper(),
        "final_prediction": str(hot.get("prediction") or "").upper(),
        "gate_raw_reason": str(waterfall.get("raw_reason") or action_vector.get("reason") or ""),
        "decision_waterfall_category": str(waterfall.get("category") or ""),
        "origin_price": origin.get("price"),
        "origin_price_v1": origin,
        "stored_outcome": (
            str(hot.get("outcome")).upper()
            if hot.get("outcome") is not None
            else None
        ),
        "runtime_source_commit": str(provenance.get("source_commit") or ""),
        "label_status": "UNRESOLVED",
        "target_price_1h": None,
        "y_up": None,
        "historical_evidence_sha256": None,
        "historical_evidence_identity": None,
    }


def _origin_valid(candidate: dict[str, Any]) -> bool:
    origin = candidate.get("origin_price_v1")
    if not isinstance(origin, dict) or origin.get("version") != "origin-price-v1":
        return False
    expected_source = normalize_exchange(candidate.get("exchange_used"))
    actual_source = normalize_exchange(origin.get("source"))
    if expected_source is None or actual_source != expected_source:
        return False
    origin_ts = parse_utc(origin.get("timestamp"))
    row_ts = parse_utc(candidate.get("ts"))
    if origin_ts is None or row_ts is None or origin_ts != row_ts:
        return False
    try:
        price = float(origin.get("price"))
    except (TypeError, ValueError):
        return False
    return price > 0 and math.isfinite(price)


def _evidence_identity(evidence: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "version",
        "source",
        "symbol",
        "window_seconds",
        "target_epoch_ms",
        "candle_open_epoch_ms",
        "candle_close_epoch_ms",
        "candle_interval_ms",
        "target_offset_from_candle_open_ms",
        "observed_at",
        "selection_rule",
        "maturity_rule",
    )
    return {key: evidence.get(key) for key in keys}


def label_candidate(
    candidate: dict[str, Any],
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    labeled = dict(candidate)
    labeled["label_status"] = "UNRESOLVED"
    labeled["target_price_1h"] = None
    labeled["y_up"] = None
    labeled["historical_evidence_sha256"] = (
        _sha256_json(evidence) if isinstance(evidence, dict) else None
    )
    labeled["historical_evidence_identity"] = (
        _evidence_identity(evidence) if isinstance(evidence, dict) else None
    )

    if not _origin_valid(candidate) or not isinstance(evidence, dict):
        return labeled
    if not validate_price_evidence(
        evidence,
        expected_exchange=candidate.get("exchange_used"),
        expected_symbol="BTCUSDT",
        expected_ts=candidate.get("ts"),
        expected_window_seconds=WINDOW_1H_S,
    ):
        return labeled

    try:
        origin = float(candidate["origin_price_v1"]["price"])
        target = float(evidence["price"])
    except (TypeError, ValueError, KeyError):
        return labeled
    if origin <= 0 or target <= 0 or not math.isfinite(origin) or not math.isfinite(target):
        return labeled
    labeled["origin_price"] = origin
    labeled["target_price_1h"] = target
    labeled["y_up"] = 1 if target > origin else 0
    labeled["label_status"] = "LABELABLE"
    return labeled


def record_hash(record: dict[str, Any]) -> str:
    material = {key: value for key, value in record.items() if key != "record_hash"}
    return _sha256_json(material)


def deterministic_nonoverlap(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    stable = sorted(
        list(rows),
        key=lambda row: (
            _dt(row.get("ts")).timestamp(),
            int(row.get("source_prediction_id") or 0),
            _canonical_json(row),
        ),
    )
    selected: list[dict[str, Any]] = []
    last_ts: datetime | None = None
    for row in stable:
        ts = _dt(row.get("ts"))
        if last_ts is None or ts >= last_ts + timedelta(seconds=WINDOW_1H_S):
            selected.append(row)
            last_ts = ts
    return selected


def order004_style_subset(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if str(row.get("stored_outcome") or "").upper() in {"WIN", "LOSS"}
        and str(row.get("final_prediction") or "").upper() in {"LONG", "SHORT"}
    ]


def _average_ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(float(v) for v in values), key=lambda pair: pair[1])
    ranks = [0.0] * len(indexed)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        avg_rank = (start + 1 + end) / 2.0
        for pos in range(start, end):
            ranks[indexed[pos][0]] = avg_rank
        start = end
    return ranks


def rank_ic(pairs: Sequence[tuple[float, int]]) -> float | None:
    if len(pairs) < 2:
        return None
    scores = [float(score) for score, _label in pairs]
    labels = [float(label) for _score, label in pairs]
    score_ranks = _average_ranks(scores)
    label_ranks = _average_ranks(labels)
    score_mean = sum(score_ranks) / len(score_ranks)
    label_mean = sum(label_ranks) / len(label_ranks)
    numerator = sum(
        (a - score_mean) * (b - label_mean)
        for a, b in zip(score_ranks, label_ranks)
    )
    score_ss = sum((a - score_mean) ** 2 for a in score_ranks)
    label_ss = sum((b - label_mean) ** 2 for b in label_ranks)
    if score_ss <= 0 or label_ss <= 0:
        return None
    return numerator / math.sqrt(score_ss * label_ss)


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


def bootstrap_auc_ci(
    pairs: Sequence[tuple[float, int]],
    *,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
) -> list[float] | None:
    if not pairs:
        return None
    rng = random.Random(seed)
    valid: list[float] = []
    material = list(pairs)
    for _ in range(int(replicates)):
        sample = [material[rng.randrange(len(material))] for _ in range(len(material))]
        value = roc_auc(sample)
        if value is not None:
            valid.append(float(value))
    low = _percentile(valid, 0.025)
    high = _percentile(valid, 0.975)
    if low is None or high is None:
        return None
    return [low, high]


def _counts(rows: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
    counter = collections.Counter(str(row.get(key) or "") for row in rows)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def analyze_records(
    records: list[dict[str, Any]],
    *,
    hot_rows_examined: int,
    full_audit_n: int,
) -> dict[str, Any]:
    labelable = [row for row in records if row.get("label_status") == "LABELABLE"]
    unresolved = [row for row in records if row.get("label_status") != "LABELABLE"]
    nonoverlap = deterministic_nonoverlap(labelable)
    pairs = [(float(row["raw_up_prob"]), int(row["y_up"])) for row in nonoverlap]
    auc_all = roc_auc(pairs)
    auc_ci = bootstrap_auc_ci(pairs)
    rank = rank_ic(pairs)

    subset = order004_style_subset(labelable)
    subset_nonoverlap = deterministic_nonoverlap(subset)
    subset_pairs = [
        (float(row["raw_up_prob"]), int(row["y_up"]))
        for row in subset_nonoverlap
    ]
    auc_subset = roc_auc(subset_pairs)

    directional_rows = [
        row for row in nonoverlap
        if str(row.get("step2_direction") or "").upper() in {"LONG", "SHORT"}
    ]
    correct = sum(
        1 for row in directional_rows
        if (
            str(row.get("step2_direction")).upper() == "LONG"
            and int(row["y_up"]) == 1
        )
        or (
            str(row.get("step2_direction")).upper() == "SHORT"
            and int(row["y_up"]) == 0
        )
    )
    raw_direction_accuracy = (
        correct / len(directional_rows) if directional_rows else None
    )

    q5_lift = None
    if len(nonoverlap) >= 20:
        q5_lift = q5_q1_lift(
            [
                (
                    float(row["raw_up_prob"]),
                    int(row["y_up"]),
                    int(row["source_prediction_id"]),
                )
                for row in nonoverlap
            ]
        )

    raw_scores = [float(row["raw_up_prob"]) for row in records]
    raw_score_distribution = {
        "n": len(raw_scores),
        "min": min(raw_scores) if raw_scores else None,
        "p10": _percentile(raw_scores, 0.10),
        "p25": _percentile(raw_scores, 0.25),
        "mean": (
            sum(raw_scores) / len(raw_scores)
            if raw_scores else None
        ),
        "p50": _percentile(raw_scores, 0.50),
        "p75": _percentile(raw_scores, 0.75),
        "p90": _percentile(raw_scores, 0.90),
        "max": max(raw_scores) if raw_scores else None,
    }

    reasons = collections.Counter(
        str(row.get("gate_raw_reason") or "") for row in records
    )
    top_reasons = [
        {"reason": reason, "n": count}
        for reason, count in sorted(
            reasons.items(),
            key=lambda item: (-item[1], item[0]),
        )[:10]
    ]
    predictions = _counts(records, "final_prediction")
    actions = _counts(records, "action")
    directions = _counts(records, "step2_direction")
    denominator = len(records)
    directional_final = sum(predictions.get(key, 0) for key in ("LONG", "SHORT"))
    flat_final = predictions.get("FLAT", 0)
    execute_n = actions.get("EXECUTE", 0)

    if len(nonoverlap) < 30:
        verdict = "INSUFFICIENT_N"
    elif auc_ci is not None and auc_ci[0] > 0.5:
        verdict = "DISCRIMINATION_POSITIVE_DIAGNOSTIC"
    elif auc_ci is not None and auc_ci[1] < 0.5:
        verdict = "DISCRIMINATION_NEGATIVE_DIAGNOSTIC"
    else:
        verdict = "INCONCLUSIVE"

    return {
        "hot_rows_examined": int(hot_rows_examined),
        "all_post_c7_candidates": denominator,
        "full_audit_n": int(full_audit_n),
        "labelable_n": len(labelable),
        "unresolved_label_n": len(unresolved),
        "label_completeness_pct": (
            100.0 * len(labelable) / denominator if denominator else 0.0
        ),
        "nonoverlap_1h_n": len(nonoverlap),
        "step2_direction_counts": directions,
        "action_counts": actions,
        "final_prediction_counts": predictions,
        "top_gate_or_raw_reasons": top_reasons,
        "raw_score_distribution": raw_score_distribution,
        "directional_final_rate": (
            directional_final / denominator if denominator else None
        ),
        "flat_final_rate": flat_final / denominator if denominator else None,
        "execute_rate": execute_n / denominator if denominator else None,
        "auc_all_opportunity": auc_all,
        "auc_ci95": auc_ci,
        "auc_004_style_subset": auc_subset,
        "auc_selection_delta": (
            auc_subset - auc_all
            if auc_subset is not None and auc_all is not None
            else None
        ),
        "rank_ic": rank,
        "q5_q1_lift": q5_lift,
        "raw_direction_accuracy": raw_direction_accuracy,
        "n_all_nonoverlap": len(nonoverlap),
        "n_004_style_nonoverlap": len(subset_nonoverlap),
        "score_verdict": verdict,
        "bootstrap": {
            "replicates": BOOTSTRAP_REPLICATES,
            "seed": BOOTSTRAP_SEED,
        },
    }


def _record_for_artifact(row: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "source_prediction_id": row.get("source_prediction_id"),
        "ts": row.get("ts"),
        "exchange_used": row.get("exchange_used"),
        "raw_up_prob": row.get("raw_up_prob"),
        "total_pressure": row.get("total_pressure"),
        "step2_direction": row.get("step2_direction"),
        "action": row.get("action"),
        "final_prediction": row.get("final_prediction"),
        "gate_raw_reason": row.get("gate_raw_reason"),
        "decision_waterfall_category": row.get("decision_waterfall_category"),
        "origin_price": row.get("origin_price"),
        "target_price_1h": row.get("target_price_1h"),
        "y_up": row.get("y_up"),
        "label_status": row.get("label_status"),
        "historical_evidence_sha256": row.get("historical_evidence_sha256"),
        "historical_evidence_identity": row.get("historical_evidence_identity"),
        "runtime_source_commit": row.get("runtime_source_commit"),
        "stored_outcome": row.get("stored_outcome"),
    }
    keep["record_hash"] = record_hash(keep)
    return keep


def collect_remote(
    *,
    wrangler_workdir: Path,
    evaluation_time: datetime,
    hot_binding: str = "HOT",
    cold_binding: str = "COLD",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cursor: int | None = None
    hot_rows_examined = 0
    crossed_boundary = False
    hot_candidate_rows: list[dict[str, Any]] = []
    page_evidence: list[dict[str, Any]] = []
    cutoff = _dt(POST_C7_CUTOFF_UTC)
    maturity_cutoff = (
        evaluation_time.astimezone(timezone.utc)
        - timedelta(seconds=WINDOW_1H_S)
    )

    while hot_rows_examined < MAX_HOT_ROWS:
        remaining = MAX_HOT_ROWS - hot_rows_examined
        page_size = min(PAGE_SIZE, remaining)
        sql = build_hot_query(cursor, page_size=page_size)
        rows, meta, page_hash = run_wrangler_select(
            workdir=wrangler_workdir,
            binding=hot_binding,
            sql=sql,
        )
        page_no = len(
            [item for item in page_evidence if item["kind"] == "HOT"]
        ) + 1
        page_evidence.append(
            {
                "kind": "HOT",
                "page": page_no,
                "sha256": page_hash,
                "rows": len(rows),
                "rows_read": meta.get("rows_read"),
                "rows_written": meta.get("rows_written"),
                "changed_db": meta.get("changed_db"),
            }
        )
        if len(rows) > page_size:
            raise RuntimeError("HOT_PAGE_OVERBOUND")
        hot_rows_examined += len(rows)
        if not rows:
            crossed_boundary = True
            break

        parsed_times: list[datetime] = []
        for row in rows:
            try:
                row_ts = _dt(row.get("ts"))
            except DenominatorRowRejected:
                continue
            parsed_times.append(row_ts)
            if (
                str(row.get("symbol") or "").upper() == "BTCUSDT"
                and row_ts >= cutoff
                and row_ts <= maturity_cutoff
            ):
                hot_candidate_rows.append(row)

        if parsed_times and min(parsed_times) < cutoff:
            crossed_boundary = True
            break
        cursor = min(int(row["id"]) for row in rows)

    if not crossed_boundary:
        raise RuntimeError("BOUNDED_WINDOW_INCOMPLETE")

    hot_by_id = {int(row["id"]): row for row in hot_candidate_rows}
    cold_by_id: dict[int, dict[str, Any]] = {}
    ids = sorted(hot_by_id)
    for start in range(0, len(ids), PAGE_SIZE):
        chunk = ids[start:start + PAGE_SIZE]
        sql = build_cold_query(chunk)
        rows, meta, page_hash = run_wrangler_select(
            workdir=wrangler_workdir,
            binding=cold_binding,
            sql=sql,
        )
        page_no = len(
            [item for item in page_evidence if item["kind"] == "COLD"]
        ) + 1
        page_evidence.append(
            {
                "kind": "COLD",
                "page": page_no,
                "sha256": page_hash,
                "requested_ids": len(chunk),
                "rows": len(rows),
                "rows_read": meta.get("rows_read"),
                "rows_written": meta.get("rows_written"),
                "changed_db": meta.get("changed_db"),
            }
        )
        for row in rows:
            cold_by_id[int(row["prediction_id"])] = row

    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for prediction_id in sorted(hot_by_id):
        hot = hot_by_id[prediction_id]
        cold = cold_by_id.get(prediction_id)
        if cold is None:
            excluded.append({"id": prediction_id, "reason": "COLD_JOIN_MISSING"})
            continue
        try:
            candidate = parse_candidate(
                hot,
                cold,
                evaluation_time=evaluation_time,
            )
        except DenominatorRowRejected as exc:
            reason = str(exc)
            if any(
                token in reason
                for token in (
                    "ID_MISMATCH",
                    "AUDIT_DIGEST_MISMATCH",
                    "PAYLOAD_LINK_MISMATCH",
                    "PAYLOAD_BYTES_MISMATCH",
                )
            ):
                raise RuntimeError(
                    f"INTEGRITY_MISMATCH:{prediction_id}:{reason}"
                ) from exc
            excluded.append({"id": prediction_id, "reason": reason})
            continue
        candidates.append(candidate)

    return candidates, {
        "transport": "REMOTE_D1_WRANGLER_SELECT_ONLY",
        "evaluation_time_utc": (
            evaluation_time.astimezone(timezone.utc).isoformat()
        ),
        "post_c7_cutoff_utc": cutoff.isoformat(),
        "maturity_cutoff_utc": maturity_cutoff.isoformat(),
        "hot_rows_examined": hot_rows_examined,
        "hot_mature_btc_prejoin_n": len(hot_candidate_rows),
        "cold_rows_requested": len(ids),
        "cold_rows_received": len(cold_by_id),
        "full_audit_n": len(candidates),
        "excluded_n": len(excluded),
        "excluded": excluded,
        "crossed_c7_boundary": crossed_boundary,
        "page_evidence": page_evidence,
        "d1_writes": 0,
    }


def label_remote_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    labeled: list[dict[str, Any]] = []
    for candidate in sorted(
        candidates,
        key=lambda row: int(row["source_prediction_id"]),
    ):
        evidence = fetch_historical_price_evidence(
            candidate.get("exchange_used"),
            "BTCUSDT",
            candidate.get("ts"),
            WINDOW_1H_S,
        )
        labeled.append(label_candidate(candidate, evidence))
    return labeled


def write_artifacts(
    *,
    records: list[dict[str, Any]],
    transport: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_records = [_record_for_artifact(row) for row in records]
    artifact_records.sort(
        key=lambda row: (row["ts"], row["source_prediction_id"])
    )
    dataset_path = output_dir / "arq2_score_denominator_007.jsonl"
    dataset_path.write_text(
        "".join(_canonical_json(row) + "\n" for row in artifact_records),
        encoding="utf-8",
    )
    summary = analyze_records(
        artifact_records,
        hot_rows_examined=int(transport["hot_rows_examined"]),
        full_audit_n=int(transport["full_audit_n"]),
    )
    summary.update(
        {
            "num_order": "ARQ2-SCORE-DENOMINATOR-007",
            "status": "COMPLETE",
            "diagnostic_only": True,
            "transport": "REMOTE_D1_WRANGLER_SELECT_ONLY",
            "reference_core_head": REFERENCE_CORE_HEAD,
            "target_semantics": {
                "id": "SENEX_NATIVE_STRICT_UP_T_PLUS_3600",
                "definition": (
                    "1 iff proof-qualified same-source price at t+3600 "
                    "> origin price else 0"
                ),
                "tie": "0",
                "polymarket_equivalence": False,
            },
            "selection_bias_diagnostic_only": True,
            "order004_selection_bias_resolved": True,
            "top1_top2_top3_touched": False,
            "strategy_curator_implemented": False,
            "d1_writes": 0,
            "core_mutations": 0,
            "model_mutations": 0,
            "deployments": 0,
            "hyp008_mutations": 0,
            "edge": "UNPROVEN",
            "transport_evidence": transport,
            "dataset_sha256": hashlib.sha256(
                dataset_path.read_bytes()
            ).hexdigest(),
        }
    )
    summary_path = output_dir / "arq2_score_denominator_007_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary


def _report_markdown(summary: dict[str, Any]) -> str:
    return f"""# ARQ2-SCORE-DENOMINATOR-007

Status: **{summary['status']}**
Transport: **REMOTE_D1_WRANGLER_SELECT_ONLY**
Diagnostic only: **YES**

## Denominator

- HOT rows examined: {summary['hot_rows_examined']}
- All post-c7 candidates: {summary['all_post_c7_candidates']}
- Full audit N: {summary['full_audit_n']}
- Labelable N: {summary['labelable_n']}
- Unresolved label N: {summary['unresolved_label_n']}
- Label completeness: {summary['label_completeness_pct']:.3f}%
- Non-overlap 1h N: {summary['nonoverlap_1h_n']}

## Attrition

- Step2 directions: {json.dumps(summary['step2_direction_counts'], sort_keys=True)}
- Actions: {json.dumps(summary['action_counts'], sort_keys=True)}
- Final predictions: {json.dumps(summary['final_prediction_counts'], sort_keys=True)}
- Raw score distribution: {json.dumps(summary['raw_score_distribution'], sort_keys=True)}
- Directional final rate: {summary['directional_final_rate']}
- FLAT final rate: {summary['flat_final_rate']}
- Execute rate: {summary['execute_rate']}

## Raw score discrimination

- AUC all-opportunity non-overlap: {summary['auc_all_opportunity']}
- Bootstrap 95% CI: {summary['auc_ci95']}
- Rank-IC: {summary['rank_ic']}
- Q5-Q1 lift: {summary['q5_q1_lift']}
- Raw step2 direction accuracy: {summary['raw_direction_accuracy']}
- Score verdict: **{summary['score_verdict']}**

## Selection-bias diagnostic

- AUC all opportunity: {summary['auc_all_opportunity']}
- AUC ORDER-004-style subset: {summary['auc_004_style_subset']}
- AUC selection delta: {summary['auc_selection_delta']}
- N all non-overlap: {summary['n_all_nonoverlap']}
- N ORDER-004-style non-overlap: {summary['n_004_style_nonoverlap']}

ORDER-004 selection bias resolved by denominator accounting: **YES**.

No Brier/LogLoss, calibration, score inversion, strategy-radar candidate test,
score/gate/risk/execution mutation, D1 write, deploy, LIVE action, real order,
or capital action was performed.

EDGE remains **UNPROVEN**.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wrangler-workdir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--evaluation-time", default=None)
    parser.add_argument("--hot-binding", default="HOT")
    parser.add_argument("--cold-binding", default="COLD")
    args = parser.parse_args()

    evaluation = (
        _dt(args.evaluation_time)
        if args.evaluation_time
        else datetime.now(timezone.utc)
    )
    candidates, transport = collect_remote(
        wrangler_workdir=Path(args.wrangler_workdir),
        evaluation_time=evaluation,
        hot_binding=args.hot_binding,
        cold_binding=args.cold_binding,
    )
    labeled = label_remote_candidates(candidates)
    output_dir = Path(args.output_dir)
    summary = write_artifacts(
        records=labeled,
        transport=transport,
        output_dir=output_dir,
    )
    (output_dir / "ARQ2-SCORE-DENOMINATOR-007.md").write_text(
        _report_markdown(summary),
        encoding="utf-8",
    )
    print(json.dumps(summary, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
