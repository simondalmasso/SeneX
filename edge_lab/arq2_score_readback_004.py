from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from senecio_polymarket.backend.authoritative_score import independent_1h_cohort

REFERENCE_CORE_HEAD = "c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a"
_FORBIDDEN_DECISION_KEYS = (
    "outcomes_dual", "price_1h_later", "settlement",
    "winner", "realized", "post_outcome",
)


class ReadbackRowRejected(ValueError):
    pass


def _walk_keys(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, str(key).lower()
            yield from _walk_keys(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{prefix}[{index}]")


def validate_analysis_audit(
    audit: dict[str, Any],
    *,
    reference_core_head: str = REFERENCE_CORE_HEAD,
) -> None:
    if not isinstance(audit, dict):
        raise ReadbackRowRejected("AUDIT_MISSING")
    replay = audit.get("decision_replay_v1")
    if not isinstance(replay, dict):
        raise ReadbackRowRejected("DECISION_REPLAY_MISSING")
    provenance = replay.get("runtime_provenance")
    if not isinstance(provenance, dict):
        raise ReadbackRowRejected("RUNTIME_PROVENANCE_MISSING")
    if provenance.get("exact") is not True:
        raise ReadbackRowRejected("RUNTIME_PROVENANCE_NOT_EXACT")
    if provenance.get("source_commit") != reference_core_head:
        raise ReadbackRowRejected("RUNTIME_PROVENANCE_COMMIT_MISMATCH")

    pipeline = audit.get("pipeline")
    step2 = pipeline.get("step2_features") if isinstance(pipeline, dict) else None
    if not isinstance(step2, dict):
        raise ReadbackRowRejected("STEP2_FEATURES_MISSING")
    if step2.get("total_pressure") is None or step2.get("up_prob") is None:
        raise ReadbackRowRejected("RAW_SCORE_MISSING")

    offenders = []
    for payload_name, payload in (("decision_replay_v1", replay), ("step2_features", step2)):
        for path, key in _walk_keys(payload, payload_name):
            if any(part in key for part in _FORBIDDEN_DECISION_KEYS):
                offenders.append(path)
    if offenders:
        raise ReadbackRowRejected(
            "DECISION_FEATURE_CONTAMINATION:" + ",".join(sorted(offenders))
        )

    if not isinstance(audit.get("decision_waterfall_v1"), dict):
        raise ReadbackRowRejected("DECISION_WATERFALL_MISSING")
    origin = audit.get("origin_price_v1")
    dual = audit.get("outcomes_dual")
    if not isinstance(origin, dict):
        raise ReadbackRowRejected("ORIGIN_PRICE_PROOF_MISSING")
    if not isinstance(dual, dict) or dual.get("price_1h_later") is None:
        raise ReadbackRowRejected("SETTLED_1H_MISSING")
    observation = dual.get("settlement_observation_v1")
    if not isinstance(observation, dict) or observation.get("observed_at") is None:
        raise ReadbackRowRejected("SETTLEMENT_PROVENANCE_MISSING")


def verify_join_integrity(hot: dict[str, Any], cold: dict[str, Any]) -> dict[str, Any]:
    if int(hot["id"]) != int(cold["prediction_id"]):
        raise ReadbackRowRejected("ID_MISMATCH")
    if str(hot.get("audit_digest") or "") != str(cold.get("audit_digest") or ""):
        raise ReadbackRowRejected("AUDIT_DIGEST_MISMATCH")
    if str(hot.get("cold_payload_sha256") or "") != str(cold.get("payload_sha256") or ""):
        raise ReadbackRowRejected("PAYLOAD_LINK_MISMATCH")
    payload = str(cold.get("payload") or "")
    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != str(cold.get("payload_sha256") or ""):
        raise ReadbackRowRejected("PAYLOAD_BYTES_MISMATCH")
    document = json.loads(payload)
    audit = document.get("audit")
    validate_analysis_audit(audit)
    return audit


def deterministic_nonoverlap(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return independent_1h_cohort(rows)


def roc_auc(pairs: list[tuple[float, int]]) -> float | None:
    pos = [float(score) for score, label in pairs if int(label) == 1]
    neg = [float(score) for score, label in pairs if int(label) == 0]
    if not pos or not neg:
        return None
    wins = sum(
        1.0 if p > n else 0.5 if p == n else 0.0
        for p in pos for n in neg
    )
    return wins / (len(pos) * len(neg))


def quintile_up_frequency(records: list[tuple[float, int, int]]) -> list[float | None]:
    ordered = sorted(records, key=lambda item: (float(item[0]), int(item[2])))
    n = len(ordered)
    out: list[float | None] = []
    start = 0
    for index in range(5):
        size = n // 5 + (1 if index < n % 5 else 0)
        group = ordered[start:start + size]
        out.append(
            sum(int(label) for _score, label, _id in group) / len(group)
            if group else None
        )
        start += size
    return out


def q5_q1_lift(records: list[tuple[float, int, int]]) -> float | None:
    rates = quintile_up_frequency(records)
    if rates[0] is None or rates[4] is None:
        return None
    return float(rates[4]) - float(rates[0])
