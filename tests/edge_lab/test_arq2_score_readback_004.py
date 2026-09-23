from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from edge_lab.arq2_score_readback_004 import (
    REFERENCE_CORE_HEAD,
    ReadbackRowRejected,
    deterministic_nonoverlap,
    q5_q1_lift,
    quintile_up_frequency,
    roc_auc,
    validate_analysis_audit,
)


def _audit() -> dict:
    return {
        "decision_replay_v1": {
            "runtime_provenance": {
                "exact": True,
                "source_commit": REFERENCE_CORE_HEAD,
            }
        },
        "pipeline": {
            "step2_features": {
                "total_pressure": 0.25,
                "up_prob": 0.7,
            }
        },
        "decision_waterfall_v1": {
            "category": "DIRECTIONAL_EXECUTE",
            "raw_reason": "fixture",
        },
        "origin_price_v1": {"version": "origin-price-v1", "price": 100.0},
        "outcomes_dual": {
            "price_1h_later": 101.0,
            "settlement_observation_v1": {
                "version": "settlement-observation-v1",
                "observed_at": "2026-09-23T02:00:00Z",
            },
        },
    }


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (
            lambda a: a["decision_replay_v1"].pop("runtime_provenance"),
            "RUNTIME_PROVENANCE_MISSING",
        ),
        (
            lambda a: a["decision_replay_v1"]["runtime_provenance"].__setitem__("exact", False),
            "RUNTIME_PROVENANCE_NOT_EXACT",
        ),
        (
            lambda a: a["decision_replay_v1"]["runtime_provenance"].__setitem__("source_commit", "wrong"),
            "RUNTIME_PROVENANCE_COMMIT_MISMATCH",
        ),
    ],
)
def test_parser_rejects_missing_or_invalid_provenance(mutation, reason: str) -> None:
    audit = _audit()
    mutation(audit)
    with pytest.raises(ReadbackRowRejected, match=reason):
        validate_analysis_audit(audit)


@pytest.mark.parametrize(
    "where,key",
    [
        ("decision_replay_v1", "settlement_result"),
        ("step2_features", "price_1h_later"),
        ("step2_features", "realized_return"),
    ],
)
def test_parser_rejects_future_or_post_settlement_decision_fields(where: str, key: str) -> None:
    audit = _audit()
    if where == "step2_features":
        audit["pipeline"]["step2_features"][key] = 1
    else:
        audit[where][key] = 1
    with pytest.raises(ReadbackRowRejected, match="DECISION_FEATURE_CONTAMINATION"):
        validate_analysis_audit(audit)


def test_nonoverlap_selector_is_deterministic() -> None:
    base = datetime(2026, 9, 23, tzinfo=timezone.utc)
    rows = [
        {"id": 3, "ts": (base + timedelta(minutes=61)).isoformat()},
        {"id": 1, "ts": base.isoformat()},
        {"id": 2, "ts": (base + timedelta(minutes=30)).isoformat()},
        {"id": 4, "ts": (base + timedelta(minutes=121)).isoformat()},
    ]
    forward = [row["id"] for row in deterministic_nonoverlap(rows)]
    reverse = [row["id"] for row in deterministic_nonoverlap(list(reversed(rows)))]
    assert forward == reverse == [1, 3, 4]


def test_auc_verified_on_synthetic_fixture() -> None:
    assert roc_auc([(0.1, 0), (0.2, 0), (0.8, 1), (0.9, 1)]) == 1.0
    assert roc_auc([(0.9, 0), (0.8, 0), (0.2, 1), (0.1, 1)]) == 0.0


def test_quintiles_and_q5_minus_q1_verified_on_synthetic_fixture() -> None:
    records = [
        (0.1, 0, 1), (0.2, 0, 2),
        (0.3, 0, 3), (0.4, 0, 4),
        (0.5, 0, 5), (0.6, 1, 6),
        (0.7, 1, 7), (0.8, 1, 8),
        (0.9, 1, 9), (1.0, 1, 10),
    ]
    assert quintile_up_frequency(records) == [0.0, 0.0, 0.5, 1.0, 1.0]
    assert q5_q1_lift(records) == 1.0


def test_runtime_paths_do_not_import_readback_artifact() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [root / "senecio_polymarket" / "backend" / "oracle_runner.py"]
    paths += list((root / "senecio_polymarket" / "oracle_runtime").glob("*.py"))
    paths += list((root / "senecio_polymarket" / "backend" / "portfolio").glob("*.py"))
    for path in paths:
        assert "arq2_score_readback_004" not in path.read_text(encoding="utf-8")
