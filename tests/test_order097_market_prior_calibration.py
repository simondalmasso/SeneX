from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research" / "edge" / "order097" / "market_prior_calibration.py"


def _load():
    spec = importlib.util.spec_from_file_location("order097_market_prior", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(
    *,
    prediction_id=1,
    ts="2026-10-03T23:25:30Z",
    slug="btc-updown-5m-1791069900",
    condition_id="cond-1",
    start_ts=1791069900,
    end_ts=1791070200,
    p_market=0.64,
    p_senex=0.72,
    senex_outcome="WIN",
):
    return {
        "id": prediction_id,
        "ts": ts,
        "symbol": "BTCUSDT",
        "prediction": "LONG",
        "confidence": 0.8,
        # This is deliberately a different SENEX settlement target and MUST
        # never become the Polymarket 5m label.
        "outcome": senex_outcome,
        "_audit": {
            "pipeline": {
                "step2_features": {
                    "up_prob": p_senex,
                    "polymarket_context_v1": {
                        "version": "polymarket-pressure-v2",
                        "directional_use": False,
                        "effective_weight": 0.0,
                        "experiment_enabled": False,
                    },
                }
            },
            "external_markets_v1": {
                "version": "real-market-context-v1",
                "polymarket": {
                    "source": "POLYMARKET_PUBLIC",
                    "version": "polymarket-btc-5m-v1",
                    "status": "LIVE_WS",
                    "eligible_for_prediction": True,
                    "slug": slug,
                    "condition_id": condition_id,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "up_probability": p_market,
                    "down_probability": 1.0 - p_market,
                    "seconds_to_close": max(0, end_ts - 1791069930),
                    "freshness_s": 0.5,
                },
            },
        },
    }


def _resolution(
    *,
    slug="btc-updown-5m-1791069900",
    condition_id="cond-1",
    start_ts=1791069900,
    end_ts=1791070200,
    outcome="UP",
    resolved_at="2026-10-03T23:31:00Z",
):
    return {
        "slug": slug,
        "condition_id": condition_id,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "outcome": outcome,
        "resolved_at": resolved_at,
        "source": "POLYMARKET_RESOLUTION",
    }


def test_extracts_only_decision_time_market_prior_and_senex_score():
    m = _load()
    pair = m.extract_t0_pair(_row())
    assert pair is not None
    assert pair.prediction_id == 1
    assert pair.market_slug == "btc-updown-5m-1791069900"
    assert pair.condition_id == "cond-1"
    assert pair.p_market == pytest.approx(0.64)
    assert pair.senex_raw_up == pytest.approx(0.72)
    assert pair.market_horizon_seconds == 300


def test_senex_outcome_field_is_never_used_as_5m_label():
    m = _load()
    pair = m.extract_t0_pair(_row(senex_outcome="LOSS"))
    assert pair is not None
    assert not hasattr(pair, "label")
    assert m.join_resolutions([pair], []) == []


@pytest.mark.parametrize(
    "mutator",
    [
        lambda row: row["_audit"].pop("external_markets_v1"),
        lambda row: row["_audit"]["external_markets_v1"]["polymarket"].update(
            {"version": "unknown"}
        ),
        lambda row: row["_audit"]["external_markets_v1"]["polymarket"].update(
            {"eligible_for_prediction": False}
        ),
        lambda row: row["_audit"]["external_markets_v1"]["polymarket"].update(
            {"up_probability": None}
        ),
        lambda row: row["_audit"]["external_markets_v1"]["polymarket"].update(
            {"end_ts": 1791070500}
        ),
    ],
)
def test_invalid_or_non_5m_t0_context_fails_closed(mutator):
    m = _load()
    row = _row()
    mutator(row)
    assert m.extract_t0_pair(row) is None


@pytest.mark.parametrize(
    "start_ts,end_ts,slug",
    [
        (
            1791069901,
            1791070201,
            "btc-updown-5m-1791069901",
        ),
        (
            1791069900.5,
            1791070200.5,
            "btc-updown-5m-1791069900",
        ),
    ],
)
def test_t0_rejects_off_boundary_or_fractional_market_grid(
    start_ts,
    end_ts,
    slug,
):
    m = _load()
    row = _row(
        start_ts=start_ts,
        end_ts=end_ts,
        slug=slug,
    )
    assert m.extract_t0_pair(row) is None


@pytest.mark.parametrize(
    "start_ts,end_ts,slug",
    [
        (
            1791069901,
            1791070201,
            "btc-updown-5m-1791069901",
        ),
        (
            1791069900.5,
            1791070200.5,
            "btc-updown-5m-1791069900",
        ),
    ],
)
def test_resolution_rejects_off_boundary_or_fractional_market_grid(
    start_ts,
    end_ts,
    slug,
):
    m = _load()
    resolution = _resolution(
        start_ts=start_ts,
        end_ts=end_ts,
        slug=slug,
    )
    with pytest.raises(m.ResolutionContractError, match="market.*grid"):
        m.join_resolutions([], [resolution])


def test_circular_polymarket_influence_is_rejected():
    m = _load()
    row = _row()
    ctx = row["_audit"]["pipeline"]["step2_features"]["polymarket_context_v1"]
    ctx.update({
        "directional_use": True,
        "effective_weight": 0.25,
        "experiment_enabled": True,
    })
    assert m.extract_t0_pair(row) is None


def test_missing_polymarket_influence_audit_is_rejected():
    m = _load()
    row = _row()
    row["_audit"]["pipeline"]["step2_features"].pop("polymarket_context_v1")
    assert m.extract_t0_pair(row) is None


def test_t0_timestamp_must_fall_inside_the_exact_market_window():
    m = _load()
    row = _row(ts="2026-10-03T23:35:30Z")
    assert m.extract_t0_pair(row) is None


def test_resolution_must_match_slug_condition_and_grid():
    m = _load()
    pair = m.extract_t0_pair(_row())
    assert pair is not None

    assert m.join_resolutions(
        [pair],
        [_resolution(condition_id="wrong")],
    ) == []

    with pytest.raises(m.ResolutionContractError, match="market identity"):
        m.join_resolutions(
            [pair],
            [
                _resolution(),
                _resolution(condition_id="cond-1", outcome="DOWN"),
            ],
        )


@pytest.mark.parametrize("source", [None, "", "   ", 123])
def test_resolution_requires_explicit_string_provenance_source(source):
    m = _load()
    pair = m.extract_t0_pair(_row())
    assert pair is not None
    resolution = _resolution()
    resolution["source"] = source
    with pytest.raises(m.ResolutionContractError, match="source"):
        m.join_resolutions([pair], [resolution])


def test_resolution_before_market_close_is_rejected():
    m = _load()
    pair = m.extract_t0_pair(_row())
    assert pair is not None
    with pytest.raises(m.ResolutionContractError, match="before market close"):
        m.join_resolutions(
            [pair],
            [_resolution(resolved_at="2026-10-03T23:29:30Z")],
        )


@pytest.mark.parametrize("outcome, expected", [("UP", 1), ("DOWN", 0)])
def test_valid_5m_resolution_joins_to_same_target(outcome, expected):
    m = _load()
    pair = m.extract_t0_pair(_row())
    assert pair is not None
    joined = m.join_resolutions([pair], [_resolution(outcome=outcome)])
    assert len(joined) == 1
    assert joined[0].label_up == expected
    assert joined[0].pair.market_slug == pair.market_slug


def test_chronological_market_group_split_never_leaks_same_market():
    m = _load()
    pairs = []
    resolutions = []
    base = 1791069900
    for market_i in range(12):
        start = base + market_i * 300
        end = start + 300
        slug = f"btc-updown-5m-{start}"
        cond = f"cond-{market_i}"
        # two observations in the same market on purpose
        for obs_i in range(2):
            ts_epoch = start + 30 + obs_i * 60
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(ts_epoch, timezone.utc).isoformat().replace("+00:00", "Z")
            pair = m.extract_t0_pair(
                _row(
                    prediction_id=market_i * 10 + obs_i,
                    ts=ts,
                    slug=slug,
                    condition_id=cond,
                    start_ts=start,
                    end_ts=end,
                    p_market=0.45 + market_i * 0.01,
                    p_senex=0.40 + market_i * 0.02,
                )
            )
            assert pair is not None
            pairs.append(pair)
        resolved_at = end + 60
        from datetime import datetime, timezone
        resolutions.append(
            _resolution(
                slug=slug,
                condition_id=cond,
                start_ts=start,
                end_ts=end,
                outcome="UP" if market_i % 2 else "DOWN",
                resolved_at=datetime.fromtimestamp(
                    resolved_at, timezone.utc
                ).isoformat().replace("+00:00", "Z"),
            )
        )

    joined = m.join_resolutions(pairs, resolutions)
    train, test = m.chronological_market_split(joined, train_fraction=0.67)

    train_markets = {(x.pair.market_slug, x.pair.condition_id) for x in train}
    test_markets = {(x.pair.market_slug, x.pair.condition_id) for x in test}
    assert train_markets
    assert test_markets
    assert train_markets.isdisjoint(test_markets)
    assert max(x.pair.market_end_ts for x in train) < min(
        x.pair.market_end_ts for x in test
    )


def test_platt_calibration_is_fit_on_train_and_paired_metrics_use_same_rows():
    m = _load()
    joined = []
    for i in range(80):
        p_raw = 0.10 + 0.8 * (i / 79)
        p_market = 0.50
        # deterministic label relation for a stable unit test
        y = 1 if p_raw >= 0.55 else 0
        pair = m.T0Pair(
            prediction_id=i,
            decision_ts=1000 + i,
            market_slug=f"m-{i}",
            condition_id=f"c-{i}",
            market_start_ts=900 + i * 10,
            market_end_ts=1200 + i * 10,
            market_horizon_seconds=300,
            p_market=p_market,
            senex_raw_up=p_raw,
        )
        joined.append(m.JoinedObservation(pair=pair, label_up=y, resolved_at=1300 + i * 10))

    train = joined[:55]
    test = joined[55:]
    calibrator = m.fit_platt(train, max_iter=5000, learning_rate=0.03)
    metrics = m.evaluate_paired(test, calibrator)

    assert metrics["n"] == len(test)
    assert metrics["market_brier"] >= 0.0
    assert metrics["senex_brier"] >= 0.0
    assert math.isfinite(metrics["brier_delta_senex_minus_market"])
    assert metrics["senex_brier"] < metrics["market_brier"]


def _resolved_market_case(
    m,
    market_i,
    *,
    outcome="UP",
    obs_count=1,
    resolved_delay=60,
    p_market=0.55,
    p_senex=0.55,
):
    from datetime import datetime, timezone

    base = 1791069900
    start = base + market_i * 300
    end = start + 300
    slug = f"btc-updown-5m-{start}"
    condition_id = f"cond-{market_i}"
    pairs = []
    for obs_i in range(obs_count):
        ts_epoch = start + 30 + obs_i * 30
        ts = datetime.fromtimestamp(
            ts_epoch, timezone.utc
        ).isoformat().replace("+00:00", "Z")
        pair = m.extract_t0_pair(
            _row(
                prediction_id=market_i * 100 + obs_i,
                ts=ts,
                slug=slug,
                condition_id=condition_id,
                start_ts=start,
                end_ts=end,
                p_market=p_market,
                p_senex=p_senex,
            )
        )
        assert pair is not None
        pairs.append(pair)
    resolved_at = datetime.fromtimestamp(
        end + resolved_delay, timezone.utc
    ).isoformat().replace("+00:00", "Z")
    resolution = _resolution(
        slug=slug,
        condition_id=condition_id,
        start_ts=start,
        end_ts=end,
        outcome=outcome,
        resolved_at=resolved_at,
    )
    return pairs, resolution


@pytest.mark.parametrize(
    "market_count,outcomes",
    [
        (1, ["UP"]),
        (10, ["UP", "DOWN"] * 5),
        (12, ["UP"] * 12),
    ],
)
def test_status_keeps_insufficient_resolution_sets_blocked(market_count, outcomes):
    m = _load()
    pairs = []
    resolutions = []
    for market_i in range(market_count):
        market_pairs, resolution = _resolved_market_case(
            m,
            market_i,
            outcome=outcomes[market_i],
        )
        pairs.extend(market_pairs)
        resolutions.append(resolution)

    status = m.experiment_status(pairs, resolutions)

    assert status["status"] == "BLOCKED_INSUFFICIENT_TARGET_ALIGNED_DATA"
    assert status["edge"] == "UNPROVEN"


def test_status_reports_missing_5m_labels_instead_of_edge():
    m = _load()
    pairs = [m.extract_t0_pair(_row())]
    pairs = [p for p in pairs if p is not None]
    status = m.experiment_status(pairs, [])
    assert status["status"] == "BLOCKED_TARGET_LABEL_5M_NOT_PERSISTED"
    assert status["edge"] == "UNPROVEN"


def test_research_module_has_no_runtime_or_order_side_effects():
    text = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "create_order",
        "send_order",
        "place_order",
        "oracle_runner",
        "gptrader",
        "polymarket_market_adapter",
        "httpx",
        "requests.",
        "websockets",
    )
    for token in forbidden:
        assert token not in text


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _aligned_market_fixture(m, market_count=12, *, senex_raw=0.5, market_prior=0.8):
    predictions = []
    resolutions = []
    base = 1791069900
    from datetime import datetime, timezone

    for market_i in range(market_count):
        start = base + market_i * 300
        end = start + 300
        slug = f"btc-updown-5m-{start}"
        condition_id = f"fixture-cond-{market_i}"
        decision = datetime.fromtimestamp(start + 30, timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        predictions.append(
            _row(
                prediction_id=market_i,
                ts=decision,
                slug=slug,
                condition_id=condition_id,
                start_ts=start,
                end_ts=end,
                p_market=market_prior,
                p_senex=senex_raw,
            )
        )
        resolutions.append(
            _resolution(
                slug=slug,
                condition_id=condition_id,
                start_ts=start,
                end_ts=end,
                outcome="UP" if market_i % 2 else "DOWN",
                resolved_at=datetime.fromtimestamp(
                    end + 1, timezone.utc
                ).isoformat().replace("+00:00", "Z"),
            )
        )
    return predictions, resolutions


@pytest.mark.parametrize("bad_source", [None, "", "   ", 123, [], {}])
def test_resolution_source_must_be_nonempty_string(bad_source):
    m = _load()
    pair = m.extract_t0_pair(_row())
    assert pair is not None
    resolution = _resolution()
    resolution["source"] = bad_source
    with pytest.raises(m.ResolutionContractError, match="source"):
        m.join_resolutions([pair], [resolution])


def test_partial_resolution_corpus_returns_explicit_blocker(tmp_path):
    m = _load()
    predictions_path = tmp_path / "predictions.jsonl"
    resolutions_path = tmp_path / "resolutions.jsonl"
    _write_jsonl(predictions_path, [_row()])
    _write_jsonl(resolutions_path, [_resolution()])

    result = m.run_offline(predictions_path, resolutions_path)

    assert result["status"] == "BLOCKED_INSUFFICIENT_TARGET_ALIGNED_DATA"
    assert result["edge"] == "UNPROVEN"
    assert result["resolved_pairs"] == 1


def test_phase0_inventory_preserves_denominator_in_blocker_output(tmp_path):
    m = _load()
    predictions_path = tmp_path / "predictions.jsonl"
    valid_a = _row(prediction_id=1)
    valid_b = _row(
        prediction_id=2,
        ts="2026-10-03T23:30:30Z",
        slug="btc-updown-5m-1791070200",
        condition_id="cond-2",
        start_ts=1791070200,
        end_ts=1791070500,
    )
    invalid = _row(prediction_id=3)
    invalid["_audit"]["external_markets_v1"]["polymarket"]["eligible_for_prediction"] = False
    _write_jsonl(predictions_path, [valid_a, valid_b, invalid])

    result = m.run_offline(predictions_path)

    assert result["total_rows"] == 3
    assert result["valid_pairs"] == 2
    assert result["rejected_pairs"] == 1
    assert result["unique_markets"] == 2
    assert result["status"] == "BLOCKED_TARGET_LABEL_5M_NOT_PERSISTED"


def test_chronological_split_purges_training_labels_unavailable_at_holdout():
    m = _load()
    base = 1791069900
    observations = []
    for market_i in range(3):
        start = base + market_i * 300
        end = start + 300
        pair = m.T0Pair(
            prediction_id=market_i,
            decision_ts=start + 30,
            market_slug=f"btc-updown-5m-{start}",
            condition_id=f"cond-{market_i}",
            market_start_ts=start,
            market_end_ts=end,
            market_horizon_seconds=300,
            p_market=0.5,
            senex_raw_up=0.5,
        )
        resolved_at = end + 1
        if market_i == 1:
            resolved_at = base + 3 * 300 + 120
        observations.append(
            m.JoinedObservation(
                pair=pair,
                label_up=market_i % 2,
                resolved_at=resolved_at,
            )
        )

    train, holdout = m.chronological_market_split(observations, train_fraction=0.67)

    earliest_holdout_decision = min(item.pair.decision_ts for item in holdout)
    assert train
    assert all(item.resolved_at < earliest_holdout_decision for item in train)
    assert all(item.pair.condition_id != "cond-1" for item in train)


def test_nested_models_compare_market_only_vs_market_plus_senex_on_same_rows(tmp_path):
    m = _load()
    predictions, resolutions = _aligned_market_fixture(
        m,
        market_count=18,
        senex_raw=0.5,
        market_prior=0.8,
    )
    predictions_path = tmp_path / "predictions.jsonl"
    resolutions_path = tmp_path / "resolutions.jsonl"
    _write_jsonl(predictions_path, predictions)
    _write_jsonl(resolutions_path, resolutions)

    result = m.run_offline(predictions_path, resolutions_path, train_fraction=0.67)

    assert result["status"] == "EVALUATED_HOLDOUT"
    assert result["models"]["market_only"]["fit_scope"] == "TRAIN_ONLY"
    assert result["models"]["market_plus_senex"]["fit_scope"] == "TRAIN_ONLY"
    assert result["holdout"]["n"] == result["test_rows"]
    assert result["holdout"]["market_only_brier"] == pytest.approx(
        result["holdout"]["market_plus_senex_brier"], abs=1e-10
    )
    assert result["holdout"]["brier_delta_augmented_minus_market_only"] == pytest.approx(
        0.0, abs=1e-10
    )


def test_constant_senex_cannot_gain_incremental_credit_from_intercept_recalibration(tmp_path):
    m = _load()
    predictions, resolutions = _aligned_market_fixture(
        m,
        market_count=18,
        senex_raw=0.5,
        market_prior=0.85,
    )
    predictions_path = tmp_path / "predictions.jsonl"
    resolutions_path = tmp_path / "resolutions.jsonl"
    _write_jsonl(predictions_path, predictions)
    _write_jsonl(resolutions_path, resolutions)

    result = m.run_offline(predictions_path, resolutions_path, train_fraction=0.67)

    assert result["status"] == "EVALUATED_HOLDOUT"
    assert result["holdout"]["brier_delta_augmented_minus_market_only"] >= -1e-10
    assert result["holdout"]["log_loss_delta_augmented_minus_market_only"] >= -1e-10


def test_constant_non_neutral_senex_feature_has_exactly_zero_incremental_effect():
    m = _load()
    observations = []
    for i in range(12):
        pair = m.T0Pair(
            prediction_id=i,
            decision_ts=1000 + i,
            market_slug=f"constant-senex-{i}",
            condition_id=f"constant-cond-{i}",
            market_start_ts=900 + i * 10,
            market_end_ts=1200 + i * 10,
            market_horizon_seconds=300,
            p_market=0.85,
            senex_raw_up=0.80,
        )
        observations.append(
            m.JoinedObservation(
                pair=pair,
                label_up=1 if i < 8 else 0,
                resolved_at=1300 + i * 10,
            )
        )

    market_only, market_plus_senex = m.fit_incremental_models(observations)
    market_logit = math.log(0.85 / 0.15)
    senex_logit = math.log(0.80 / 0.20)

    assert market_plus_senex.coefficients[1] == pytest.approx(0.0, abs=1e-12)
    assert market_plus_senex.predict(
        market_logit,
        senex_logit,
    ) == pytest.approx(
        market_only.predict(market_logit),
        abs=1e-12,
    )


@pytest.mark.parametrize(
    "slug,start_ts,end_ts",
    [
        ("btc-updown-5m-1791069901", 1791069901, 1791070201),
        ("btc-updown-5m-1791069900", 1791069900.5, 1791070200.5),
    ],
)
def test_t0_market_grid_requires_exact_300_second_epoch_boundary(
    slug,
    start_ts,
    end_ts,
):
    m = _load()
    row = _row(slug=slug, start_ts=start_ts, end_ts=end_ts)
    assert m.extract_t0_pair(row) is None


@pytest.mark.parametrize(
    "slug,start_ts,end_ts,pair_start,pair_end",
    [
        (
            "btc-updown-5m-1791069901",
            1791069901,
            1791070201,
            1791069901,
            1791070201,
        ),
        (
            "btc-updown-5m-1791069900",
            1791069900.5,
            1791070200.5,
            1791069900,
            1791070200,
        ),
    ],
)
def test_resolution_market_grid_rejects_off_boundary_or_fractional_timestamps(
    slug,
    start_ts,
    end_ts,
    pair_start,
    pair_end,
):
    m = _load()
    pair = m.T0Pair(
        prediction_id="grid-contract",
        decision_ts=pair_start + 30,
        market_slug=slug,
        condition_id="cond-grid",
        market_start_ts=pair_start,
        market_end_ts=pair_end,
        market_horizon_seconds=300,
        p_market=0.5,
        senex_raw_up=0.5,
    )
    resolution = _resolution(
        slug=slug,
        condition_id="cond-grid",
        start_ts=start_ts,
        end_ts=end_ts,
    )
    with pytest.raises(m.ResolutionContractError, match="grid"):
        m.join_resolutions([pair], [resolution])


def test_off_boundary_t0_market_grid_is_rejected():
    m = _load()
    row = _row(
        ts=330,
        slug="btc-updown-5m-301",
        condition_id="off-boundary",
        start_ts=301,
        end_ts=601,
    )
    assert m.extract_t0_pair(row) is None


def test_fractional_t0_grid_timestamp_is_not_silently_truncated():
    m = _load()
    row = _row(
        ts=330,
        slug="btc-updown-5m-300",
        condition_id="fractional-grid",
        start_ts=300.5,
        end_ts=600.5,
    )
    assert m.extract_t0_pair(row) is None


def test_off_boundary_or_fractional_resolution_grid_is_rejected():
    m = _load()

    with pytest.raises(m.ResolutionContractError, match="grid"):
        m.join_resolutions(
            [],
            [
                _resolution(
                    slug="btc-updown-5m-301",
                    condition_id="off-boundary",
                    start_ts=301,
                    end_ts=601,
                    resolved_at=700,
                )
            ],
        )

    with pytest.raises(m.ResolutionContractError, match="grid"):
        m.join_resolutions(
            [],
            [
                _resolution(
                    slug="btc-updown-5m-300",
                    condition_id="fractional-grid",
                    start_ts=300.5,
                    end_ts=600.5,
                    resolved_at=700,
                )
            ],
        )

def test_logistic_fitter_converges_on_narrow_probability_features():
    m = _load()
    import random

    rng = random.Random(7)
    observations = []
    features = []
    n = 500
    for i in range(n):
        p = 0.45 + 0.10 * (i / (n - 1))
        x = math.log(p / (1.0 - p))
        target_p = 1.0 / (1.0 + math.exp(-2.0 * x))
        label = 1 if rng.random() < target_p else 0
        pair = m.T0Pair(
            prediction_id=i,
            decision_ts=1000 + i,
            market_slug=f"narrow-{i}",
            condition_id=f"narrow-cond-{i}",
            market_start_ts=900 + i,
            market_end_ts=1200 + i,
            market_horizon_seconds=300,
            p_market=p,
            senex_raw_up=0.5,
        )
        observations.append(
            m.JoinedObservation(
                pair=pair,
                label_up=label,
                resolved_at=1300 + i,
            )
        )
        features.append((x,))

    fitted = m._fit_logistic_features(observations, features)

    # Penalized Newton/IRLS optimum for this deterministic fixture is ~1.50688.
    # Fixed-step GD previously stopped near 0.36 after 4,000 iterations.
    assert fitted.intercept == pytest.approx(0.11297054, abs=1e-5)
    assert fitted.coefficients[0] == pytest.approx(1.50688214, abs=1e-5)


def test_logistic_refinement_is_scale_aware_when_l2_is_zero():
    m = _load()

    observations = []
    features = []
    prediction_id = 0
    scaled = 1e-10

    # Symmetric non-separable fixture with finite optimum:
    # P(y=1 | +scaled) = 0.55 and P(y=1 | -scaled) = 0.45.
    for feature, positives in ((scaled, 55), (-scaled, 45)):
        for index in range(100):
            pair = m.T0Pair(
                prediction_id=prediction_id,
                decision_ts=1000 + prediction_id,
                market_slug=f"scaled-{prediction_id}",
                condition_id=f"scaled-cond-{prediction_id}",
                market_start_ts=900 + prediction_id,
                market_end_ts=1200 + prediction_id,
                market_horizon_seconds=300,
                p_market=0.5,
                senex_raw_up=0.5,
            )
            observations.append(
                m.JoinedObservation(
                    pair=pair,
                    label_up=1 if index < positives else 0,
                    resolved_at=1300 + prediction_id,
                )
            )
            features.append((feature,))
            prediction_id += 1

    fitted = m._fit_logistic_features(
        observations,
        features,
        l2=0.0,
    )

    positive = fitted.predict(scaled)
    negative = fitted.predict(-scaled)

    assert positive == pytest.approx(0.55, abs=1e-6)
    assert negative == pytest.approx(0.45, abs=1e-6)

