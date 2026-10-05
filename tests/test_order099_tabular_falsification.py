from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
O97_PATH = ROOT / "research" / "edge" / "order097" / "market_prior_calibration.py"
O99_PATH = ROOT / "research" / "edge" / "order099" / "tabular_falsification.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _modules():
    return _load(O97_PATH, "order097_for_order099_tests"), _load(
        O99_PATH, "order099_tabular"
    )


def _obs(o97, market_i: int, *, p_market: float, senex: float, label: int):
    start = 1791069600 + market_i * 300
    decision = start + 30
    pair = o97.T0Pair(
        prediction_id=market_i,
        decision_ts=decision,
        market_slug=f"btc-updown-5m-{start}",
        condition_id=f"cond-{market_i}",
        market_start_ts=start,
        market_end_ts=start + 300,
        market_horizon_seconds=300,
        p_market=p_market,
        senex_raw_up=senex,
    )
    return o97.JoinedObservation(
        pair=pair,
        label_up=label,
        resolved_at=start + 301,
    )


def test_train_cutpoints_do_not_depend_on_holdout_values():
    o97, m = _modules()
    train = [
        _obs(o97, i, p_market=0.10 + i * 0.08, senex=0.20 + i * 0.06, label=i % 2)
        for i in range(8)
    ]
    holdout_a = [
        _obs(o97, 20, p_market=0.01, senex=0.99, label=1),
        _obs(o97, 21, p_market=0.99, senex=0.01, label=0),
    ]
    holdout_b = [
        _obs(o97, 20, p_market=0.49, senex=0.51, label=1),
        _obs(o97, 21, p_market=0.51, senex=0.49, label=0),
    ]

    a = m.derive_train_cutpoints(train)
    b = m.derive_train_cutpoints(train)

    assert a == b
    assert holdout_a != holdout_b
    assert set(a) == {
        "p_market_quartiles",
        "senex_raw_quartiles",
        "abs_disagreement_quartiles",
        "near_zero_disagreement_epsilon",
    }


@pytest.mark.parametrize(
    "hour,expected",
    [
        (0, "00-05"),
        (5, "00-05"),
        (6, "06-11"),
        (11, "06-11"),
        (12, "12-17"),
        (17, "12-17"),
        (18, "18-23"),
        (23, "18-23"),
    ],
)
def test_utc_session_bucket_is_fixed_in_advance(hour, expected):
    _, m = _modules()
    ts = datetime(2026, 10, 4, hour, 30, tzinfo=timezone.utc).timestamp()
    assert m.utc_session_bucket(ts) == expected


def test_tabular_rows_use_train_derived_buckets_and_preserve_exact_market_identity():
    o97, m = _modules()
    train = [
        _obs(o97, i, p_market=0.10 + i * 0.08, senex=0.20 + i * 0.06, label=i % 2)
        for i in range(8)
    ]
    cutpoints = m.derive_train_cutpoints(train)
    rows = m.tabular_rows(train, split="TRAIN", cutpoints=cutpoints)

    assert len(rows) == 8
    assert rows[0]["split"] == "TRAIN"
    assert rows[0]["market_slug"].startswith("btc-updown-5m-")
    assert rows[0]["condition_id"].startswith("cond-")
    assert rows[0]["signed_disagreement"] == pytest.approx(
        rows[0]["senex_raw_up"] - rows[0]["p_market"]
    )
    assert rows[0]["abs_disagreement"] == pytest.approx(
        abs(rows[0]["signed_disagreement"])
    )
    assert rows[0]["p_market_quartile"] in {"Q1", "Q2", "Q3", "Q4"}
    assert rows[0]["senex_raw_quartile"] in {"Q1", "Q2", "Q3", "Q4"}
    assert rows[0]["disagreement_sign"] in {"NEGATIVE", "NEAR_ZERO", "POSITIVE"}


def test_market_cluster_bootstrap_is_deterministic_and_respects_constant_effect():
    _, m = _modules()
    loss_rows = [
        {
            "market_key": f"m{i}",
            "brier_delta": -0.10,
            "log_loss_delta": -0.20,
        }
        for i in range(12)
    ]

    a = m.cluster_bootstrap(loss_rows, n_bootstrap=500, random_seed=17)
    b = m.cluster_bootstrap(loss_rows, n_bootstrap=500, random_seed=17)

    assert a == b
    assert a["n_markets"] == 12
    assert a["brier"]["mean_delta"] == pytest.approx(-0.10)
    assert a["brier"]["ci95_low"] == pytest.approx(-0.10)
    assert a["brier"]["ci95_high"] == pytest.approx(-0.10)
    assert a["brier"]["fraction_augmented_better"] == pytest.approx(1.0)
    assert a["log_loss"]["mean_delta"] == pytest.approx(-0.20)


