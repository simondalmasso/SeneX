from senecio_polymarket.oracle.institutional_core import SingleDecisionCore


def _risk_filter():
    return {
        "risk_score": 0.0,
        "size_multiplier": 1.0,
        "verdict": "ALLOW",
    }


def _market_state():
    return {
        "volatility": 0.02,
        "price": 100.0,
        "symbol": "BTCUSDT",
        "timeframe": "15m",
    }


def _raw_features():
    return {
        "conviction": 0.80,
        "noise": 0.10,
        "direction": "LONG",
        "up_prob": 0.82,
        "down_prob": 0.18,
        "probability_semantics": "UNVALIDATED",
    }


def test_compute_ev_fails_closed_on_unvalidated_win_probability():
    core = SingleDecisionCore()
    result = core.compute_ev(
        _raw_features(),
        _risk_filter(),
        _market_state(),
        slippage_bps=0.0,
    )

    assert result["tradeable"] is False
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "UNVALIDATED_WIN_PROBABILITY"
    assert result["p_win"] is None
    assert result["probability_semantics"] == "UNVALIDATED"


def test_compute_ev_uses_only_explicit_oos_calibrated_probability():
    core = SingleDecisionCore()
    features = _raw_features()
    features.update(
        {
            "p_win_calibrated": 0.70,
            "p_win_calibrated_provenance": {
                "probability_semantics": "VALIDATED_OOS_PROBABILITY",
                "method": "PLATT_V1",
                "artifact_sha256": "a" * 64,
            },
        }
    )

    result = core.compute_ev(
        features,
        _risk_filter(),
        _market_state(),
        slippage_bps=0.0,
    )

    assert result["status"] == "OK"
    assert result["reason"] == "CALIBRATED_WIN_PROBABILITY"
    assert result["p_win"] == 0.70
    assert result["p_win_source"] == "p_win_calibrated"
    assert result["probability_semantics"] == "VALIDATED_OOS_PROBABILITY"


def test_compute_ev_rejects_calibrated_probability_without_valid_provenance():
    core = SingleDecisionCore()
    features = _raw_features()
    features.update(
        {
            "p_win_calibrated": 0.70,
            "p_win_calibrated_provenance": {
                "probability_semantics": "UNVALIDATED",
                "method": "PLATT_V1",
                "artifact_sha256": "a" * 64,
            },
        }
    )

    result = core.compute_ev(
        features,
        _risk_filter(),
        _market_state(),
        slippage_bps=0.0,
    )

    assert result["tradeable"] is False
    assert result["reason"] == "UNVALIDATED_WIN_PROBABILITY"
