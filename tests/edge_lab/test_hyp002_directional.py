from edge_lab.hyp002 import analyze_long_short


def _rec(direction, outcome, status):
    return {
        "final_prediction": direction,
        "native_directional_outcome": outcome,
        "cohort_status": status,
    }


def test_hyp002_separates_historical_diagnostic_from_prospective_authority():
    records=[
        _rec("LONG","WIN","DIAGNOSTIC_ONLY_HISTORICAL"),
        _rec("LONG","LOSS","DIAGNOSTIC_ONLY_HISTORICAL"),
        _rec("SHORT","WIN","DIAGNOSTIC_ONLY_HISTORICAL"),
        _rec("LONG","WIN","AUTHORITY_CANDIDATE"),
        _rec("SHORT","LOSS","AUTHORITY_CANDIDATE"),
        _rec("SHORT","WIN","EXCLUDED_OVERLAP"),
    ]
    report=analyze_long_short(records)
    assert report["historical_diagnostic"]["LONG"]["n"] == 2
    assert report["historical_diagnostic"]["LONG"]["win_rate"] == 0.5
    assert report["historical_diagnostic"]["SHORT"]["n"] == 1
    assert report["prospective_authority_candidates"]["LONG"]["n"] == 1
    assert report["prospective_authority_candidates"]["SHORT"]["n"] == 1
    assert report["prospective_authority_candidates"]["SHORT"]["win_rate"] == 0.0
    assert report["excluded_rows"] == 1
    assert report["edge_status"] == "UNPROVEN"
    assert report["brier_logloss_allowed"] is False
