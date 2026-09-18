from pathlib import Path

from edge_lab.native_cohort import FreezeManifest, NativeCohortCollector


CODE="code-a"
CONFIG="config-a"
WEIGHTS="weights-a"


def _row(*, row_id=1, ts="2026-09-18T05:00:00Z", prediction="LONG", outcome="WIN",
         exchange="okx", up_prob=0.8123456789, price_now=100.0, price_1h=101.0,
         code=CODE, config=CONFIG, weights=WEIGHTS):
    return {
        "id": row_id,
        "ts": ts,
        "symbol": "BTCUSDT",
        "prediction": prediction,
        "outcome": outcome,
        "exchange_used": exchange,
        "price_now": price_now,
        "audit": {
            "action_vector": {"action": "EXECUTE", "side": prediction},
            "pipeline": {
                "step2_features": {
                    "direction": prediction,
                    "up_prob": up_prob,
                    "missing_input_mask_v1": {
                        "version": "missing-input-mask-v1",
                        "masked_features": ["oi_momentum"],
                        "observed_input_count": 5,
                        "missing_excluded_from_agreement_denominator": True,
                    },
                }
            },
            "decision_replay_v1": {
                "code_hash": code,
                "config_hash": config,
                "effective_weights_hash": weights,
            },
            "outcomes_dual": {
                "outcome_1h": outcome,
                "price_1h_later": price_1h,
                "primary_window": "1h",
                "settlement_contract_version": "aud063-v1",
                "price_evidence_v1": {
                    "1h": {
                        "version": "historical-price-evidence-v1",
                        "source": exchange,
                        "symbol": "BTC/USDT",
                        "window_seconds": 3600,
                        "target_epoch_ms": 1,
                        "candle_open_epoch_ms": 1,
                        "candle_close_epoch_ms": 2,
                        "candle_interval_ms": 60000,
                        "target_offset_from_candle_open_ms": 0,
                        "price": price_1h,
                        "observed_at": "2026-09-18T06:01:00Z",
                        "selection_rule": "ONE_MINUTE_CANDLE_CONTAINING_EXACT_TARGET",
                        "maturity_rule": "OBSERVED_AT_GTE_CANDLE_CLOSE_EPOCH_MS",
                    }
                },
                "settlement_observation_v1": {
                    "version": "settlement-observation-v1",
                    "observed_at": "2026-09-18T06:01:00Z",
                },
            },
        },
    }


def _manifest():
    return FreezeManifest(
        cohort_id="HYP-001C-NATIVE-V1",
        registered_at_utc="2026-09-18T04:30:00Z",
        code_hash=CODE,
        config_hash=CONFIG,
        effective_weights_hash=WEIGHTS,
        feature_availability_policy="missing-input-mask-v1",
        exchange_policy="EXACT_PERSISTED_EXCHANGE_NO_DEFAULT_NO_INFERENCE",
        exchange_used="okx",
    )


def test_prospective_collector_persists_raw_score_and_native_label(tmp_path: Path):
    c = NativeCohortCollector(_manifest(), tmp_path / "cohort.jsonl", proof_validator=lambda _: True)
    raw=0.8123456789
    record=c.collect(_row(up_prob=raw))
    assert record["cohort_status"] == "AUTHORITY_CANDIDATE"
    assert record["raw_up_prob"] == raw
    assert record["raw_up_prob_transformed"] is False
    assert record["action"] == "EXECUTE"
    assert record["direction"] == "LONG"
    assert record["final_prediction"] == "LONG"
    assert record["exchange_used"] == "okx"
    assert record["price_now"] == 100.0
    assert record["price_1h_later"] == 101.0
    assert record["native_y_up"] == 1
    assert record["native_directional_outcome"] == "WIN"
    assert record["settlement_evidence_1h"]["source"] == "okx"
    assert record["authority_candidate"] is True


def test_native_y_up_is_strict_and_tie_maps_to_zero(tmp_path: Path):
    c = NativeCohortCollector(_manifest(), tmp_path / "cohort.jsonl", proof_validator=lambda _: True)
    record=c.collect(_row(price_now=100.0, price_1h=100.0, outcome="LOSS"))
    assert record["native_y_up"] == 0


def test_non_overlap_excludes_second_prospective_row_inside_one_hour(tmp_path: Path):
    c = NativeCohortCollector(_manifest(), tmp_path / "cohort.jsonl", proof_validator=lambda _: True)
    first=c.collect(_row(row_id=1,ts="2026-09-18T05:00:00Z"))
    second=c.collect(_row(row_id=2,ts="2026-09-18T05:30:00Z"))
    third=c.collect(_row(row_id=3,ts="2026-09-18T06:00:00Z"))
    assert first["cohort_status"] == "AUTHORITY_CANDIDATE"
    assert second["cohort_status"] == "EXCLUDED_OVERLAP"
    assert third["cohort_status"] == "AUTHORITY_CANDIDATE"


def test_freeze_mismatch_excludes_prospective_row(tmp_path: Path):
    c = NativeCohortCollector(_manifest(), tmp_path / "cohort.jsonl", proof_validator=lambda _: True)
    record=c.collect(_row(code="changed"))
    assert record["cohort_status"] == "EXCLUDED_FREEZE_MISMATCH"
    assert "CODE_HASH_MISMATCH" in record["exclusion_reasons"]
    assert record["authority_candidate"] is False


def test_historical_row_is_diagnostic_only(tmp_path: Path):
    c = NativeCohortCollector(_manifest(), tmp_path / "cohort.jsonl", proof_validator=lambda _: True)
    record=c.collect(_row(ts="2026-09-18T04:00:00Z"))
    assert record["cohort_status"] == "DIAGNOSTIC_ONLY_HISTORICAL"
    assert record["diagnostic_only"] is True
    assert record["authority_candidate"] is False


def test_exchange_policy_is_frozen(tmp_path: Path):
    c = NativeCohortCollector(_manifest(), tmp_path / "cohort.jsonl", proof_validator=lambda _: True)
    record=c.collect(_row(exchange="kraken"))
    assert record["cohort_status"] == "EXCLUDED_FREEZE_MISMATCH"
    assert "EXCHANGE_POLICY_MISMATCH" in record["exclusion_reasons"]


def test_historical_rows_are_nonoverlap_selected_too(tmp_path: Path):
    c = NativeCohortCollector(_manifest(), tmp_path / "cohort.jsonl", proof_validator=lambda _: True)
    first=c.collect(_row(row_id=10,ts="2026-09-18T03:00:00Z"))
    second=c.collect(_row(row_id=11,ts="2026-09-18T03:30:00Z"))
    third=c.collect(_row(row_id=12,ts="2026-09-18T04:00:00Z"))
    assert first["cohort_status"] == "DIAGNOSTIC_ONLY_HISTORICAL"
    assert second["cohort_status"] == "EXCLUDED_OVERLAP"
    assert third["cohort_status"] == "DIAGNOSTIC_ONLY_HISTORICAL"
