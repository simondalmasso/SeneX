[Reading 219 lines from start (total: 219 lines, 0 remaining)]

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
O97_PATH = ROOT / "research" / "edge" / "order097" / "market_prior_calibration.py"
O100_PATH = ROOT / "research" / "edge" / "order100" / "prospective_low_disagreement.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _modules():
    return _load(O97_PATH, "order097_for_order100_tests"), _load(
        O100_PATH, "order100_prospective"
    )


def _obs(o97, market_i: int, *, start_ts: int, p_market: float, senex: float, label: int):
    pair = o97.T0Pair(
        prediction_id=market_i,
        decision_ts=start_ts + 30,
        market_slug=f"btc-updown-5m-{start_ts}",
        condition_id=f"cond-{market_i}",
        market_start_ts=start_ts,
        market_end_ts=start_ts + 300,
        market_horizon_seconds=300,
        p_market=p_market,
        senex_raw_up=senex,
    )
    return o97.JoinedObservation(
        pair=pair,
        label_up=label,
        resolved_at=start_ts + 301,
    )


def test_frozen_models_match_preregistered_coefficients():
    _, m = _modules()
    market, augmented = m.frozen_models()

    assert market.intercept == pytest.approx(-0.029977798153386935)
    assert market.coefficients == pytest.approx((0.4346033993273502,))
    assert augmented.intercept == pytest.approx(-0.016707909800067956)
    assert augmented.coefficients == pytest.approx(
        (0.42444216403460056, 0.04187479127974901)
    )


def test_prospective_subset_enforces_cutoff_band_and_one_market_one_row():
    o97, m = _modules()
    cutoff = m.PROSPECTIVE_START_TS
    rows = [
        _obs(o97, 1, start_ts=cutoff - 300, p_market=0.50, senex=0.50, label=1),
        _obs(o97, 2, start_ts=cutoff, p_market=0.50, senex=0.60, label=1),
        _obs(o97, 3, start_ts=cutoff + 300, p_market=0.50, senex=0.80, label=0),
    ]
    duplicate = rows[1]._replace(
        pair=rows[1].pair._replace(prediction_id=99, decision_ts=cutoff + 60)
    )
    rows.append(duplicate)

    subset = m.prospective_primary_subset(rows)

    assert len(subset) == 1
    assert subset[0].pair.prediction_id == 2
    assert subset[0].pair.market_start_ts == cutoff


def test_prospective_status_blocks_until_300_unique_markets():
    _, m = _modules()
    assert m.prospective_sample_gate(299) is not None
    assert m.prospective_sample_gate(300) is None


@pytest.mark.parametrize(
    "bootstrap,n_markets,expected",
    [
        (
            {
                "brier": {"mean_delta": -0.006, "ci95_high": -0.001},
                "log_loss": {"mean_delta": -0.01, "ci95_high": -0.002},
            },
            300,
            "PROSPECTIVE_EDGE_CONFIRMED",
        ),
        (
            {
                "brier": {"mean_delta": -0.004, "ci95_high": -0.001},
                "log_loss": {"mean_delta": -0.01, "ci95_high": -0.002},
            },
            300,
            "PROSPECTIVE_INCREMENTAL_EDGE_NOT_CONFIRMED",
        ),
        (
            {
                "brier": {"mean_delta": -0.006, "ci95_high": 0.001},
                "log_loss": {"mean_delta": -0.01, "ci95_high": -0.002},
            },
            300,
            "PROSPECTIVE_INCREMENTAL_EDGE_NOT_CONFIRMED",
        ),
        (
            {
                "brier": {"mean_delta": -0.006, "ci95_high": -0.001},
                "log_loss": {"mean_delta": -0.01, "ci95_high": -0.002},
            },
            299,
            "COLLECTING_PROSPECTIVE_DATA",
        ),
    ],
)
def test_prospective_verdict_is_frozen_and_fail_closed(bootstrap, n_markets, expected):
    _, m = _modules()
    assert m.prospective_verdict(bootstrap, n_markets=n_markets) == expected


def test_band_classification_uses_earliest_market_row_before_filtering():
    o97, m = _modules()
    cutoff = m.PROSPECTIVE_START_TS
    earliest = _obs(
        o97,
        20,
        start_ts=cutoff,
        p_market=0.50,
        senex=0.90,
        label=1,
    )
    later = earliest._replace(
        pair=earliest.pair._replace(
            prediction_id=21,
            decision_ts=cutoff + 60,
            senex_raw_up=0.55,
        )
    )

    subset = m.prospective_primary_subset([earliest, later])

    assert subset == []


def test_primary_eval_does_not_compute_bootstrap_below_frozen_gate(monkeypatch):
    o97, m = _modules()
    cutoff = m.PROSPECTIVE_START_TS
    rows = [
        _obs(o97, 30, start_ts=cutoff, p_market=0.50, senex=0.55, label=1),
        _obs(o97, 31, start_ts=cutoff + 300, p_market=0.50, senex=0.55, label=0),
    ]

    def forbidden(*args, **kwargs):
        raise AssertionError("bootstrap must not run below ORDER100 N>=300 gate")

    monkeypatch.setattr(m.order099, "cluster_bootstrap", forbidden)
    result = m._evaluate_subset(
        rows,
        n_bootstrap=m.DEFAULT_BOOTSTRAP,
        seed=m.DEFAULT_SEED,
        minimum_markets=m.MIN_PROSPECTIVE_MARKETS,
    )

    assert result == {
        "n_markets": 2,
        "status": "GATE_CLOSED_NO_INTERIM_METRICS",
    }


@pytest.mark.parametrize(
    "bootstrap,seed",
    [
        (9999, 7),
        (10000, 8),
        (1, 1),
        (10000.9, 7),
        (10000, 7.9),
        (10000.1, 7.1),
        (True, 7),
        (10000, False),
        ("10000", 7),
        (10000, "7"),
    ],
)
def test_frozen_eval_parameters_reject_noncanonical_values(bootstrap, seed):
    _, m = _modules()
    with pytest.raises(ValueError, match="frozen evaluator requires"):
        m._require_frozen_eval_parameters(bootstrap, seed)

    m._require_frozen_eval_parameters(m.DEFAULT_BOOTSTRAP, m.DEFAULT_SEED)


def test_order100_requires_prospective_v2_lineage_contract():
    source = O100_PATH.read_text(encoding="utf-8")
    assert (
        'required_prediction_contract="senex-order098-t0-audit-export-v2"'
        in source
    )
    assert "persistence_receipts_path=persistence_receipts_path" in source
    assert 'parser.add_argument("--persistence-receipts", required=True)' in source


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_prospective_verdict_rejects_nonfinite_metrics(bad):
    _, m = _modules()
    bootstrap = {
        "brier": {"mean_delta": bad, "ci95_high": bad},
        "log_loss": {"mean_delta": bad, "ci95_high": bad},
    }
    assert (
        m.prospective_verdict(bootstrap, n_markets=300)
        == "PROSPECTIVE_INCREMENTAL_EDGE_NOT_CONFIRMED"
    )
