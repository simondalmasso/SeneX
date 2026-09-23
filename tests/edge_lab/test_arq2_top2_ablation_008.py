from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from edge_lab.arq2_top2_ablation_008 import (
    SOURCE_DATASET_SHA256,
    PressurePersistenceInsufficient,
    analyze_ablation,
    build_feature_scores,
    load_source_dataset,
    paired_bootstrap_delta_auc,
)


def _record(pid: int, ts: str, score: float, label: int) -> dict:
    return {
        "source_prediction_id": pid,
        "ts": ts,
        "raw_up_prob": 0.5,
        "total_pressure": score,
        "y_up": label,
        "label_status": "LABELABLE",
        "step2_direction": "LONG",
        "final_prediction": "LONG",
        "action": "EXECUTE",
        "stored_outcome": None,
    }


def _cold(pid: int, *, total: float, orderflow, funding, oi) -> dict:
    payload = json.dumps(
        {
            "audit": {
                "decision_replay_v1": {
                    "runtime_provenance": {
                        "exact": True,
                        "source_commit": "c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a",
                    }
                },
                "pipeline": {
                    "step2_features": {
                        "total_pressure": total,
                        "pressures": {
                            "orderflow": orderflow,
                            "funding": funding,
                            "oi": oi,
                        },
                    }
                },
            }
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "prediction_id": pid,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def test_exact_007_dataset_hash_is_required(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    path.write_text('{"source_prediction_id":1}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_DATASET_HASH_MISMATCH"):
        load_source_dataset(path)


def test_build_feature_scores_uses_only_frozen_persisted_group_formula() -> None:
    result = build_feature_scores(
        total_pressure=0.40,
        orderflow_pressure=0.10,
        funding_pressure=-0.02,
        oi_pressure=0.03,
    )
    assert result == {
        "full_score": pytest.approx(0.40),
        "top2_pressure": pytest.approx(0.11),
        "no_top2_score": pytest.approx(0.29),
        "micro_only_score": pytest.approx(0.11),
    }


def test_missing_pressure_component_fails_closed() -> None:
    rows = [
        _record(1, "2026-09-23T00:00:00+00:00", 0.3, 0),
        _record(2, "2026-09-23T01:00:00+00:00", 0.7, 1),
    ]
    cold = {
        1: _cold(1, total=0.3, orderflow=0.1, funding=0.01, oi=None),
        2: _cold(2, total=0.7, orderflow=0.2, funding=0.01, oi=0.03),
    }
    result = analyze_ablation(rows, cold, nonoverlap_ids=[1, 2])
    assert result["status"] == "PERSISTENCE_INSUFFICIENT"
    assert result["top2_components_present_n"] == 1
    assert result["nonoverlap_top2_complete_n"] == 1
    assert result["delta_auc_top2"] is None
    assert result["top2_verdict"] == "UNMEASURED"


def test_nonoverlap_membership_is_identity_stable_and_not_reselected() -> None:
    rows = [
        _record(10, "2026-09-23T00:00:00+00:00", 0.2, 0),
        _record(11, "2026-09-23T00:10:00+00:00", 0.8, 1),
        _record(12, "2026-09-23T01:00:00+00:00", 0.7, 1),
    ]
    cold = {
        10: _cold(10, total=0.2, orderflow=0.01, funding=0.01, oi=0.01),
        11: _cold(11, total=0.8, orderflow=0.01, funding=0.01, oi=0.01),
        12: _cold(12, total=0.7, orderflow=0.01, funding=0.01, oi=0.01),
    }
    result = analyze_ablation(rows, cold, nonoverlap_ids=[10, 12])
    assert result["nonoverlap_ids"] == [10, 12]
    assert result["n_inferential"] == 2


def test_paired_bootstrap_uses_same_resampled_indices() -> None:
    rows = [
        {"full_score": 0.1, "no_top2_score": 0.4, "y_up": 0},
        {"full_score": 0.2, "no_top2_score": 0.3, "y_up": 0},
        {"full_score": 0.8, "no_top2_score": 0.2, "y_up": 1},
        {"full_score": 0.9, "no_top2_score": 0.1, "y_up": 1},
    ]
    out = paired_bootstrap_delta_auc(rows, replicates=200, seed=17)
    assert out["valid_replicates"] > 0
    assert out["paired_indices"] is True
    assert out["delta_auc"] == pytest.approx(1.0)
    assert out["ci95"][0] >= 0.0


def test_labels_cannot_enter_feature_construction() -> None:
    sig = inspect.signature(build_feature_scores)
    assert "y_up" not in sig.parameters
    assert "label" not in sig.parameters


def test_no_individual_component_optimization_or_tuning_surface_exists() -> None:
    import edge_lab.arq2_top2_ablation_008 as module

    public = {name for name in dir(module) if not name.startswith("_")}
    forbidden = {
        "optimize_orderflow",
        "optimize_funding",
        "optimize_oi",
        "tune_weights",
        "invert_score",
        "calibrate_score",
    }
    assert public.isdisjoint(forbidden)


def test_research_module_has_no_d1_write_or_runtime_mutation_surface() -> None:
    import edge_lab.arq2_top2_ablation_008 as module

    source = inspect.getsource(module).upper()
    for token in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "DEPLOY", "LIVE_ORDER"):
        assert token not in source


def test_real_007_nonoverlap_identity_is_exactly_stable() -> None:
    root = Path(__file__).resolve().parents[2]
    rows = load_source_dataset(root / "research" / "arq2_score_denominator_007.jsonl")
    assert len(rows) == 125
    ids = __import__("edge_lab.arq2_top2_ablation_008", fromlist=["source_nonoverlap_ids"]).source_nonoverlap_ids(rows)
    assert ids == [
        5858, 5866, 5874, 5882, 5890, 5898, 5906, 5914,
        5922, 5930, 5938, 5946, 5954, 5962, 5970, 5978,
        5986, 5994, 6002, 6010, 6018, 6026, 6034, 6042,
        6050, 6058, 6066, 6074, 6082, 6090, 6098, 6106,
    ]


def test_source_contains_no_score_tuning_inversion_or_calibration_path() -> None:
    import edge_lab.arq2_top2_ablation_008 as module

    source = inspect.getsource(module).lower()
    for forbidden in (
        "tune_weight",
        "optimize_weight",
        "invert_score",
        "calibrate_score",
        "best_component",
        "winner_component",
    ):
        assert forbidden not in source
