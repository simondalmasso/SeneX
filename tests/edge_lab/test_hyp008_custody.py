from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / "research" / "hyp008_custody_repair_a_c_d.json"
COHORT_PATH = ROOT / "research" / "hyp008_prospective_cohort.jsonl"
PROTOCOL_PATH = ROOT / "research" / "hyp008_prospective_protocol_v1.json"

EXPECTED_ROW_SHA256 = "a6e7384c7f94b0f4d5faa9d9754344e466b1f7e940184a1cea12fca8a7c0025b"


def _canonical_sha256(value: object) -> tuple[str, int]:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), len(payload)


def test_recovered_jev_schema_and_arm_reproduce_declared_hashes() -> None:
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    task = audit["task_a"]

    question_hash, question_bytes = _canonical_sha256(task["questions"])
    assert task["original_question_order"] == [
        "directional_quality",
        "execution_quality",
        "toxic_flow",
    ]
    assert question_hash == task["declared_question_schema_hash"]
    assert question_hash == "f689a24ba9613caaa725a91d23f21fe52a244e457c69d2e2233cffa8e44d7a1d"
    assert question_bytes == 1461

    arm_hash, arm_bytes = _canonical_sha256(task["arm_original"])
    assert arm_hash == task["declared_arm_hash"]
    assert arm_hash == "6bc5be515b0843793d1f7d698d154cb3867f32e745387ab9cfacff4bfb471686"
    assert arm_bytes == 2883
    assert task["arm_original"]["model_exact"] == "jev-1.13.0"
    assert task["arm_original"]["veto_rule"] == {
        "veto_if_any": [
            "directional_quality=adverse",
            "execution_quality=poor",
            "toxic_flow=present",
        ],
        "no_probability_thresholds": True,
        "unclear_is_veto": False,
    }


def test_observation_1_is_byte_exact_and_protocol_inadmissible() -> None:
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    row_bytes = COHORT_PATH.read_bytes()
    assert hashlib.sha256(row_bytes).hexdigest() == EXPECTED_ROW_SHA256
    assert len(row_bytes) == 1424

    row = json.loads(row_bytes)
    classification = audit["task_d"]
    assert row["opportunity_id"] == classification["observation_id"]
    assert row["eligibility"] is True
    assert classification["recorded_eligibility_true"] is True
    assert classification["protocol_admissible"] is False
    assert classification["reason"] == "EXACT_BOUNDARY_CAPTURE_VIOLATION"
    assert row["eventStartTime"] == classification["eventStartTime"]
    assert row["decision_capture_timestamp"] == classification["decision_capture_timestamp"]

    start = datetime.fromisoformat(row["eventStartTime"].replace("Z", "+00:00"))
    captured = datetime.fromisoformat(row["decision_capture_timestamp"].replace("Z", "+00:00"))
    delta_ms = (captured - start).total_seconds() * 1000
    assert delta_ms == classification["boundary_delta_ms"] == 150407.828

    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))["protocol"]
    eligibility = protocol["eligibility"]
    assert eligibility["decision_state_asof_boundary_required"] is True
    assert eligibility["retrospective_backfill"] is False


def test_original_row_writer_is_not_fabricated() -> None:
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    task = audit["task_c"]
    assert task["status"] == "BLOCKED"
    assert task["reason"] == "ORIGINAL_HYP008_WRITER_NOT_REPRODUCIBLE"
    assert task["exact_repository_writer_program"] is None
    assert task["reproducible"] is False
    assert task["no_reconstruction_from_memory"] is True
