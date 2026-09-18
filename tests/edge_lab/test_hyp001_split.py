from datetime import datetime, timezone

from edge_lab.equivalence import (
    PolymarketHourlyContract,
    SenexHourlyContract,
    compare_exact_hourly_equivalence,
)
from edge_lab.up_prob_audit import audit_up_prob_semantics


def test_hyp001a_rejects_current_senex_vs_polymarket_hourly_contract():
    poly = PolymarketHourlyContract(
        asset="BTC/USDT",
        start_ts="2026-09-18T05:00:00Z",
        end_ts="2026-09-18T06:00:00Z",
        timezone="America/New_York",
        resolution="1H_CANDLE_CLOSE_VS_OPEN",
        data_source="binance",
        tie_semantics="UP_ON_EQUAL",
    )
    senex = SenexHourlyContract(
        asset="BTC/USDT",
        start_ts="2026-09-18T05:00:00Z",
        end_ts="2026-09-18T06:00:00Z",
        timezone="UTC",
        resolution="T_PLUS_3600_PRICE_VS_PREDICTION_PRICE",
        data_source="okx",
        tie_semantics="NEITHER_DIRECTION_WINS_ON_EQUAL",
    )

    result = compare_exact_hourly_equivalence(poly, senex)

    assert result.verdict == "NOT_EQUIVALENT"
    assert "DATA_SOURCE_MISMATCH" in result.reasons
    assert "RESOLUTION_SEMANTICS_MISMATCH" in result.reasons
    assert "TIE_SEMANTICS_MISMATCH" in result.reasons
    assert result.directional_baseline_allowed is False


def test_hyp001a_rejects_clock_anchor_mismatch_even_if_other_fields_match():
    poly = PolymarketHourlyContract(
        asset="BTC/USDT",
        start_ts="2026-09-18T05:00:00Z",
        end_ts="2026-09-18T06:00:00Z",
        timezone="America/New_York",
        resolution="1H_CANDLE_CLOSE_VS_OPEN",
        data_source="binance",
        tie_semantics="UP_ON_EQUAL",
    )
    senex = SenexHourlyContract(
        asset="BTC/USDT",
        start_ts="2026-09-18T05:15:00Z",
        end_ts="2026-09-18T06:15:00Z",
        timezone="UTC",
        resolution="1H_CANDLE_CLOSE_VS_OPEN",
        data_source="binance",
        tie_semantics="UP_ON_EQUAL",
    )

    result = compare_exact_hourly_equivalence(poly, senex)

    assert result.verdict == "NOT_EQUIVALENT"
    assert "START_TIMESTAMP_MISMATCH" in result.reasons
    assert "END_TIMESTAMP_MISMATCH" in result.reasons


def test_hyp001a_timezone_labels_do_not_block_equal_utc_instants():
    poly = PolymarketHourlyContract(
        asset="BTC/USDT",
        start_ts="2026-09-18T01:00:00-04:00",
        end_ts="2026-09-18T02:00:00-04:00",
        timezone="America/New_York",
        resolution="SAME",
        data_source="same",
        tie_semantics="same",
    )
    senex = SenexHourlyContract(
        asset="BTC/USDT",
        start_ts="2026-09-18T05:00:00Z",
        end_ts="2026-09-18T06:00:00Z",
        timezone="UTC",
        resolution="SAME",
        data_source="same",
        tie_semantics="same",
    )
    assert compare_exact_hourly_equivalence(poly, senex).verdict == "EQUIVALENT"


def test_hyp001b_up_prob_is_score_not_calibrated_probability():
    audit = audit_up_prob_semantics()

    assert audit.formula == "sigmoid(5 * total_pressure)"
    assert audit.down_relation == "down_prob = 1 - up_prob"
    assert audit.raw_semantics == "LOGISTIC_SQUASHED_ENGINEERED_PRESSURE_SCORE"
    assert audit.calibrated_probability is False
    assert audit.prospectively_calibratable is True
    assert audit.retroactive_calibration_allowed is False
    assert audit.brier_logloss_allowed_now is False
    assert audit.recommended_mapping == "PLATT_ON_LOGIT_UP_PROB"
    assert audit.required_validation == "DISJOINT_FORWARD_VALIDATION_COHORT"
