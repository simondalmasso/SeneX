from datetime import datetime, timedelta, timezone
import pytest
from research.edge.candle_economic_challenger.walkforward import purged_splits, evaluate_abcd
from research.edge.candle_economic_challenger.contracts import EvidenceError

def rows(n=64):
    t=datetime(2026,1,1,tzinfo=timezone.utc)
    result=[]
    for i in range(n):
        ts=t+timedelta(hours=i)
        result.append({"market_id":f"m-{i}","decision_ts":ts.isoformat(),
        "label_end_ts":(ts+timedelta(hours=1)).isoformat(),
        "side":"LONG" if i%2 else "SHORT",
        "entry_reference":100.,"exit_reference":100.2 if i%2 else 99.8,
        "geometry":{"body_to_range":float(i%3)/4,
        "upper_wick_to_range":.25,"lower_wick_to_range":.25,
        "close_location_value":.5,"rolling_range_compression":.8,
        "prior_trend_slope":.002},
        "patterns":{"bullish_harami":i%4==0,"bearish_harami":i%4==1,
        "bullish_hikkake":False,"bearish_hikkake":False,"hanging_man":False}})
    return result

def test_purged_walk_forward_never_uses_label_overlap():
    data=rows(); folds=purged_splits(data,n_splits=3,min_train=20,embargo_seconds=3600)
    assert len(folds)==3
    for train,test in folds:
        cutoff=datetime.fromisoformat(data[test[0]]["decision_ts"])-timedelta(seconds=3600)
        assert all(datetime.fromisoformat(data[i]["label_end_ts"])<cutoff for i in train)
        assert set(train).isdisjoint(test)

def test_overlap_duplicate_and_unsorted_rows_fail_closed():
    data=rows()
    for bad in [data+[data[0]],list(reversed(data))]:
        with pytest.raises(EvidenceError):purged_splits(bad,n_splits=3,min_train=20)

def test_abcd_same_oos_rows_cost_scenarios_and_honest_small_n():
    result=evaluate_abcd(rows(),min_train=20,n_splits=2,n_bootstrap=100,seed=7)
    assert set(result["scenarios"])=={"10","15","20","30"}
    assert result["n_oos"]>0 and result["verdict"]=="INSUFFICIENT_EVIDENCE"
    assert result["experimental_only"] is True
    for cost,scenario in result["scenarios"].items():
        assert set(scenario)=={"A","B","C","D","paired_delta_B","paired_delta_C","paired_delta_D"}
        assert all(scenario[k]["n_opportunities"]==result["n_oos"] for k in ("A","B","C","D"))
    assert evaluate_abcd(rows(),min_train=20,n_splits=2,n_bootstrap=100,seed=7)==result

def test_cannot_claim_edge_from_missing_original_data():
    result=evaluate_abcd([],min_train=20,n_splits=2)
    assert result["verdict"]=="BLOCKED_ARTIFACT_BYTES"
    assert result["scenarios"]=={}
