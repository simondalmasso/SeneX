from edge_lab.hyp008_shadow import (
    hyp008_design,
    is_exact_clock_hour,
    polymarket_hourly_label,
)


def test_hyp008_preserves_polymarket_tie_up_semantics():
    assert polymarket_hourly_label(100.0, 101.0) == "UP"
    assert polymarket_hourly_label(100.0, 100.0) == "UP"
    assert polymarket_hourly_label(100.0, 99.0) == "DOWN"


def test_hyp008_requires_exact_clock_hour_boundary():
    assert is_exact_clock_hour("2026-09-18T05:00:00Z") is True
    assert is_exact_clock_hour("2026-09-18T05:00:01Z") is False
    assert is_exact_clock_hour("2026-09-18T05:15:00Z") is False


def test_hyp008_is_design_only_and_core_read_only():
    spec=hyp008_design()
    assert spec["status"] == "DESIGNED_NOT_RUN"
    assert spec["source"] == "BINANCE_BTCUSDT"
    assert spec["candle"] == "FINALIZED_1H"
    assert spec["tie_semantics"] == "UP_ON_EQUAL"
    assert spec["core_mutations"] == 0
    assert spec["edge_status"] == "UNPROVEN"
    assert spec["signal_capture_time"] == "EXACT_CLOCK_HOUR_BOUNDARY"
