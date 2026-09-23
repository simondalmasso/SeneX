from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import pytest

import edge_lab.arq2_score_denominator_007 as denominator007

from edge_lab.arq2_score_denominator_007 import (
    REFERENCE_CORE_HEAD,
    DenominatorRowRejected,
    build_cold_query,
    build_hot_query,
    deterministic_nonoverlap,
    label_candidate,
    order004_style_subset,
    parse_candidate,
    rank_ic,
    analyze_records,
)
from senecio_polymarket.backend.settlement_contract import (
    price_evidence_from_candles,
)


def _cold_payload(*, action: str = "HOLD", prediction: str = "FLAT", dual_price: float | None = None) -> str:
    ts = "2026-09-23T12:34:56+00:00"
    audit = {
        "decision_replay_v1": {
            "runtime_provenance": {
                "exact": True,
                "source_commit": REFERENCE_CORE_HEAD,
            }
        },
        "pipeline": {
            "step2_features": {
                "up_prob": 0.61,
                "total_pressure": 0.12,
                "direction": "LONG",
            }
        },
        "decision_waterfall_v1": {
            "category": "FLAT_HOLD" if action == "HOLD" else "DIRECTIONAL_EXECUTE",
            "raw_reason": "fixture",
        },
        "action_vector": {
            "action": action,
            "side": None if action == "HOLD" else "LONG",
        },
        "origin_price_v1": {
            "version": "origin-price-v1",
            "price": 100.0,
            "source": "okx",
            "timestamp": ts,
        },
    }
    if dual_price is not None:
        audit["outcomes_dual"] = {"price_1h_later": dual_price}
    return json.dumps({"version": 1, "id": 7001, "schema": "fixture", "audit": audit}, separators=(",", ":"))


def _joined(*, action: str = "HOLD", prediction: str = "FLAT", outcome=None, dual_price=None):
    payload = _cold_payload(action=action, prediction=prediction, dual_price=dual_price)
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    hot = {
        "id": 7001,
        "ts": "2026-09-23T12:34:56+00:00",
        "symbol": "BTCUSDT",
        "prediction": prediction,
        "confidence": 0.6,
        "ev": 0.0,
        "price_now": 100.0,
        "outcome": outcome,
        "exchange_used": "okx",
        "created_at": "2026-09-23T12:34:57+00:00",
        "audit_digest": "audit-fixture",
        "cold_payload_sha256": sha,
    }
    cold = {
        "prediction_id": 7001,
        "payload": payload,
        "audit_digest": "audit-fixture",
        "payload_sha256": sha,
    }
    return hot, cold


def _evidence(*, exchange: str = "okx", ts: str = "2026-09-23T12:34:56+00:00", price: float = 101.0):
    target_open_ms = int(datetime(2026, 9, 23, 13, 34, tzinfo=timezone.utc).timestamp() * 1000)
    return price_evidence_from_candles(
        candles=[[target_open_ms, 0, 0, 0, price, 0]],
        exchange=exchange,
        symbol="BTCUSDT",
        ts_iso=ts,
        window_seconds=3600,
        observed_at="2026-09-23T13:36:00+00:00",
    )


def test_hot_query_is_keyset_select_without_outcome_filter() -> None:
    q1 = build_hot_query(None, page_size=80)
    q2 = build_hot_query(7000, page_size=80)
    assert q1.lstrip().upper().startswith("SELECT ")
    assert " LIMIT 80" in q1
    assert "ORDER BY id DESC" in q1
    assert "id < 7000" in q2
    assert "OFFSET" not in q1.upper() + q2.upper()
    assert "COUNT(" not in q1.upper() + q2.upper()
    assert not re.search(r"WHERE\s+[^;]*(?:outcome\s*=|outcome\s+IN)", q1, re.I)
    assert not re.search(r"WHERE\s+[^;]*(?:outcome\s*=|outcome\s+IN)", q2, re.I)


