from __future__ import annotations

import importlib.util
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
