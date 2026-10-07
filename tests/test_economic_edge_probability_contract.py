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


def test_market_friction_layer_consumes_calibrated_model_ev_without_recomputing_probability():
    class FakeMarketEV:
        def __init__(self):
            self.model_ev = None

        def compute_market_ev(self, **kwargs):
            self.model_ev = kwargs["model_ev"]
            return {"market_ev": kwargs["model_ev"]}

    core = SingleDecisionCore(min_ev_to_trade=0.001)
    fake = FakeMarketEV()
    core.market_ev = fake
    features = _raw_features()
    features.update(
        {
            "p_win_calibrated": 0.90,
            "p_win_calibrated_provenance": {
                "probability_semantics": "VALIDATED_OOS_PROBABILITY",
                "method": "PLATT_V1",
                "artifact_sha256": "c" * 64,
            },
        }
    )

    result = core.compute_ev(
        features,
        _risk_filter(),
        _market_state(),
        slippage_bps=0.0,
    )

    expected_model_ev = (0.90 * 0.024) - (0.10 * 0.016) - 0.0004
    assert abs(fake.model_ev - expected_model_ev) < 1e-12
    assert abs(result["adjusted_ev"] - expected_model_ev) < 1e-12


def test_hold_reason_propagates_unvalidated_probability_gate():
    core = SingleDecisionCore(min_confidence=0.40)
    features = _raw_features()
    ev_result = core.compute_ev(
        features,
        _risk_filter(),
        _market_state(),
        slippage_bps=0.0,
    )
    feasibility = core.check_execution_feasibility(
        ev_result,
        {
            "liquidity_quality": 1.0,
            "slippage_bps": 0.0,
            "latency_ms": 100.0,
            "spread_bps": 1.0,
        },
    )

    action = core.produce_action(
        features,
        _risk_filter(),
        ev_result,
        feasibility,
        {
            **_market_state(),
            "liquidity_quality": 1.0,
        },
    )

    assert action["action"] == "HOLD"
    assert action["reason"] == "UNVALIDATED_WIN_PROBABILITY"


def test_probability_semantics_cannot_be_spoofed_without_calibration_artifact():
    core = SingleDecisionCore()
    features = _raw_features()
    features["probability_semantics"] = "VALIDATED_OOS_PROBABILITY"

    result = core.compute_ev(
        features,
        _risk_filter(),
        _market_state(),
        slippage_bps=0.0,
    )

    assert result["tradeable"] is False
    assert result["reason"] == "UNVALIDATED_WIN_PROBABILITY"
    assert result["probability_semantics"] == "UNVALIDATED"


def test_boolean_calibrated_probability_is_rejected():
    core = SingleDecisionCore()
    features = _raw_features()
    features.update(
        {
            "p_win_calibrated": True,
            "p_win_calibrated_provenance": {
                "probability_semantics": "VALIDATED_OOS_PROBABILITY",
                "method": "PLATT_V1",
                "artifact_sha256": "d" * 64,
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
