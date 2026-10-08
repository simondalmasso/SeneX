"""ORDER197 AUD F01-F05: independently test scientific fail-closed behavior."""
import copy
from datetime import datetime, timedelta, timezone
import pytest

from research.edge.candle_economic_challenger.walkforward import evaluate_abcd
from research.edge.candle_economic_challenger.contracts import EvidenceError

def cohort(n=70, *, offset=0):
    t=datetime(2026,1,1,tzinfo=timezone.utc)
    data=[]
    for i in range(n):
        ts=t+timedelta(hours=i)
        data.append({"market_id":f"synthetic-{i}","decision_ts":ts.isoformat(),
           "label_end_ts":(ts+timedelta(hours=1)).isoformat(),
           "side":"LONG",
           "entry_reference":100.,
           "exit_reference":100.+(0.22 if i%5 else -0.25)+offset,
           "geometry":{"body_to_range":(i%3)/4,"upper_wick_to_range":.25,
              "lower_wick_to_range":.25,"close_location_value":.5,
              "rolling_range_compression":.8,"prior_trend_slope":.002},
           "patterns":{"bullish_harami":i%4==0,"bearish_harami":i%4==1,
              "bullish_hikkake":False,"bearish_hikkake":False,"hanging_man":False}})
    return data

def test_F01_stress_replays_exact_15bps_actions_no_retraining():
    result=evaluate_abcd(cohort(offset=.06),min_train=20,n_splits=2,n_bootstrap=100,seed=7)
    baseline=result["scenarios"]["15"]
    assert baseline["E"]["n_trades"]>0  # 15bps trains an actionable no-candle baseline
    assert result["scenarios"]["30"]["E"]["net_mean_bps"]<0  # stress hurts, cannot retrain to FLAT
    for c in ("10","20","30"):
        for model in ("A","E","B","C","D"):
            assert result["scenarios"][c][model]["oos_actions"]==baseline[model]["oos_actions"]
    assert result["training_cost_bps"]==15

def test_F02_no_candle_abstention_baseline_and_incrementality():
    result=evaluate_abcd(cohort(),min_train=20,n_splits=2,n_bootstrap=100,seed=7)
    scenario=result["scenarios"]["15"]
    assert set(("A","E","B","C","D","paired_delta_B_vs_E","paired_delta_C_vs_E",
                "paired_delta_D_vs_E","paired_delta_D_vs_B")).issubset(scenario)
    assert len(scenario["E"]["oos_actions"])==result["n_oos"]
    assert result["family_comparisons"]>=7

def test_F03_absolute_ci_and_familywise_promotion_not_pseudopvalue():
    from research.edge.candle_economic_challenger.walkforward import promotion_gate
    result=evaluate_abcd(cohort(),min_train=20,n_splits=2,n_bootstrap=100,seed=7)
    for name in ("B","C","D"):
        value=result["scenarios"]["15"][name]
        assert len(value["absolute_net_ci_adjusted"])==2
    stat=result["scenarios"]["15"]["paired_delta_B"]
    assert "one_sided_p" not in stat and "holm_conservative_significance" not in stat
    assert "familywise_ci_adjusted" in stat
    # Positive paired gain alone cannot promote when absolute net uncertainty crosses zero.
    assert not promotion_gate(absolute_low=-.01,incremental_low=2.,
                              vs_no_candle_low=2.,trades=150,opportunities=400,
                              independent_clusters=400)
    assert promotion_gate(absolute_low=1.,incremental_low=2.,
                              vs_no_candle_low=2.,trades=150,opportunities=400,
                              independent_clusters=400)
def test_F04_forged_claim_and_unanchored_attestation_cannot_promote():
    from research.edge.candle_economic_challenger.custody import verify_exact_byte_attestation
    rows=cohort(360)
    with pytest.raises(EvidenceError,match="custody|anchor|attestation"):
        evaluate_abcd(rows,min_train=20,n_splits=2,n_bootstrap=100,
                      custody_verified=True)
    fake={"version":"candle-custody-v1","dataset_sha256":"0"*64,"rows_sha256":"1"*64}
    result=evaluate_abcd(rows,min_train=20,n_splits=2,n_bootstrap=100,
                         attestation=fake)
    assert result["verdict"]=="BLOCKED_ARTIFACT_BYTES"
    assert verify_exact_byte_attestation(rows,source_bytes={},label_bytes={}) is None

def snapshot(minute):
    from datetime import datetime, timezone
    epoch=int(datetime(2026,10,7,tzinfo=timezone.utc).timestamp()*1000)
    bars=[[epoch+minute*60000+i*900000,100.,101.,99.,100.2,1.] for i in range(16)]
    t=datetime.fromtimestamp((bars[-1][0]+900000)/1000,tz=timezone.utc).isoformat()
    return {"prediction_id":f"m-{minute}","decision_ts":t,
            "venue":"BINANCE","symbol":"BTCUSDT","market_type":"SPOT",
            "source_sha256":"a"*64,"ohlcv":bars,"side":"LONG","regime":"TRENDING"}