def test_market_cluster_bootstrap_counts_rows_within_sampled_market_clusters():
    _, m = _modules()
    loss_rows = [
        {"market_key": "A", "brier_delta": -0.20, "log_loss_delta": -0.20},
        {"market_key": "A", "brier_delta": -0.10, "log_loss_delta": -0.10},
        {"market_key": "B", "brier_delta": 0.10, "log_loss_delta": 0.10},
        {"market_key": "C", "brier_delta": 0.00, "log_loss_delta": 0.00},
    ]
    result = m.cluster_bootstrap(loss_rows, n_bootstrap=200, random_seed=7)

    assert result["n_rows"] == 4
    assert result["n_markets"] == 3
    assert result["brier"]["mean_delta"] == pytest.approx(-0.05)


def test_edge_verdict_requires_practical_brier_gain_and_both_ci_upper_bounds_below_zero():
    _, m = _modules()
    supported = {
        "brier": {
            "mean_delta": -0.008,
            "ci95_low": -0.012,
            "ci95_high": -0.006,
        },
        "log_loss": {
            "mean_delta": -0.020,
            "ci95_low": -0.030,
            "ci95_high": -0.010,
        },
    }
    merely_negative = {
        "brier": {
            "mean_delta": -0.003,
            "ci95_low": -0.008,
            "ci95_high": -0.001,
        },
        "log_loss": {
            "mean_delta": -0.020,
            "ci95_low": -0.030,
            "ci95_high": -0.010,
        },
    }
    uncertain = {
        "brier": {
            "mean_delta": -0.010,
            "ci95_low": -0.020,
            "ci95_high": 0.002,
        },
        "log_loss": {
            "mean_delta": -0.020,
            "ci95_low": -0.030,
            "ci95_high": -0.010,
        },
    }

    assert m.edge_verdict(supported) == "EDGE_SUPPORTED"
    assert m.edge_verdict(merely_negative) == "INCREMENTAL_EDGE_NOT_DEMONSTRATED"
    assert m.edge_verdict(uncertain) == "INCREMENTAL_EDGE_NOT_DEMONSTRATED"


def test_unique_market_selection_keeps_earliest_decision_only():
    o97, m = _modules()
    early = _obs(o97, 1, p_market=0.40, senex=0.55, label=1)
    late_pair = early.pair._replace(
        prediction_id=999,
        decision_ts=early.pair.decision_ts + 120,
        p_market=0.80,
        senex_raw_up=0.20,
    )
    late = o97.JoinedObservation(
        pair=late_pair,
        label_up=early.label_up,
        resolved_at=early.resolved_at,
    )

    selected = m.unique_market_observations([late, early])

    assert len(selected) == 1
    assert selected[0].pair.prediction_id == early.pair.prediction_id
    assert selected[0].pair.p_market == pytest.approx(0.40)


