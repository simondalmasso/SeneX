from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

import edge_lab.arq2_top2_ablation_008 as a008
from edge_lab.arq2_top2_ablation_010 import (
    BOOTSTRAP_SEED,
    EXPECTED_NONOVERLAP_ID_SHA256,
    EXPECTED_NONOVERLAP_N,
    RECOVERY_ROW_ID,
    REPLICATES,
    analyze_ablation_010,
    build_exact_cold_query,
    load_recovery_proof,
    load_source_population,
    recover_effective_oi_pressure,
)


def _source(pid: int, ts: str, label: int) -> dict:
    return {
        "source_prediction_id": pid,
        "ts": ts,
        "y_up": label,
        "label_status": "LABELABLE",
    }


def _cold(
    pid: int,
    *,
    total: float,
    orderflow,
    funding,
    oi,
    oi_status: str = "REAL_NONZERO",
    fallback=None,
    masked: list[str] | None = None,
) -> dict:
    pressures = {
        "orderflow": orderflow,
        "funding": funding,
        "oi": oi,
        "bidask": 0.01,
        "volume_delta": -0.002,
        "price_momentum": 0.003,
        "polymarket": 0.0,
    }
    payload = json.dumps(
        {
            "audit": {
                "decision_replay_v1": {
                    "runtime_provenance": {
                        "exact": True,
                        "source_commit": a008.REFERENCE_CORE_HEAD,
                    }
                },
                "pipeline": {
                    "step1_market": {
                        "feature_availability_v1": {
                            "oi_momentum": {
                                "status": oi_status,
                                "source": "fixture",
                                "fallback_value": fallback,
                            }
                        }
                    },
                    "step2_features": {
                        "total_pressure": total,
                        "pressures": pressures,
                        "missing_input_mask_v1": {
                            "masked_features": list(masked or [])
                        },
                    },
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


def _proof() -> dict:
    return {
        "num_order": "ARQ2-TOP2-MISSING-SEMANTICS-009",
        "status": "COMPLETE",
        "row_id": 5858,
        "row_integrity": "PASS",
        "reference_core_head": a008.REFERENCE_CORE_HEAD,
        "source_dataset_sha256": a008.SOURCE_DATASET_SHA256,
        "recovery_verdict": "SEMANTICALLY_RECOVERABLE_WITHOUT_IMPUTATION",
        "null_oi_semantics": "MASKED_UNAVAILABLE_ZERO_EFFECTIVE_CONTRIBUTION",
    }


def test_exact_007_dataset_hash_and_exact_32_identity_required() -> None:
    root = Path(__file__).resolve().parents[2]
    rows, ids = load_source_population(
        root / "research" / "arq2_score_denominator_007.jsonl"
    )
    assert len(rows) == 125
    assert len(ids) == EXPECTED_NONOVERLAP_N == 32
    digest = hashlib.sha256(",".join(map(str, ids)).encode()).hexdigest()
    assert digest == EXPECTED_NONOVERLAP_ID_SHA256


def test_cold_query_requests_only_exact_32_ids() -> None:
    root = Path(__file__).resolve().parents[2]
    _, ids = load_source_population(
        root / "research" / "arq2_score_denominator_007.jsonl"
    )
    query = build_exact_cold_query(ids)
    assert "oracle_prediction_audit_cold" in query
    assert "OFFSET" not in query.upper()
    assert "COUNT(" not in query.upper()
    assert "prediction_id IN (" in query
    for pid in ids:
        assert str(pid) in query
    assert "LIMIT" not in query.upper()


def test_only_5858_null_oi_can_recover_to_effective_zero() -> None:
    cold = _cold(
        5858,
        total=0.111,
        orderflow=0.1,
        funding=0.0,
        oi=None,
        oi_status="MISSING",
        fallback=0.0,
        masked=["oi_momentum"],
    )
    doc = json.loads(cold["payload"])
    step2 = doc["audit"]["pipeline"]["step2_features"]
    # Make algebra exact: all other numeric pressures sum to total.
    step2["total_pressure"] = sum(
        float(v) for v in step2["pressures"].values()
        if isinstance(v, (int, float))
    )
    result = recover_effective_oi_pressure(
        prediction_id=5858,
        audit=doc["audit"],
        recovery_proof=_proof(),
    )
    assert result["effective_oi_pressure"] == 0.0
    assert result["recovered_by_runtime_semantics"] is True


def test_5858_recovery_requires_availability_mask_algebra_and_009_verdict() -> None:
    base = _cold(
        5858,
        total=0.111,
        orderflow=0.1,
        funding=0.0,
        oi=None,
        oi_status="MISSING",
        fallback=0.0,
        masked=["oi_momentum"],
    )
    doc = json.loads(base["payload"])
    step2 = doc["audit"]["pipeline"]["step2_features"]
    step2["total_pressure"] = sum(
        float(v) for v in step2["pressures"].values()
        if isinstance(v, (int, float))
    )
    audit = doc["audit"]

    broken = json.loads(json.dumps(audit))
    broken["pipeline"]["step1_market"]["feature_availability_v1"]["oi_momentum"]["status"] = "REAL_OBSERVED_ZERO"
    with pytest.raises(ValueError, match="RECOVERY_PROOF_FAILED"):
        recover_effective_oi_pressure(
            prediction_id=5858, audit=broken, recovery_proof=_proof()
        )

    broken = json.loads(json.dumps(audit))
    broken["pipeline"]["step2_features"]["missing_input_mask_v1"]["masked_features"] = []
    with pytest.raises(ValueError, match="RECOVERY_PROOF_FAILED"):
        recover_effective_oi_pressure(
            prediction_id=5858, audit=broken, recovery_proof=_proof()
        )

    broken = json.loads(json.dumps(audit))
    broken["pipeline"]["step2_features"]["total_pressure"] += 0.01
    with pytest.raises(ValueError, match="RECOVERY_PROOF_FAILED"):
        recover_effective_oi_pressure(
            prediction_id=5858, audit=broken, recovery_proof=_proof()
        )

    proof = _proof()
    proof["recovery_verdict"] = "NOT_RECOVERABLE"
    with pytest.raises(ValueError, match="RECOVERY_PROOF_FAILED"):
        recover_effective_oi_pressure(
            prediction_id=5858, audit=audit, recovery_proof=proof
        )


def test_any_other_null_component_fails_closed() -> None:
    cold = _cold(
        5866,
        total=0.2,
        orderflow=0.1,
        funding=None,
        oi=0.02,
    )
    rows = [_source(5866, "2026-09-23T00:00:00+00:00", 1)]
    result = analyze_ablation_010(
        rows,
        {5866: cold},
        recovery_proof=_proof(),
        nonoverlap_ids=[5866],
    )
    assert result["status"] == "PERSISTENCE_INSUFFICIENT"
    assert result["top2_verdict"] == "UNMEASURED"


def test_formula_is_identical_to_008() -> None:
    got = a008.build_feature_scores(
        total_pressure=0.4,
        orderflow_pressure=0.1,
        funding_pressure=-0.02,
        oi_pressure=0.03,
    )
    rows = [
        _source(1, "2026-09-23T00:00:00+00:00", 0),
        _source(2, "2026-09-23T01:00:00+00:00", 1),
    ]
    cold = {
        1: _cold(1, total=0.4, orderflow=0.1, funding=-0.02, oi=0.03),
        2: _cold(2, total=0.5, orderflow=0.2, funding=-0.01, oi=0.04),
    }
    result = analyze_ablation_010(
        rows, cold, recovery_proof=_proof(), nonoverlap_ids=[1, 2]
    )
    assert result["materialized_rows"][0]["full_score"] == got["full_score"]
    assert result["materialized_rows"][0]["no_top2_score"] == got["no_top2_score"]
    assert result["materialized_rows"][0]["top2_pressure"] == got["top2_pressure"]


def test_bootstrap_constants_are_identical_to_008() -> None:
    assert REPLICATES == a008.REPLICATES == 10000
    assert BOOTSTRAP_SEED == a008.BOOTSTRAP_SEED == 20260923


def test_labels_do_not_enter_feature_or_recovery_construction() -> None:
    sig = inspect.signature(recover_effective_oi_pressure)
    assert "y_up" not in sig.parameters
    assert "label" not in sig.parameters


def test_no_component_level_optimization_or_tuning_surface() -> None:
    import edge_lab.arq2_top2_ablation_010 as module

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


def test_research_module_has_no_d1_write_or_runtime_mutation_surface() -> None:
    import edge_lab.arq2_top2_ablation_010 as module

    source = inspect.getsource(module).upper()
    for token in (
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "DROP ",
        "ALTER ",
        "DEPLOY",
        "LIVE_ORDER",
    ):
        assert token not in source


def test_009_proof_loader_requires_exact_recovery_verdict(tmp_path: Path) -> None:
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(_proof()), encoding="utf-8")
    proof = load_recovery_proof(path)
    assert proof["row_id"] == RECOVERY_ROW_ID == 5858

    bad = _proof()
    bad["row_integrity"] = "FAIL"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="RECOVERY_ARTIFACT_INVALID"):
        load_recovery_proof(path)


def test_5858_recovery_rejects_nonzero_fallback() -> None:
    cold = _cold(
        5858,
        total=0.111,
        orderflow=0.1,
        funding=0.0,
        oi=None,
        oi_status="MISSING",
        fallback=0.25,
        masked=["oi_momentum"],
    )
    doc = json.loads(cold["payload"])
    step2 = doc["audit"]["pipeline"]["step2_features"]
    step2["total_pressure"] = sum(
        float(v) for v in step2["pressures"].values()
        if isinstance(v, (int, float))
    )
    with pytest.raises(ValueError, match="RECOVERY_PROOF_FAILED:AVAILABILITY"):
        recover_effective_oi_pressure(
            prediction_id=5858,
            audit=doc["audit"],
            recovery_proof=_proof(),
        )


def test_oi_null_on_any_other_id_fails_closed() -> None:
    cold = _cold(
        5866,
        total=0.111,
        orderflow=0.1,
        funding=0.0,
        oi=None,
        oi_status="MISSING",
        fallback=0.0,
        masked=["oi_momentum"],
    )
    rows = [_source(5866, "2026-09-23T00:00:00+00:00", 1)]
    result = analyze_ablation_010(
        rows,
        {5866: cold},
        recovery_proof=_proof(),
        nonoverlap_ids=[5866],
    )
    assert result["status"] == "PERSISTENCE_INSUFFICIENT"
    assert result["top2_verdict"] == "UNMEASURED"


def test_no_oracle_runtime_imports_from_010_module() -> None:
    import edge_lab.arq2_top2_ablation_010 as module

    source = inspect.getsource(module)
    assert "senecio_polymarket.oracle_runtime" not in source
    assert "senecio_polymarket.oracle." not in source
