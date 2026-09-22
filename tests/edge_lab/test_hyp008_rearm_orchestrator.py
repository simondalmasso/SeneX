from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "hyp008_rearm_orch_v1.json"
MANIFEST_PATH = ROOT / "research" / "hyp008_ready_for_rearm_v1.json"
ORCH_PATH = ROOT / "edge_lab" / "hyp008_rearm_orchestrator.py"
PROTOCOL_PATH = ROOT / "research" / "hyp008_prospective_protocol_v1.json"
COHORT_PATH = ROOT / "research" / "hyp008_prospective_cohort.jsonl"
OBS1_SHA256 = "a6e7384c7f94b0f4d5faa9d9754344e466b1f7e940184a1cea12fca8a7c0025b"
ARQ2_HEAD = "62e4140d742ec691a62f1ca609bb97362389505e"
H011_RUNTIME = "c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a"


def _module():
    assert ORCH_PATH.exists(), "ORCH_MODULE_MISSING"
    spec = importlib.util.spec_from_file_location("hyp008_rearm_orchestrator", ORCH_PATH)
    assert spec is not None and spec.loader is not None, "ORCH_MODULE_UNLOADABLE"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _run(fixture: dict | None = None, prior: set[str] | None = None):
    orch = _module()
    return orch.run_fixture_orchestration(
        fixture or _fixture(),
        protocol_path=PROTOCOL_PATH,
        prior_opportunity_ids=prior or set(),
    )


def _event(fixture: dict, gate: str) -> dict:
    return next(item for item in fixture["events"] if item["gate"] == gate)


def test_ready_manifest_is_frozen_but_not_authorized_before_gate_b() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["manifest_id"] == "HYP008_READY_FOR_REARM_V1"
    assert manifest["status"] == "PREPARED_AWAITING_ARQ1_GATE_B_PASS"
    assert manifest["ready_for_rearm"] is False
    assert manifest["gate_b_24h"] == "IN_PROGRESS"
    assert manifest["rearm_requires"] == "ARQ1_GATE_B_24H=PASS"
    assert manifest["authority_head"] == ARQ2_HEAD
    assert manifest["current_h011_runtime"] == H011_RUNTIME
    assert manifest["protocol_hash"] == "8f0a2125c44ffb8873cb4908a13e6a4a998bb93f903ec8cfe6d5ea97570fd44b"
    assert manifest["jev_arm_hash"] == "6bc5be515b0843793d1f7d698d154cb3867f32e745387ab9cfacff4bfb471686"
    gates = manifest["orchestration_contract"]["gates"]
    assert [(g["gate"], g["owner"]) for g in gates] == [
        ("H-15", "ARQ2"), ("H-10", "ARQ3"), ("H-05", "ARQ3"),
        ("H+00", "ARQ3"), ("H+03", "ARQ3"), ("H+05", "ARQ2"),
    ]
    assert manifest["dry_run_controls"]["cohort_append_permitted"] is False
    assert manifest["dry_run_controls"]["live_capture_permitted"] is False


def test_valid_fixture_reaches_ready_for_append_without_appending() -> None:
    result = _run()
    assert result["state"] == "READY_FOR_APPEND"
    assert result["cohort_appends"] == 0
    assert result["append_intent_count"] == 1
    assert result["selected_source_id"] == 7001
    assert result["candidate"] == "DOWN"


def test_current_h011_packet_is_compatible_without_arq1_arq2_sha_parity() -> None:
    fixture = _fixture()
    assert fixture["current_h011_runtime"] != fixture["authority_head"]
    source = _module().normalize_h011_source_packet(
        fixture["h011_packet"],
        boundary=fixture["boundary"],
    )
    assert source["id"] == 7001
    assert source["polymarket_directional_use"] is False
    assert source["polymarket_effective_weight"] == 0
    assert _run(fixture)["state"] == "READY_FOR_APPEND"


def test_handoff_after_h_minus_15_fails_closed() -> None:
    fixture = _fixture()
    _event(fixture, "H-15")["at"] = "2026-09-24T11:45:00.001Z"
    with pytest.raises(Exception, match="HANDOFF_LATE"):
        _run(fixture)


