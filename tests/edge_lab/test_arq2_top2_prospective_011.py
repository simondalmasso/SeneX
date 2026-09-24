from __future__ import annotations

import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from edge_lab.arq2_top2_prospective_011 import (
    EARLIEST_POSSIBLE_COMPLETE_READ_UTC,
    FREEZE_TS_UTC,
    REFERENCE_CORE_HEAD,
    TARGET_NEW_NONOVERLAP_N,
    build_hot_query,
    collection_read_allowed,
    collect_hot_bounded,
    deterministic_nonoverlap,
    effective_top2_component,
    final_analysis,
    initialize_manifest,
    initialize_progress,
    label_action_independent,
    prepare_candidate_from_hot_cold,
    row_is_eligible,
    select_frozen_cohort,
    should_stop_at_known_high_water,
)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _audit(
    *,
    source_commit: str = REFERENCE_CORE_HEAD,
    component: str = "oi",
    persisted_value=0.02,
    availability_status: str = "REAL_NONZERO",
    fallback=None,
    masked: list[str] | None = None,
) -> dict:
    feature = {
        "oi": "oi_momentum",
        "funding": "funding",
        "orderflow": "orderflow",
    }[component]
    pressures = {
        "orderflow": 0.05,
        "funding": -0.01,
        "oi": 0.02,
        "bidask": 0.01,
        "volume_delta": -0.002,
        "price_momentum": 0.003,
        "polymarket": 0.0,
    }
    pressures[component] = persisted_value
    numeric_sum = sum(
        float(v) for v in pressures.values()
        if isinstance(v, (int, float))
    )
    return {
        "decision_replay_v1": {
            "runtime_provenance": {
                "exact": True,
                "source_commit": source_commit,
            }
        },
        "pipeline": {
            "step1_market": {
                "feature_availability_v1": {
                    feature: {
                        "status": availability_status,
                        "fallback_value": fallback,
                        "source": "fixture",
                    }
                }
            },
            "step2_features": {
                "total_pressure": numeric_sum,
                "pressures": pressures,
                "missing_input_mask_v1": {
                    "masked_features": list(masked or [])
                },
            },
        },
    }


def _row(
    pid: int,
    ts: str,
    *,
    action: str = "HOLD",
    outcome=None,
    source_commit: str = REFERENCE_CORE_HEAD,
) -> dict:
    return {
        "source_prediction_id": pid,
        "id": pid,
        "ts": ts,
        "symbol": "BTCUSDT",
        "action": action,
        "outcome": outcome,
        "audit": _audit(source_commit=source_commit),
    }


def test_freeze_excludes_every_007_010_row() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "research" / "arq2_score_denominator_007.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    freeze = _dt(FREEZE_TS_UTC)
    assert all(_dt(row["ts"]) <= freeze for row in rows)


def test_collection_gate_forbids_any_normal_d1_read_before_earliest_gate() -> None:
    before = _dt("2026-09-28T00:23:34Z")
    at_gate = _dt(EARLIEST_POSSIBLE_COMPLETE_READ_UTC)
    assert collection_read_allowed(before) is False
    assert collection_read_allowed(at_gate) is True


def test_no_early_metric_path_before_n96() -> None:
    rows = [{"source_prediction_id": i, "y_up": i % 2} for i in range(95)]
    with pytest.raises(ValueError, match="NO_EARLY_LOOK"):
        final_analysis(rows)


def test_deterministic_nonoverlap_and_exact_96_freeze() -> None:
    base = _dt("2026-09-24T01:00:00Z")
    rows = []
    for i in range(120):
        ts = base.timestamp() + i * 3600
        rows.append({
            "source_prediction_id": 1000 + i,
            "ts": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        })
    selected = deterministic_nonoverlap(list(reversed(rows)))
    assert [r["source_prediction_id"] for r in selected[:3]] == [1000, 1001, 1002]
    frozen = select_frozen_cohort(rows)
    assert len(frozen) == TARGET_NEW_NONOVERLAP_N == 96
    assert frozen[-1]["source_prediction_id"] == 1095