def test_cold_query_is_select_for_exact_ids_only() -> None:
    q = build_cold_query([9, 3, 9, 5])
    assert q.lstrip().upper().startswith("SELECT ")
    assert "prediction_id IN (3,5,9)" in q
    assert all(token not in q.upper() for token in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER "))


@pytest.mark.parametrize(
    ("prediction", "action", "outcome"),
    [
        ("FLAT", "HOLD", None),
        ("LONG", "EXECUTE", None),
        ("SHORT", "HOLD", "WIN"),
    ],
)
def test_flat_hold_and_directional_rows_can_enter_denominator(prediction, action, outcome) -> None:
    hot, cold = _joined(action=action, prediction=prediction, outcome=outcome)
    candidate = parse_candidate(
        hot,
        cold,
        evaluation_time="2026-09-23T14:00:00+00:00",
    )
    assert candidate["source_prediction_id"] == 7001
    assert candidate["final_prediction"] == prediction
    assert candidate["action"] == action


def test_candidate_rejects_wrong_runtime_provenance() -> None:
    hot, cold = _joined()
    doc = json.loads(cold["payload"])
    doc["audit"]["decision_replay_v1"]["runtime_provenance"]["source_commit"] = "wrong"
    cold["payload"] = json.dumps(doc, separators=(",", ":"))
    cold["payload_sha256"] = hashlib.sha256(cold["payload"].encode()).hexdigest()
    hot["cold_payload_sha256"] = cold["payload_sha256"]
    with pytest.raises(DenominatorRowRejected, match="RUNTIME_PROVENANCE_COMMIT_MISMATCH"):
        parse_candidate(hot, cold, evaluation_time="2026-09-23T14:00:00+00:00")


def test_y_up_is_independent_of_prediction_action_and_outcomes_dual() -> None:
    evidence = _evidence(price=101.0)
    labels = []
    for prediction, action, dual in [
        ("LONG", "EXECUTE", 0.01),
        ("SHORT", "HOLD", 999999.0),
        ("FLAT", "HOLD", 100.0),
    ]:
        hot, cold = _joined(action=action, prediction=prediction, outcome=None, dual_price=dual)
        candidate = parse_candidate(hot, cold, evaluation_time="2026-09-23T14:00:00+00:00")
        labeled = label_candidate(candidate, evidence)
        labels.append((labeled["label_status"], labeled["y_up"], labeled["target_price_1h"]))
    assert labels == [("LABELABLE", 1, 101.0)] * 3


def test_same_source_exact_target_evidence_required() -> None:
    hot, cold = _joined()
    candidate = parse_candidate(hot, cold, evaluation_time="2026-09-23T14:00:00+00:00")

    wrong_source = _evidence(exchange="kraken")
    assert label_candidate(candidate, wrong_source)["label_status"] == "UNRESOLVED"

    wrong_target = _evidence(ts="2026-09-23T12:35:56+00:00")
    assert label_candidate(candidate, wrong_target)["label_status"] == "UNRESOLVED"

    good = _evidence()
    assert label_candidate(candidate, good)["label_status"] == "LABELABLE"


def test_origin_source_and_timestamp_mismatch_are_unresolved() -> None:
    hot, cold = _joined()
    doc = json.loads(cold["payload"])
    doc["audit"]["origin_price_v1"]["source"] = "kraken"
    cold["payload"] = json.dumps(doc, separators=(",", ":"))
    cold["payload_sha256"] = hashlib.sha256(cold["payload"].encode()).hexdigest()
    hot["cold_payload_sha256"] = cold["payload_sha256"]
    candidate = parse_candidate(hot, cold, evaluation_time="2026-09-23T14:00:00+00:00")
    assert label_candidate(candidate, _evidence())["label_status"] == "UNRESOLVED"

    hot, cold = _joined()
    doc = json.loads(cold["payload"])
    doc["audit"]["origin_price_v1"]["timestamp"] = "2026-09-23T12:34:55+00:00"
    cold["payload"] = json.dumps(doc, separators=(",", ":"))
    cold["payload_sha256"] = hashlib.sha256(cold["payload"].encode()).hexdigest()
    hot["cold_payload_sha256"] = cold["payload_sha256"]
    candidate = parse_candidate(hot, cold, evaluation_time="2026-09-23T14:00:00+00:00")
    assert label_candidate(candidate, _evidence())["label_status"] == "UNRESOLVED"


def test_nonoverlap_is_deterministic() -> None:
    rows = [
        {"source_prediction_id": 3, "ts": "2026-09-23T01:01:00+00:00"},
        {"source_prediction_id": 1, "ts": "2026-09-23T00:00:00+00:00"},
        {"source_prediction_id": 2, "ts": "2026-09-23T00:30:00+00:00"},
        {"source_prediction_id": 4, "ts": "2026-09-23T02:01:00+00:00"},
    ]
    assert [r["source_prediction_id"] for r in deterministic_nonoverlap(rows)] == [1, 3, 4]
    assert [r["source_prediction_id"] for r in deterministic_nonoverlap(list(reversed(rows)))] == [1, 3, 4]


def test_selection_subset_does_not_change_full_denominator() -> None:
    rows = [
        {"source_prediction_id": 1, "stored_outcome": None, "final_prediction": "FLAT"},
        {"source_prediction_id": 2, "stored_outcome": "WIN", "final_prediction": "LONG"},
        {"source_prediction_id": 3, "stored_outcome": "LOSS", "final_prediction": "SHORT"},
    ]
    original = json.loads(json.dumps(rows))
    subset = order004_style_subset(rows)
    assert [r["source_prediction_id"] for r in subset] == [2, 3]
    assert rows == original


def test_rank_ic_is_spearman_style_and_descriptive() -> None:
    assert rank_ic([(0.1, 0), (0.2, 0), (0.8, 1), (0.9, 1)]) > 0.8
    assert rank_ic([(0.9, 0), (0.8, 0), (0.2, 1), (0.1, 1)]) < -0.8



def test_historical_settlement_learning_metadata_is_allowed_only_predecision() -> None:
    hot, cold = _joined()
    doc = json.loads(cold["payload"])
    step2 = doc["audit"]["pipeline"]["step2_features"]
    step2["learning_state_v1"] = {
        "decision_cutoff_epoch": 1790170000.0,
        "source_settlement_observation_epochs": [
            {"prediction_id": 6999, "observed_at_epoch": 1790169999.0}
        ],
    }
    cold["payload"] = json.dumps(doc, separators=(",", ":"))
    cold["payload_sha256"] = hashlib.sha256(cold["payload"].encode()).hexdigest()
    hot["cold_payload_sha256"] = cold["payload_sha256"]
    candidate = parse_candidate(hot, cold, evaluation_time="2026-09-23T14:00:00+00:00")
    assert candidate["source_prediction_id"] == 7001

    doc["audit"]["pipeline"]["step2_features"]["learning_state_v1"]["source_settlement_observation_epochs"][0]["observed_at_epoch"] = 1790170000.1
    cold["payload"] = json.dumps(doc, separators=(",", ":"))
    cold["payload_sha256"] = hashlib.sha256(cold["payload"].encode()).hexdigest()
    hot["cold_payload_sha256"] = cold["payload_sha256"]
    with pytest.raises(DenominatorRowRejected, match="POST_DECISION_LEARNING_EVIDENCE"):
        parse_candidate(hot, cold, evaluation_time="2026-09-23T14:00:00+00:00")


def test_wrangler_transport_aborts_if_backend_reports_any_write(monkeypatch, tmp_path) -> None:
    class Result:
        returncode = 0
        stderr = b""
        stdout = json.dumps([
            {"results": [], "meta": {"rows_written": 1, "changed_db": True}}
        ]).encode("utf-8")

    monkeypatch.setattr(denominator007.shutil, "which", lambda _name: "npx.cmd")
    monkeypatch.setattr(denominator007.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(RuntimeError, match="D1_WRITE_DETECTED"):
        denominator007.run_wrangler_select(
            workdir=tmp_path,
            binding="HOT",
            sql="SELECT id FROM oracle_predictions_hot ORDER BY id DESC LIMIT 1;",
        )


def test_population_a_reports_descriptive_raw_score_distribution() -> None:
    rows = [
        {
            "source_prediction_id": i + 1,
            "ts": f"2026-09-23T0{i}:00:00+00:00",
            "raw_up_prob": score,
            "y_up": i % 2,
            "label_status": "LABELABLE",
            "step2_direction": "LONG" if i % 2 else "SHORT",
            "final_prediction": "LONG",
            "action": "EXECUTE",
            "gate_raw_reason": "fixture",
            "stored_outcome": None,
        }
        for i, score in enumerate([0.1, 0.3, 0.7, 0.9])
    ]
    summary = analyze_records(rows, hot_rows_examined=4, full_audit_n=4)
    assert summary["raw_score_distribution"]["n"] == 4
    assert summary["raw_score_distribution"]["min"] == 0.1
    assert summary["raw_score_distribution"]["p50"] == pytest.approx(0.5)
    assert summary["raw_score_distribution"]["max"] == 0.9
