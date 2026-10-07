[Reading 452 lines from start (total: 452 lines, 0 remaining)]

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.challengers.common import (
    ChallengerContractError,
    Observation,
    append_prospective_receipt,
    assert_historical_synthetic_only,
    calibration_report,
    load_manifest,
    manifest_sha256,
    proper_score_report,
    purged_walk_forward_splits,
    seal_prospective_receipt,
)
from research.challengers.evaluation import (
    EvaluationContractError,
    evaluate_historical_records,
    fit_market_residual_stack,
)
from research.challengers.recency_challenger_v1 import (
    CHALLENGER_ID as RECENCY_ID,
    RecencyDocument,
    aggregate_features,
)
from research.challengers.selection import (
    evaluate_market_residual_folds,
    select_candidates,
)
from research.challengers.synthetic_benchmark import run_synthetic
from research.challengers.wolfram_recal_v1 import (
    CHALLENGER_ID as WOLFRAM_ID,
    fit_recalibration,
    predict,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "research" / "challengers" / "manifests"


def _ts(base: datetime, hours: int) -> str:
    return (base + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def _rows(n: int = 60) -> list[Observation]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        rows.append(
            Observation(
                market_id=f"m-{i}",
                decision_ts=_ts(base, i),
                label_end_ts=_ts(base, i + 4),
                label=i % 2,
                p_market=0.4 if i % 2 else 0.6,
                senex_raw_up=0.3 if i % 2 else 0.7,
            )
        )
    return rows


def test_frozen_manifests_are_zero_spend_and_historical_only():
    for name in ("WOLFRAM_RECAL_V1.json", "RECENCY_CHALLENGER_V1.json"):
        manifest = load_manifest(MANIFESTS / name)
        assert manifest["zero_spend"] is True
        assert manifest["prospective_t_star"] is None
        assert manifest["prospective_n"] is None
        assert_historical_synthetic_only(manifest)
        assert len(manifest_sha256(manifest)) == 64

    wolfram = load_manifest(MANIFESTS / "WOLFRAM_RECAL_V1.json")
    recency = load_manifest(MANIFESTS / "RECENCY_CHALLENGER_V1.json")
    assert wolfram["external_reference"]["commit"] == "359f7770bdc49be4390669f9f96d0afd6822d88c"
    assert recency["external_reference"]["commit"] == "7f582ad8c2e140eca08098b245f9e31b68e28b60"
    assert recency["normalizer_contract"] == "SENEX_RECENCY_NORMALIZER_V1"
    assert recency["mixed_extractor_policy"] == "FAIL_CLOSED"


def test_wolfram_math_verification_artifact_is_hash_bound_and_positive_definite():
    manifest = load_manifest(MANIFESTS / "WOLFRAM_RECAL_V1.json")
    artifact = (MANIFESTS / manifest["math_verification_artifact"]).resolve()
    observed = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert observed == manifest["math_verification_sha256"]

    receipt = json.loads(artifact.read_text(encoding="utf-8"))
    assert receipt["wolfram_result"]["gradient_match"] is True
    assert receipt["wolfram_result"]["hessian_match"] is True
    assert "lambda^2" in receipt["wolfram_result"]["determinant"]


def test_purged_walk_forward_never_allows_overlapping_training_label_window():
    rows = _rows()
    splits = purged_walk_forward_splits(
        rows,
        n_splits=4,
        min_train_size=20,
        embargo_seconds=2 * 3600,
    )
    assert len(splits) == 4
    for split in splits:
        test_start = datetime.fromisoformat(split.test_start_ts.replace("Z", "+00:00"))
        cutoff = test_start - timedelta(seconds=split.embargo_seconds)
        assert set(split.train_indices).isdisjoint(split.test_indices)
        assert all(
            datetime.fromisoformat(rows[idx].label_end_ts.replace("Z", "+00:00"))
            < cutoff
            for idx in split.train_indices
        )


def test_proper_scores_report_incremental_delta_against_market():
    labels = [1, 0, 1, 0]
    market = [0.6, 0.4, 0.6, 0.4]
    candidate = [0.8, 0.2, 0.8, 0.2]
    report = proper_score_report(labels, market, candidate)
    assert report["delta_brier"] < 0
    assert report["delta_log_loss"] < 0


def test_calibration_report_is_explicit_and_fixed_bin():
    labels = [1, 0, 1, 0]
    underconfident = calibration_report(labels, [0.75, 0.25, 0.75, 0.25], n_bins=4)
    near_deterministic = calibration_report(labels, [0.99, 0.01, 0.99, 0.01], n_bins=4)

    assert underconfident["n"] == 4
    assert underconfident["n_bins"] == 4
    assert math.isfinite(underconfident["ece"])
    assert len(underconfident["bins"]) > 0
    assert near_deterministic["ece"] < underconfident["ece"]


def test_wolfram_recalibration_reference_is_deterministic():
    raw = [0.15, 0.25, 0.4, 0.55, 0.7, 0.85, 0.2, 0.8]
    labels = [0, 0, 0, 1, 1, 1, 0, 1]
    a = fit_recalibration(raw, labels)
    b = fit_recalibration(raw, labels)
    assert a == b
    assert a.digest() == b.digest()
    probabilities = predict(a, raw)
    assert len(probabilities) == len(raw)
    assert all(0.0 < value < 1.0 for value in probabilities)
    assert a.slope >= 0.0
    ranked_raw = sorted(raw)
    ranked_calibrated = predict(a, ranked_raw)
    assert ranked_calibrated == sorted(ranked_calibrated)


def test_wolfram_recalibration_never_inverts_ranking_under_anti_signal():
    raw = [0.10, 0.20, 0.30, 0.70, 0.80, 0.90]
    labels = [1, 1, 1, 0, 0, 0]

    model = fit_recalibration(raw, labels)
    calibrated = predict(model, sorted(raw))

    assert model.slope >= 0.0
    assert calibrated == sorted(calibrated)


def test_recency_rejects_post_cutoff_and_market_odds():
    cutoff = "2026-10-06T12:00:00Z"
    post = RecencyDocument(
        source="reddit",
        document_id="post",
        published_at="2026-10-06T12:01:00Z",
        captured_at="2026-10-06T12:01:00Z",
        topic="crypto",
        sentiment=0.1,
        is_breaking=False,
        content_sha256="a" * 64,
        extractor_id="SENEX_RECENCY_NORMALIZER_V1",
        extractor_sha256="f" * 64,
    )
    with pytest.raises(ChallengerContractError, match="post-cutoff"):
        aggregate_features([post], cutoff_ts=cutoff)

    odds = RecencyDocument(
        source="public_web",
        document_id="odds",
        published_at="2026-10-06T11:00:00Z",
        captured_at="2026-10-06T11:01:00Z",
        topic="crypto",
        sentiment=0.1,
        is_breaking=False,
        content_sha256="b" * 64,
        extractor_id="SENEX_RECENCY_NORMALIZER_V1",
        extractor_sha256="f" * 64,
        contains_market_odds=True,
    )
    with pytest.raises(ChallengerContractError, match="forbids Polymarket odds"):
        aggregate_features([odds], cutoff_ts=cutoff)


def test_recency_rejects_non_frozen_source_type():
    document = RecencyDocument(
        source="paid_x_api",
        document_id="x",
        published_at="2026-10-06T11:00:00Z",
        captured_at="2026-10-06T11:01:00Z",
        topic="crypto",
        sentiment=0.0,
        is_breaking=False,
        content_sha256="c" * 64,
        extractor_id="SENEX_RECENCY_NORMALIZER_V1",
        extractor_sha256="f" * 64,
    )
    with pytest.raises(ChallengerContractError, match="zero-spend source set"):
        aggregate_features([document], cutoff_ts="2026-10-06T12:00:00Z")


def test_recency_rejects_duplicate_document_identity_and_mixed_extractor_hashes():
    cutoff = "2026-10-06T12:00:00Z"
    base = RecencyDocument(
        source="reddit",
        document_id="same",
        published_at="2026-10-06T11:00:00Z",
        captured_at="2026-10-06T11:01:00Z",
        topic="crypto",
        sentiment=0.2,
        is_breaking=False,
        content_sha256="1" * 64,
        extractor_id="SENEX_RECENCY_NORMALIZER_V1",
        extractor_sha256="f" * 64,
    )
    duplicate = RecencyDocument(
        source="reddit",
        document_id="same",
        published_at="2026-10-06T11:05:00Z",
        captured_at="2026-10-06T11:06:00Z",
        topic="crypto",
        sentiment=0.3,
        is_breaking=False,
        content_sha256="2" * 64,
        extractor_id="SENEX_RECENCY_NORMALIZER_V1",
        extractor_sha256="f" * 64,
    )
    with pytest.raises(ChallengerContractError, match="duplicate recency document"):
        aggregate_features([base, duplicate], cutoff_ts=cutoff)

    other = RecencyDocument(
        source="github",
        document_id="other",
        published_at="2026-10-06T11:10:00Z",
        captured_at="2026-10-06T11:11:00Z",
        topic="other",
        sentiment=0.0,
        is_breaking=False,
        content_sha256="3" * 64,
        extractor_id="SENEX_RECENCY_NORMALIZER_V1",
        extractor_sha256="e" * 64,
    )
    with pytest.raises(ChallengerContractError, match="mixes extractor hashes"):
        aggregate_features([base, other], cutoff_ts=cutoff)


def test_recency_aggregation_is_causal_and_deterministic():
    cutoff = "2026-10-06T12:00:00Z"
    docs = [
        RecencyDocument(
            source="reddit",
            document_id="a",
            published_at="2026-10-06T11:30:00Z",
            captured_at="2026-10-06T11:35:00Z",
            topic="crypto",
            sentiment=0.6,
            is_breaking=True,
            content_sha256="d" * 64,
            extractor_id="SENEX_RECENCY_NORMALIZER_V1",
            extractor_sha256="f" * 64,
        ),
        RecencyDocument(
            source="hackernews",
            document_id="b",
            published_at="2026-10-06T08:00:00Z",
            captured_at="2026-10-06T08:05:00Z",
            topic="macro",
            sentiment=-0.2,
            is_breaking=False,
            content_sha256="e" * 64,
            extractor_id="SENEX_RECENCY_NORMALIZER_V1",
            extractor_sha256="f" * 64,
        ),
    ]
    first = aggregate_features(docs, cutoff_ts=cutoff)
    second = aggregate_features(reversed(docs), cutoff_ts=cutoff)
    assert first == second
    assert first["document_count_6h"] == 2.0
    assert first["document_count_1h"] == 1.0
    assert first["source_diversity_6h"] == 2.0
    assert first["breaking_event_flag_6h"] == 1.0
    assert first["missing_all_sources"] == 0.0


def test_market_residual_stack_does_not_flip_anti_signal():
    labels = [1, 0, 1, 0, 1, 0, 1, 0]
    market = [0.65, 0.35, 0.65, 0.35, 0.65, 0.35, 0.65, 0.35]
    anti_senex = [0.20, 0.80, 0.20, 0.80, 0.20, 0.80, 0.20, 0.80]

    result = fit_market_residual_stack(market, anti_senex, labels)

    assert result["beta_senex_residual"] == pytest.approx(0.0)
    assert result["comparison_vs_market"]["delta_brier_vs_market"] == pytest.approx(0.0)
    assert result["comparison_vs_market"]["delta_log_loss_vs_market"] == pytest.approx(0.0)


def test_market_residual_stack_is_evaluated_purged_oos_and_not_selectable():
    rows = _rows(72)
    splits = purged_walk_forward_splits(
        rows,
        n_splits=3,
        min_train_size=30,
        embargo_seconds=2 * 3600,
    )
    diagnostic = evaluate_market_residual_folds(rows, splits)

    assert diagnostic["diagnostic_id"] == "MARKET_PLUS_SENEX_RESIDUAL_OOS_V1"
    assert diagnostic["selection_eligible"] is False
    assert diagnostic["oos_market_count"] == sum(
        len(split.test_indices) for split in splits
    )
    assert len(diagnostic["folds"]) == len(splits)
    assert all(fold["beta_senex_residual"] >= 0.0 for fold in diagnostic["folds"])
    assert "candidate_calibration" in diagnostic["overall"]


def test_historical_evaluator_rejects_prospective_rows():
    with pytest.raises(EvaluationContractError, match="prospective rows are forbidden"):
        evaluate_historical_records(
            [
                {
                    "dataset_role": "PROSPECTIVE",
                    "label": 1,
                    "p_market": 0.55,
                    "candidate_probability": 0.60,
                }
            ]
        )


def test_selection_is_capped_at_two_and_requires_non_worse_proper_scores():
    reports = {
        "A": {"valid": True, "overall": {"delta_brier": -0.03, "delta_log_loss": -0.02}},
        "B": {"valid": True, "overall": {"delta_brier": -0.01, "delta_log_loss": -0.01}},
        "C": {"valid": True, "overall": {"delta_brier": 0.01, "delta_log_loss": -0.02}},
    }
    assert select_candidates(reports, max_candidates=2) == ["A", "B"]
    with pytest.raises(ChallengerContractError, match="at most two"):
        select_candidates(reports, max_candidates=3)


def test_synthetic_benchmark_evaluates_both_without_edge_claim():
    result = run_synthetic()
    assert result["synthetic_only"] is True
    assert result["edge_claim"] == "NONE"
    assert result["prospective_t_star"] is None
    assert result["prospective_n"] is None
    assert set(result["reports"]) == {WOLFRAM_ID, RECENCY_ID}
    assert result["market_residual_diagnostic"]["selection_eligible"] is False
    assert result["market_residual_diagnostic"]["oos_market_count"] > 0
    assert set(result["manifest_sha256"]) == {WOLFRAM_ID, RECENCY_ID}
    assert all(len(value) == 64 for value in result["manifest_sha256"].values())
    assert len(result["selected_candidates"]) <= 2
    for report in result["reports"].values():
        assert report["oos_market_count"] > 0
        assert math.isfinite(report["overall"]["delta_brier"])
        assert math.isfinite(report["overall"]["delta_log_loss"])
        assert math.isfinite(report["overall"]["candidate_calibration"]["ece"])


def test_committed_synthetic_report_matches_deterministic_runner():
    committed = json.loads(
        (ROOT / "research" / "challengers" / "results" / "SYNTHETIC_V1_REPORT.json")
        .read_text(encoding="utf-8")
    )
    assert committed == run_synthetic()


def test_prospective_receipt_is_hash_bound_and_must_precede_outcome():
    manifest = load_manifest(MANIFESTS / "WOLFRAM_RECAL_V1.json")
    digest = manifest_sha256(manifest)
    kwargs = {
        "challenger_id": WOLFRAM_ID,
        "challenger_version": "1",
        "source_commit": "d" * 40,
        "manifest_sha256_value": digest,
        "market_id": "btc-market-1",
        "cutoff_ts": "2026-10-06T12:00:00Z",
        "outcome_not_before_ts": "2026-10-06T12:05:00Z",
        "created_at": "2026-10-06T12:00:01Z",
        "p_market": 0.55,
        "candidate_probability": 0.58,
        "input_features": {"senex_raw_up": 0.61},
        "model_sha256": "6" * 64,
        "output_sha256": "4" * 64,
    }
    first = seal_prospective_receipt(**kwargs)
    second = seal_prospective_receipt(**kwargs)
    assert first == second
    assert len(first["receipt_sha256"]) == 64
    assert first["model_sha256"] == "6" * 64
    assert first["output_sha256"] == "4" * 64
    assert first["paper_only"] is True
    assert first["live"] is False
    from research.challengers.common import verify_prospective_receipt
    assert verify_prospective_receipt(first) is True

    kwargs["created_at"] = "2026-10-06T12:05:00Z"
    with pytest.raises(ChallengerContractError, match="before outcome eligibility"):
        seal_prospective_receipt(**kwargs)

    kwargs["created_at"] = "2026-10-06T12:00:01Z"
    kwargs["source_commit"] = "deadbeef"
    with pytest.raises(ChallengerContractError, match="40-hex commit"):
        seal_prospective_receipt(**kwargs)


def test_prospective_receipt_ledger_is_append_only_and_idempotent(tmp_path):
    manifest = load_manifest(MANIFESTS / "WOLFRAM_RECAL_V1.json")
    base = {
        "challenger_id": WOLFRAM_ID,
        "challenger_version": "1",
        "source_commit": "d" * 40,
        "manifest_sha256_value": manifest_sha256(manifest),
        "market_id": "btc-market-2",
        "cutoff_ts": "2026-10-06T13:00:00Z",
        "outcome_not_before_ts": "2026-10-06T13:05:00Z",
        "created_at": "2026-10-06T13:00:01Z",
        "p_market": 0.52,
        "candidate_probability": 0.54,
        "input_features": {"senex_raw_up": 0.57},
        "model_sha256": "7" * 64,
        "output_sha256": "5" * 64,
    }
    receipt = seal_prospective_receipt(**base)
    ledger = tmp_path / "receipts.jsonl"

    assert append_prospective_receipt(ledger, receipt) is True
    assert append_prospective_receipt(ledger, receipt) is False
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1

    conflicting = dict(base)
    conflicting["candidate_probability"] = 0.61
    other = seal_prospective_receipt(**conflicting)
    with pytest.raises(ChallengerContractError, match="identity conflict"):
        append_prospective_receipt(ledger, other)

[executed on device: DESKTOP-DPH3941 (f5db7315-cdea-42b4-b067-243411e4a115)]