from senecio_polymarket.backend.flat_reason import classify_flat_reason


def _flat(*, reason="", direction=None, suppressed=False, availability=None):
    return {
        "prediction": "FLAT",
        "audit": {
            "action_vector": {"reason": reason},
            "pipeline": {
                "step1_market": {"feature_availability_v1": availability or {}},
                "step2_features": {"direction": direction, "long_suppressed_by_regime": suppressed},
            },
        },
    }


def test_flat_reason_semantics_are_stable() -> None:
    assert classify_flat_reason({"prediction": "LONG"}) == "DIRECTIONAL_EXECUTE"
    assert classify_flat_reason(_flat(suppressed=True)) == "LONG_BEAR_SUPPRESSION"
    assert classify_flat_reason(_flat(direction="NEUTRAL")) == "NO_DIRECTION/NEUTRAL"
    assert classify_flat_reason(_flat(reason="low_conviction")) == "LOW_CONVICTION_GATE"
    assert classify_flat_reason(_flat(reason="negative_ev")) == "NEGATIVE_OR_INSUFFICIENT_EV"
    assert classify_flat_reason(_flat(availability={"orderflow": {"status": "SOURCE_ERROR"}})) == "MISSING_INPUT/DEGRADED_SOURCE"
    assert classify_flat_reason(_flat(reason="unclassified")) == "OTHER_EXPLICIT_REASON"