def test_F05_minute_side_regime_attrition_without_backfill():
    from research.edge.candle_economic_challenger.coverage import report_alignment_coverage
    snapshots=[snapshot(m) for m in (0,15,30,45)]
    report=report_alignment_coverage(snapshots)
    assert report["total_observed_t0"]==4
    for m in ("00","15","30","45"):
        assert report["by_minute"][m]["total"]==1
    assert report["by_minute"]["00"]["eligible"]==1
    assert sum(report["by_minute"][m]["eligible"] for m in ("15","30","45"))==0
    assert report["by_side"]["LONG"]["total"]==4
    assert report["by_regime"]["TRENDING"]["total"]==4
    assert report["proposed_cohort"]=="PREREGISTERED_HOURLY_ONLY"
    assert report["bars_needed_for_all_offset_15m_closes"]==19
    assert report["backfilled_bars"]==0

def test_F04_original_entry_reference_must_equal_sealed_T0_price(tmp_path,monkeypatch):
    import json,hashlib
    from research.edge.candle_economic_challenger import custody
    from research.edge.candle_economic_challenger.contracts import parse_snapshot
    from research.edge.candle_economic_challenger.candle_features import extract_features,classify_patterns
    start=int(datetime(2026,1,1,tzinfo=timezone.utc).timestamp()*1000)
    bars=[[start+i*900000,100.,101.,99.,100.2,1.] for i in range(16)]
    src={"prediction_id":"px","decision_ts":"2026-01-01T04:00:00Z",
         "venue":"BINANCE","symbol":"BTCUSDT","market_type":"SPOT",
         "source_sha256":"a"*64,"ohlcv":bars,"side":"LONG","price_now":100.}
    parsed=parse_snapshot(src)
    outcome={"prediction_id":"px","decision_ts":"2026-01-01T04:00:00Z",
             "label_end_ts":"2026-01-01T05:00:00Z","venue":"BINANCE",
             "symbol":"BTCUSDT","market_type":"SPOT","entry_reference":101.,
             "exit_reference":102.}
    row={"market_id":"px","decision_ts":src["decision_ts"],
         "label_end_ts":outcome["label_end_ts"],"side":"LONG",
         "entry_reference":101.,"exit_reference":102.,
         "geometry":extract_features(parsed["hourly"]),
         "patterns":classify_patterns(parsed["hourly"])}
    raw_src=json.dumps(src,sort_keys=True).encode()
    raw_label=json.dumps(outcome,sort_keys=True).encode()
    anchor={"version":"candle-custody-trust-anchors-v1",
            "status":"AUD_VERIFIED_PRE_OUTCOME",
            "entries":{"px":{"source_sha256":hashlib.sha256(raw_src).hexdigest(),
                             "label_sha256":hashlib.sha256(raw_label).hexdigest()}}}
    anchorpath=tmp_path/"independently-pinned-test-anchors.json"
    anchorpath.write_text(json.dumps(anchor),encoding="utf-8")
    monkeypatch.setattr(custody,"_ANCHORS",anchorpath)
    with pytest.raises(EvidenceError,match="entry|T0"):
        custody.verify_exact_byte_attestation([row],source_bytes={"px":raw_src},
                                              label_bytes={"px":raw_label})
    outcome["entry_reference"]=100.
    row["entry_reference"]=100.
    raw_label=json.dumps(outcome,sort_keys=True).encode()
    anchor["entries"]["px"]["label_sha256"]=hashlib.sha256(raw_label).hexdigest()
    anchorpath.write_text(json.dumps(anchor),encoding="utf-8")
    attestation=custody.verify_exact_byte_attestation([row],source_bytes={"px":raw_src},
                                                     label_bytes={"px":raw_label})
    assert attestation is not None and len(attestation.source_label_bindings)==1
    with pytest.raises(EvidenceError,match="anchors"):
        custody.verify_exact_byte_attestation([row],source_bytes={"px":raw_src},
                                              label_bytes={"px":raw_label+b" "})

def test_P2_report_nonoverlapping_horizons_not_just_hour_buckets():
    records=cohort(80)
    origin=datetime(2026,1,1,tzinfo=timezone.utc)
    for i,row in enumerate(records):
        ts=origin+timedelta(minutes=i*15)
        row["decision_ts"]=ts.isoformat()
        row["label_end_ts"]=(ts+timedelta(hours=1)).isoformat()
    report=evaluate_abcd(records,min_train=20,n_splits=2,n_bootstrap=100)
    dep=report["dependence"]
    assert dep["nominal_unique_t0"]==report["n_oos"]
    assert dep["nonoverlapping_label_windows"]<dep["nominal_unique_t0"]
    assert dep["nonoverlapping_label_windows"]<=((dep["nominal_unique_t0"]+3)//4)+1

def test_F02_synthetic_geometry_incremental_over_E_and_D_vs_B():
    from research.edge.candle_economic_challenger.walkforward import evaluate_abcd
    data=cohort()
    for i,row in enumerate(data):
        # Artificial labels intentionally correlate with one geometry partition.
        row["exit_reference"]=101. if i%3==2 else 99.6
    output=evaluate_abcd(data,min_train=20,n_splits=2,n_bootstrap=100,seed=7)
    scenario=output["scenarios"]["15"]
    assert scenario["E"]["net_mean_bps"]==pytest.approx(0.0)
    assert scenario["B"]["net_mean_bps"]>scenario["E"]["net_mean_bps"]
    assert scenario["paired_delta_B_vs_E"]["mean_net_bps"]>0
    assert scenario["paired_delta_D_vs_B"]["mean_net_bps"]==pytest.approx(0.0)
    assert output["scientific_verdict"]=="BLOCKED_ARTIFACT_BYTES"