def test_action_and_outcome_do_not_affect_eligibility() -> None:
    ts = "2026-09-24T02:00:00Z"
    hold = _row(1, ts, action="HOLD", outcome=None)
    execute = _row(2, ts, action="EXECUTE", outcome="LOSS")
    evaluation = _dt("2026-09-24T04:00:00Z")
    assert row_is_eligible(hold, evaluation_time=evaluation)[0] is True
    assert row_is_eligible(execute, evaluation_time=evaluation)[0] is True


def test_hot_query_has_no_outcome_filter_offset_or_count() -> None:
    q1 = build_hot_query(None, page_size=80)
    q2 = build_hot_query(7000, page_size=80)
    combined = (q1 + " " + q2).upper()
    assert q1.lstrip().upper().startswith("SELECT ")
    assert "ORDER BY ID DESC" in combined
    assert "ID < 7000" in combined
    import re
    assert not re.search(r"WHERE\\s+[^;]*(?:OUTCOME\\s*=|OUTCOME\\s+IN)", combined)
    assert "OFFSET" not in combined
    assert "COUNT(" not in combined


def test_exact_c7_provenance_required() -> None:
    row = _row(
        1,
        "2026-09-24T02:00:00Z",
        source_commit="wrong",
    )
    eligible, reason = row_is_eligible(
        row,
        evaluation_time=_dt("2026-09-24T04:00:00Z"),
    )
    assert eligible is False
    assert reason == "RUNTIME_PROVENANCE_COMMIT_MISMATCH"


@pytest.mark.parametrize(
    ("component", "feature"),
    [
        ("oi", "oi_momentum"),
        ("funding", "funding"),
        ("orderflow", "orderflow"),
    ],
)
def test_null_to_zero_requires_full_missing_semantic_proof(component: str, feature: str) -> None:
    audit = _audit(
        component=component,
        persisted_value=None,
        availability_status="MISSING",
        fallback=0.0,
        masked=[feature],
    )
    out = effective_top2_component(component=component, audit=audit)
    assert out["effective_value"] == 0.0
    assert out["recovered_by_runtime_semantics"] is True

    bad = json.loads(json.dumps(audit))
    bad["pipeline"]["step1_market"]["feature_availability_v1"][feature]["fallback_value"] = 0.1
    with pytest.raises(ValueError, match="NULL_SEMANTICS_UNPROVEN"):
        effective_top2_component(component=component, audit=bad)


def test_exact_96_rows_make_manifest_final_and_do_not_adapt_n() -> None:
    manifest = initialize_manifest()
    progress = initialize_progress()
    rows = [
        {
            "source_prediction_id": i,
            "ts": datetime.fromtimestamp(
                _dt("2026-09-24T01:00:00Z").timestamp() + i * 3600,
                tz=timezone.utc,
            ).isoformat(),
        }
        for i in range(100)
    ]
    frozen = select_frozen_cohort(rows)
    assert len(frozen) == 96
    assert manifest["target_new_nonoverlap_n"] == 96
    assert progress["progress_n"] == 0


def test_resumable_high_water_stops_when_known_range_is_reached() -> None:
    progress = initialize_progress()
    progress["scan_high_water_id"] = 7000
    assert should_stop_at_known_high_water(7001, progress) is False
    assert should_stop_at_known_high_water(7000, progress) is True
    assert should_stop_at_known_high_water(6999, progress) is True


def test_no_component_optimization_surface() -> None:
    import edge_lab.arq2_top2_prospective_011 as module

    public = {name for name in dir(module) if not name.startswith("_")}
    forbidden = {
        "optimize_orderflow",
        "optimize_funding",
        "optimize_oi",
        "tune_weights",
        "invert_score",
        "calibrate_score",
        "best_component",
        "winner_component",
    }
    assert public.isdisjoint(forbidden)


