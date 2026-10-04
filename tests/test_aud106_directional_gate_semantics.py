from senecio_polymarket.backend import authoritative_score as score


def test_directional_gate_pass_is_operational_not_edge_evidence():
    gate = score._gate(
        {"verified": 30, "win_rate_pct": 60.0},
        min_n=30,
        threshold_pct=55.0,
    )

    assert gate["pass"] is True
    assert gate["gate_semantics"] == "OPERATIONAL_POINT_ESTIMATE"
    assert gate["uncertainty_adjusted"] is False
    assert gate["edge_evidence"] is False
    assert gate["statistical_evidence_gate"] == "quality.gates.wilson_lower_95"


def test_authoritative_score_exposes_directional_gate_semantics_top_level():
    report = score.build_authoritative_score([], symbol="BTCUSDT")

    assert report["directional_gate_semantics"] == "OPERATIONAL_POINT_ESTIMATE"
    assert report["directional_gates_are_edge_evidence"] is False
    assert report["statistical_edge_evidence_gate"] == "quality.gates.wilson_lower_95"
    assert report["trade_mode"] == "PAPER"
    assert report["orders_enabled"] is False
    assert report["live_capital_locked"] is True