def test_corrected_subgroup_inference_uses_n30_floor_and_existing_holm_bh():
    _, m = _modules()
    rows = [
        {
            "market_key": f"neg-{i}",
            "brier_delta": -0.05,
            "log_loss_delta": -0.10,
            "disagreement_sign": "NEGATIVE",
        }
        for i in range(40)
    ]
    rows.extend(
        {
            "market_key": f"small-{i}",
            "brier_delta": -0.20,
            "log_loss_delta": -0.20,
            "disagreement_sign": "POSITIVE",
        }
        for i in range(10)
    )

    result = m.corrected_subgroup_inference(
        rows,
        fields=("disagreement_sign",),
    )

    assert result["n_hypotheses"] == 1
    cells = {
        (cell["field"], cell["value"]): cell
        for cell in result["cells"]
    }
    strong = cells[("disagreement_sign", "NEGATIVE")]
    small = cells[("disagreement_sign", "POSITIVE")]
    assert strong["n"] == 40
    assert strong["inference_eligible"] is True
    assert strong["raw_p_value"] < 1e-6
    assert strong["holm_rejected"] is True
    assert strong["bh_rejected"] is True
    assert small["n"] == 10
    assert small["inference_eligible"] is False
    assert small["raw_p_value"] is None
    assert small["holm_rejected"] is False
    assert small["bh_rejected"] is False


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def test_order098_artifact_manifests_are_verified_end_to_end(tmp_path):
    _, m = _modules()
    predictions = tmp_path / "t0_predictions.jsonl"
    resolutions = tmp_path / "resolutions.jsonl"
    prediction_rows = [
        {"id": 1, "source_audit_sha256": "a" * 64},
        {"id": 2, "source_audit_sha256": "b" * 64},
    ]
    resolution_rows = [
        {
            "slug": "btc-updown-5m-1791069600",
            "condition_id": "0xabc",
            "start_ts": 1791069600,
            "end_ts": 1791069900,
            "outcome": "UP",
            "resolved_at": 1791069901,
            "source": "POLYMARKET_GAMMA_RESOLVED_V1",
        }
    ]
    predictions.write_text(
        "".join(_canonical(row) + "\n" for row in prediction_rows),
        encoding="utf-8",
    )
    resolutions.write_text(
        "".join(_canonical(row) + "\n" for row in resolution_rows),
        encoding="utf-8",
    )
    predictions_sha = _sha256_file(predictions)
    resolutions_sha = _sha256_file(resolutions)

    p_manifest = tmp_path / "t0_manifest.json"
    p_manifest.write_text(
        json.dumps({
            "contract": "senex-order098-t0-audit-export-v1",
            "output_file_sha256": predictions_sha,
            "output_row_hashes_sha256": _sha256_text(
                _canonical(["a" * 64, "b" * 64])
            ),
            "fetched_rows": 2,
            "projected_rows": 2,
            "skipped_rows": 0,
        }),
        encoding="utf-8",
    )
    r_manifest = tmp_path / "resolution_manifest.json"
    r_manifest.write_text(
        json.dumps({
            "contract": "senex-order098-polymarket-5m-resolution-corpus-v1",
            "predictions_file_sha256": predictions_sha,
            "output_file_sha256": resolutions_sha,
            "resolution_records_sha256": _sha256_text(
                _canonical(resolution_rows)
            ),
            "requested_markets": 1,
            "accepted_markets": 1,
            "rejected_markets": 0,
        }),
        encoding="utf-8",
    )

    result = m.verify_order098_artifacts(
        predictions,
        p_manifest,
        resolutions,
        r_manifest,
    )

    assert result["predictions_sha256"] == predictions_sha
    assert result["resolutions_sha256"] == resolutions_sha
    assert result["requested_markets"] == 1
    assert result["accepted_markets"] == 1
    assert result["rejected_markets"] == 0


def test_order098_artifact_verification_fails_closed_on_hash_or_partial_corpus(tmp_path):
    _, m = _modules()
    predictions = tmp_path / "t0_predictions.jsonl"
    resolutions = tmp_path / "resolutions.jsonl"
    predictions.write_text(
        _canonical({"id": 1, "source_audit_sha256": "a" * 64}) + "\n",
        encoding="utf-8",
    )
    resolutions.write_text(
        _canonical({"slug": "btc-updown-5m-1791069600"}) + "\n",
        encoding="utf-8",
    )
    predictions_sha = _sha256_file(predictions)
    resolutions_sha = _sha256_file(resolutions)
    p_manifest = tmp_path / "t0_manifest.json"
    p_manifest.write_text(json.dumps({
        "contract": "senex-order098-t0-audit-export-v1",
        "output_file_sha256": predictions_sha,
        "output_row_hashes_sha256": _sha256_text(_canonical(["a" * 64])),
        "fetched_rows": 1,
        "projected_rows": 1,
        "skipped_rows": 0,
    }), encoding="utf-8")
    r_manifest = tmp_path / "resolution_manifest.json"
    r_manifest.write_text(json.dumps({
        "contract": "senex-order098-polymarket-5m-resolution-corpus-v1",
        "predictions_file_sha256": predictions_sha,
        "output_file_sha256": resolutions_sha,
        "resolution_records_sha256": _sha256_text(
            _canonical([{"slug": "btc-updown-5m-1791069600"}])
        ),
        "requested_markets": 2,
        "accepted_markets": 1,
        "rejected_markets": 1,
    }), encoding="utf-8")

    with pytest.raises(m.ArtifactContractError, match="partial"):
        m.verify_order098_artifacts(
            predictions,
            p_manifest,
            resolutions,
            r_manifest,
        )

    bad_manifest = json.loads(r_manifest.read_text(encoding="utf-8"))
    bad_manifest["requested_markets"] = 1
    bad_manifest["rejected_markets"] = 0
    bad_manifest["output_file_sha256"] = "0" * 64
    r_manifest.write_text(json.dumps(bad_manifest), encoding="utf-8")
    with pytest.raises(m.ArtifactContractError, match="SHA256"):
        m.verify_order098_artifacts(
            predictions,
            p_manifest,
            resolutions,
            r_manifest,
        )


