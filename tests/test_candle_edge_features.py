import pytest
from research.edge.candle_economic_challenger.candle_features import extract_features, classify_patterns
from research.edge.candle_economic_challenger.contracts import EvidenceError

def bar(o,h,l,c):
    return {"open":o,"high":h,"low":l,"close":c,"volume":10.0}

def test_geometry_uses_closed_candles_and_zero_range_is_unknown():
    bars=[bar(10,11,9,10.5),bar(10.5,12,10,11.5),
          bar(11.5,13,11,12.5),bar(12.5,14,12,13.0)]
    f=extract_features(bars)
    assert f["body_to_range"]==pytest.approx(.25)
    assert f["upper_wick_to_range"]==pytest.approx(.5)
    assert f["lower_wick_to_range"]==pytest.approx(.25)
    assert f["close_location_value"]==pytest.approx(0.0)  # close at midpoint of [12,14]
    assert f["prior_trend_slope"]>0
    with pytest.raises(EvidenceError,match="zero_range"):
        extract_features(bars[:-1]+[bar(13,13,13,13)])

def test_bullish_and_bearish_harami_fixtures():
    prev=[bar(10,11,9,10),bar(10,11,9,10)]
    bullish=classify_patterns(prev+[bar(11,11.2,7.8,8),bar(8.7,9.5,8.6,9.1)])
    bearish=classify_patterns(prev+[bar(8,11.2,7.8,11),bar(10.2,10.4,9.8,10)])
    assert bullish["bullish_harami"]==1 and bullish["bearish_harami"]==0
    assert bearish["bearish_harami"]==1 and bearish["bullish_harami"]==0

def test_hikkake_setup_only_no_future_confirmation():
    bullish=classify_patterns([bar(10,11,9,10),bar(10,12,8,11),
                               bar(10,11,9,10.5),bar(9.5,10,8.5,9)])
    bearish=classify_patterns([bar(10,11,9,10),bar(10,12,8,11),
                               bar(10,11,9,10.5),bar(10.8,11.5,9.5,11)])
    assert bullish["bullish_hikkake"]==1
    assert bearish["bearish_hikkake"]==1

def test_hanging_man_requires_prior_uptrend_and_long_lower_wick():
    got=classify_patterns([bar(10,11,9,10.5),bar(10.5,12,10,11.5),
                           bar(11.5,13,11,12.5),bar(13,13.05,10,12.9)])
    assert got["hanging_man"]==1
    with pytest.raises(EvidenceError,match="history"): classify_patterns([bar(1,2,.5,1.5)])