def test_missing_arq3_ack_fails_closed() -> None:
    fixture = _fixture()
    fixture["events"] = [e for e in fixture["events"] if e["gate"] != "H-10"]
    with pytest.raises(Exception, match="MISSING_GATE:H-10"):
        _run(fixture)


def test_late_arq3_ack_fails_closed() -> None:
    fixture = _fixture()
    _event(fixture, "H-10")["at"] = "2026-09-24T11:50:00.001Z"
    with pytest.raises(Exception, match="ACK_LATE"):
        _run(fixture)


@pytest.mark.parametrize("field", ["event_id", "market_id", "condition_id", "market_slug", "eventStartTime"])
def test_identity_mismatch_fails_closed(field: str) -> None:
    fixture = _fixture()
    _event(fixture, "H-10")["identity"][field] = "mismatch"
    with pytest.raises(Exception, match="IDENTITY_MISMATCH"):
        _run(fixture)


def test_token_mismatch_fails_closed() -> None:
    fixture = _fixture()
    _event(fixture, "H-05")["identity"]["down_token_id"] = "wrong-token"
    with pytest.raises(Exception, match="IDENTITY_MISMATCH"):
        _run(fixture)


def test_duplicate_opportunity_fails_closed() -> None:
    first = _run()
    with pytest.raises(Exception, match="DUPLICATE_OPPORTUNITY"):
        _run(prior={first["opportunity_id"]})


def test_exact_retry_is_idempotent_and_never_appends_twice() -> None:
    fixture = _fixture()
    c2 = copy.deepcopy(_event(fixture, "H+05"))
    fixture["events"].append(c2)
    result = _run(fixture)
    assert result["state"] == "READY_FOR_APPEND"
    assert result["append_intent_count"] == 1
    assert result["cohort_appends"] == 0
    assert result["idempotent_replays"] == 1


@pytest.mark.parametrize("key,code", [
    ("execution_evidence", "EXECUTION_EVIDENCE_MISSING"),
    ("jev_input_at_decision", "JEV_INPUT_MISSING"),
])
def test_missing_arq3_evidence_fails_closed(key: str, code: str) -> None:
    fixture = _fixture()
    fixture[key] = None
    with pytest.raises(Exception, match=code):
        _run(fixture)


def test_source_ts_after_boundary_fails_closed() -> None:
    fixture = _fixture()
    fixture["h011_packet"]["predictions"][1]["ts"] = "2026-09-24T12:00:00.001Z"
    fixture["h011_packet"]["predictions"][0]["ts"] = "2026-09-24T12:00:00.002Z"
    with pytest.raises(Exception, match="H011_SOURCE_NOT_AVAILABLE_ASOF_BOUNDARY"):
        _run(fixture)


def test_source_created_at_after_boundary_fails_closed() -> None:
    fixture = _fixture()
    for row in fixture["h011_packet"]["predictions"]:
        row["created_at"] = "2026-09-24T12:00:00.001Z"
    with pytest.raises(Exception, match="H011_SOURCE_NOT_AVAILABLE_ASOF_BOUNDARY"):
        _run(fixture)


def test_capture_lateness_over_2s_fails_closed() -> None:
    fixture = _fixture()
    _event(fixture, "H+00")["at"] = "2026-09-24T12:00:02.001Z"
    fixture["arq3_capture_timing"]["capture_completed_ms"] = 1790251202001
    fixture["arq3_capture_timing"]["actual_capture_ms"] = 1790251202001
    fixture["arq3_capture_timing"]["lateness_ms"] = 2001
    fixture["arq3_capture_timing"]["TIMING_VALID"] = False
    with pytest.raises(Exception, match="CAPTURE_LATE"):
        _run(fixture)


def test_obs1_is_untouched_by_fixture_dry_run() -> None:
    before = COHORT_PATH.read_bytes()
    assert hashlib.sha256(before).hexdigest() == OBS1_SHA256
    _run()
    after = COHORT_PATH.read_bytes()
    assert after == before
    assert hashlib.sha256(after).hexdigest() == OBS1_SHA256
