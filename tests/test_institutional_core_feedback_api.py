import pytest

from senecio_polymarket.oracle.institutional_core import SingleDecisionCore


def test_trade_feedback_and_calibration_feedback_have_distinct_apis():
    core = SingleDecisionCore(initial_capital=1000.0)
    original_weight = core.weights["orderflow"]

    decision = {
        "side": "LONG",
        "pipeline": {
            "step2_features": {
                "conviction": 0.5,
                "pressures": {"orderflow": 1.0},
            }
        },
    }

    core.record_trade_outcome(0.01, decision)

    assert core._capital == pytest.approx(1010.0)
    assert core._pnl_history[-1] == pytest.approx(0.01)
    assert core.weights["orderflow"] != original_weight

    core.record_outcome(True)
    assert list(core._calibration_window)[-1] is True
