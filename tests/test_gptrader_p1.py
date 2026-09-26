from unittest.mock import patch

from senecio_polymarket.backend.gptrader import (
    BaselinePolicy,
    DEFAULT_CONFIDENCE_THRESHOLD,
    RAW_SCORE_SEMANTICS,
    evaluate_baseline,
    hour_cluster_id,
    sample_gate,
    summarize_resolved_sample,
)


def packet(direction="LONG", confidence=0.60, ev=0.01, ts="2026-09-26T12:15:00+00:00", symbol="BTCUSDT"):
    return {
        "packet_id": f"p-{symbol}-{ts}",
        "timestamp": ts,
        "symbol": symbol,
        "prediction": direction,
        "confidence": confidence,
        "ev": ev,
        "_audit": {"pipeline": {"step2_features": {"up_prob": 0.63}}},
    }


def test_same_packet_same_policy_is_deterministic():
    first = evaluate_baseline(packet(), BaselinePolicy.FOLLOW_ALL_DIRECTIONAL)
    second = evaluate_baseline(packet(), BaselinePolicy.FOLLOW_ALL_DIRECTIONAL)
    assert first == second
    assert first.action == "TAKE"
    assert first.senex_direction == "LONG"


def test_always_abstain_and_flat_handling():
    directional = evaluate_baseline(packet(), BaselinePolicy.ALWAYS_ABSTAIN)
    flat = evaluate_baseline(packet(direction="FLAT"), BaselinePolicy.FOLLOW_ALL_DIRECTIONAL)
    assert directional.action == "ABSTAIN" and directional.eligibility is True
    assert flat.action == "ABSTAIN" and flat.eligibility is False
    assert flat.reason_codes == ("NON_DIRECTIONAL_PACKET",)


def test_fixed_confidence_threshold_boundary_is_inclusive():
    at = evaluate_baseline(packet(confidence=DEFAULT_CONFIDENCE_THRESHOLD), BaselinePolicy.FIXED_CONFIDENCE_THRESHOLD)
    below = evaluate_baseline(packet(confidence=DEFAULT_CONFIDENCE_THRESHOLD - 1e-9), BaselinePolicy.FIXED_CONFIDENCE_THRESHOLD)
    assert at.action == "TAKE"
    assert below.action == "ABSTAIN"


def test_ev_sign_boundary_requires_strict_positive_score():
    positive = evaluate_baseline(packet(ev=1e-12), BaselinePolicy.EV_SIGN)
    zero = evaluate_baseline(packet(ev=0.0), BaselinePolicy.EV_SIGN)
    negative = evaluate_baseline(packet(ev=-0.1), BaselinePolicy.EV_SIGN)
    assert positive.action == "TAKE"
    assert zero.action == negative.action == "ABSTAIN"


def test_raw_score_semantics_are_explicitly_uncalibrated():
    decision = evaluate_baseline(packet(), BaselinePolicy.FOLLOW_ALL_DIRECTIONAL)
    assert decision.score_semantics["confidence"] == RAW_SCORE_SEMANTICS
    assert decision.score_semantics["up_prob"] == RAW_SCORE_SEMANTICS
    assert "PROBABILITY" not in decision.score_semantics["confidence"]


def test_baseline_has_no_network_dependency():
    with patch("socket.socket", side_effect=AssertionError("network forbidden")):
        assert evaluate_baseline(packet(), BaselinePolicy.FOLLOW_ALL_DIRECTIONAL).action == "TAKE"


def test_non_overlapping_hour_cluster_and_same_hour_cross_symbol_relation():
    btc = packet(ts="2026-09-26T12:01:00+00:00", symbol="BTCUSDT")
    eth = packet(ts="2026-09-26T12:59:59+00:00", symbol="ETHUSDT")
    next_hour = packet(ts="2026-09-26T13:00:00+00:00", symbol="BTCUSDT")
    assert hour_cluster_id(btc["timestamp"]) == hour_cluster_id(eth["timestamp"])
    assert hour_cluster_id(btc["timestamp"]) != hour_cluster_id(next_hour["timestamp"])


def test_raw_resolved_rows_do_not_inflate_independent_n():
    rows = [
        {"timestamp": "2026-09-26T12:01:00Z", "symbol": "BTCUSDT", "resolved": True},
        {"timestamp": "2026-09-26T12:15:00Z", "symbol": "BTCUSDT", "resolved": True},
        {"timestamp": "2026-09-26T12:30:00Z", "symbol": "ETHUSDT", "resolved": True},
        {"timestamp": "2026-09-26T13:00:00Z", "symbol": "BTCUSDT", "resolved": True},
        {"timestamp": "2026-09-26T14:00:00Z", "symbol": "BTCUSDT", "resolved": False},
    ]
    summary = summarize_resolved_sample(rows)
    assert summary.raw_resolved_rows == 4
    assert summary.independent_1h == 2
    assert summary.calendar_days == 1


def test_sample_gate_exact_boundaries_and_failures():
    assert sample_gate(600, 14).passed is True
    assert sample_gate(600, 14).verdict == "GATE_OPEN"
    assert sample_gate(599, 14).verdict == "INSUFFICIENT_DATA"
    assert sample_gate(600, 13).verdict == "INSUFFICIENT_DATA"
