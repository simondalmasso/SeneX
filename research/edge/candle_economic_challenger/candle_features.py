"""Minimal preregistered closed-candle features; not TA-Lib equivalent."""
from __future__ import annotations
import math
from .contracts import EvidenceError

GEOMETRY=("body_to_range","upper_wick_to_range","lower_wick_to_range",
          "close_location_value","rolling_range_compression","prior_trend_slope")
PATTERNS=("bullish_harami","bearish_harami","bullish_hikkake","bearish_hikkake","hanging_man")

def _validated(bars):
    if not isinstance(bars,(tuple,list)) or len(bars)<4:
        raise EvidenceError("history insufficient for candle features")
    for b in bars:
        if not isinstance(b,dict) or any(k not in b for k in ("open","high","low","close")):
            raise EvidenceError("incomplete hourly bar")
        try: o,h,l,c=(float(b[k]) for k in ("open","high","low","close"))
        except (TypeError,ValueError) as exc: raise EvidenceError("invalid candle price") from exc
        if not all(map(math.isfinite,(o,h,l,c))) or l<=0 or h<max(o,c) or l>min(o,c):
            raise EvidenceError("invalid hourly bar")
        if h==l: raise EvidenceError("zero_range is unknown, not zero pattern")
    return bars

def extract_features(hourly):
    b=_validated(hourly); last=b[-1]
    o,h,l,c=(float(last[k]) for k in ("open","high","low","close"))
    rng=h-l
    previous=[float(x["high"])-float(x["low"]) for x in b[-4:-1]]
    avg=sum(previous)/len(previous)
    if avg<=0: raise EvidenceError("zero_range in rolling reference")
    p0,p1=(float(b[-4]["close"]),float(b[-2]["close"]))
    return {"body_to_range":abs(c-o)/rng,
            "upper_wick_to_range":(h-max(c,o))/rng,
            "lower_wick_to_range":(min(c,o)-l)/rng,
            "close_location_value":(2*c-h-l)/rng,
            "rolling_range_compression":rng/avg,
            "prior_trend_slope":(p1/p0-1.0)/2.0}

def classify_patterns(hourly):
    b=_validated(hourly); a,prev,now=b[-3],b[-2],b[-1]
    op_,cp=(float(prev[k]) for k in ("open","close"))
    on,cn=(float(now[k]) for k in ("open","close"))
    previous_body=abs(cp-op_); present_body=abs(cn-on)
    contained=min(on,cn)>=min(op_,cp) and max(on,cn)<=max(op_,cp)
    harami=contained and previous_body>present_body*1.5 and present_body>0
    # Hikkake setup at this exact T0 only: mother[-3], inside[-2], trap[-1].
    inside=float(prev["high"])<float(a["high"]) and float(prev["low"])>float(a["low"])
    bullish_hikkake=inside and float(now["low"])<float(prev["low"]) and float(now["high"])<float(prev["high"])
    bearish_hikkake=inside and float(now["high"])>float(prev["high"]) and float(now["low"])>float(prev["low"])
    uptrend=float(b[-4]["close"])<float(b[-3]["close"])<float(b[-2]["close"])
    rng=float(now["high"])-float(now["low"])
    lower=min(on,cn)-float(now["low"]); upper=float(now["high"])-max(on,cn)
    hang=uptrend and present_body>0 and present_body<=rng*.35 and lower>=2*present_body and upper<=present_body
    return {"bullish_harami":int(harami and cp<op_ and cn>on),
            "bearish_harami":int(harami and cp>op_ and cn<on),
            "bullish_hikkake":int(bullish_hikkake),
            "bearish_hikkake":int(bearish_hikkake),"hanging_man":int(hang)}