def test_preregistered_market_floor_blocks_199_and_allows_200():
    _, m = _modules()

    blocker = m.minimum_market_gate(199)
    assert blocker is not None
    assert "200 unique resolved markets" in blocker

    assert m.minimum_market_gate(200) is None


def test_direct_cli_help_runs_from_repo_root():
    result = subprocess.run(
        [sys.executable, str(O99_PATH), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "ORDER099 preregistered tabular falsification" in result.stdout


def test_order098_artifact_verification_rejects_selective_resolution_coverage(tmp_path):
    _, m = _modules()
    predictions = tmp_path / "t0_predictions.jsonl"
    resolutions = tmp_path / "resolutions.jsonl"
    p_manifest = tmp_path / "t0_manifest.json"
    r_manifest = tmp_path / "resolution_manifest.json"

    starts = [1791090000, 1791090300]
    prediction_rows = []
    for i, start in enumerate(starts, start=1):
        prediction_rows.append({
            "id": i,
            "ts": start + 30,
            "symbol": "BTCUSDT",
            "audit": {
                "pipeline": {
                    "step2_features": {
                        "up_prob": 0.55,
                        "polymarket_context_v1": {
                            "directional_use": False,
                            "experiment_enabled": False,
                            "effective_weight": 0.0,
                        },
                    }
                },
                "external_markets_v1": {
                    "polymarket": {
                        "source": "POLYMARKET_PUBLIC",
                        "version": "polymarket-btc-5m-v1",
                        "eligible_for_prediction": True,
                        "slug": f"btc-updown-5m-{start}",
                        "condition_id": f"cond-{i}",
                        "start_ts": start,
                        "end_ts": start + 300,
                        "up_probability": 0.50,
                    }
                },
            },
            "source_audit_sha256": f"{i:064x}",
        })

    resolution_rows = [{
        "slug": f"btc-updown-5m-{starts[0]}",
        "condition_id": "cond-1",
        "start_ts": starts[0],
        "end_ts": starts[0] + 300,
        "outcome": "UP",
        "resolved_at": starts[0] + 301,
        "source": "POLYMARKET_GAMMA_RESOLVED_V1",
    }]

    predictions.write_text(
        "".join(_canonical(row) + "\n" for row in prediction_rows),
        encoding="utf-8",
    )
    resolutions.write_text(
        "".join(_canonical(row) + "\n" for row in resolution_rows),
        encoding="utf-8",
    )
    predictions_sha = _sha256_file(predictions)
    resolutions_sha = _sha256_file(resolutions)

    p_manifest.write_text(json.dumps({
        "contract": "senex-order098-t0-audit-export-v1",
        "output_file_sha256": predictions_sha,
        "output_row_hashes_sha256": _sha256_text(
            _canonical([row["source_audit_sha256"] for row in prediction_rows])
        ),
        "fetched_rows": 2,
        "projected_rows": 2,
        "skipped_rows": 0,
    }), encoding="utf-8")
    r_manifest.write_text(json.dumps({
        "contract": "senex-order098-polymarket-5m-resolution-corpus-v1",
        "predictions_file_sha256": predictions_sha,
        "output_file_sha256": resolutions_sha,
        "resolution_records_sha256": _sha256_text(_canonical(resolution_rows)),
        "requested_markets": 1,
        "accepted_markets": 1,
        "rejected_markets": 0,
    }), encoding="utf-8")

    with pytest.raises(m.ArtifactContractError, match="market identity coverage"):
        m.verify_order098_artifacts(
            predictions,
            p_manifest,
            resolutions,
            r_manifest,
        )