def test_zero_write_and_no_runtime_import_surface() -> None:
    import edge_lab.arq2_top2_prospective_011 as module

    source = inspect.getsource(module)
    upper = source.upper()
    for token in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "LIVE_ORDER"):
        assert token not in upper
    assert "senecio_polymarket.oracle_runtime" not in source
    assert "senecio_polymarket.oracle." not in source


def test_early_gate_prevents_runner_invocation_entirely() -> None:
    calls = {"n": 0}

    def runner(_sql: str):
        calls["n"] += 1
        return [], {"rows_written": 0, "changed_db": False}, "hash"

    with pytest.raises(RuntimeError, match="D1_EARLY_READ_BLOCKED"):
        collect_hot_bounded(
            now=_dt("2026-09-27T23:59:59Z"),
            progress=initialize_progress(),
            runner=runner,
            max_pages=1,
        )
    assert calls["n"] == 0


def test_hot_cold_integrity_and_action_independent_label() -> None:
    from senecio_polymarket.backend.settlement_contract import (
        price_evidence_from_candles,
    )

    ts = "2026-09-24T02:00:00+00:00"
    audit = _audit()
    audit["origin_price_v1"] = {
        "version": "origin-price-v1",
        "price": 100.0,
        "source": "okx",
        "timestamp": ts,
    }
    audit["action_vector"] = {"action": "HOLD"}
    payload = json.dumps({"audit": audit}, separators=(",", ":"))
    payload_sha = hashlib.sha256(payload.encode()).hexdigest()

    hot = {
        "id": 9001,
        "ts": ts,
        "symbol": "BTCUSDT",
        "prediction": "FLAT",
        "exchange_used": "okx",
        "audit_digest": "audit-9001",
        "cold_payload_sha256": payload_sha,
    }
    cold = {
        "prediction_id": 9001,
        "payload": payload,
        "audit_digest": "audit-9001",
        "payload_sha256": payload_sha,
    }
    candidate = prepare_candidate_from_hot_cold(
        hot,
        cold,
        evaluation_time=_dt("2026-09-24T04:00:00Z"),
    )

    target_ms = int(_dt("2026-09-24T03:00:00Z").timestamp() * 1000)
    evidence = price_evidence_from_candles(
        candles=[[target_ms, 0, 0, 0, 101.0, 0]],
        exchange="okx",
        symbol="BTCUSDT",
        ts_iso=ts,
        window_seconds=3600,
        observed_at="2026-09-24T03:02:00Z",
    )
    labeled_hold = label_action_independent(candidate, evidence)

    changed = dict(candidate)
    changed["action"] = "EXECUTE"
    changed["final_prediction"] = "SHORT"
    labeled_execute = label_action_independent(changed, evidence)

    assert labeled_hold["label_status"] == "LABELABLE"
    assert labeled_execute["label_status"] == "LABELABLE"
    assert labeled_hold["y_up"] == labeled_execute["y_up"] == 1


def test_hot_cold_hash_mismatch_fails_closed() -> None:
    audit = _audit()
    audit["origin_price_v1"] = {
        "version": "origin-price-v1",
        "price": 100.0,
        "source": "okx",
        "timestamp": "2026-09-24T02:00:00+00:00",
    }
    payload = json.dumps({"audit": audit}, separators=(",", ":"))
    hot = {
        "id": 9001,
        "ts": "2026-09-24T02:00:00+00:00",
        "symbol": "BTCUSDT",
        "prediction": "FLAT",
        "exchange_used": "okx",
        "audit_digest": "audit-9001",
        "cold_payload_sha256": "wrong",
    }
    cold = {
        "prediction_id": 9001,
        "payload": payload,
        "audit_digest": "audit-9001",
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }
    with pytest.raises(Exception, match="PAYLOAD_LINK_MISMATCH"):
        prepare_candidate_from_hot_cold(
            hot,
            cold,
            evaluation_time=_dt("2026-09-24T04:00:00Z"),
        )
