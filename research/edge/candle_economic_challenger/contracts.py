"""Causal, decision-time-only 15m OHLCV custody validation. No network I/O."""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, timezone

class EvidenceError(ValueError):
    """Ineligible scientific evidence; never silently fill missing bars."""

def digest(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()

def utc(raw):
    if not isinstance(raw,str) or not raw or not (raw.endswith("Z") or "+" in raw[10:] or "-" in raw[10:]):
        raise EvidenceError("timezone-aware UTC timestamp required")
    try:
        value=datetime.fromisoformat(raw.replace("Z","+00:00"))
    except ValueError as exc:
        raise EvidenceError("invalid timestamp") from exc
    if value.tzinfo is None:
        raise EvidenceError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc)

def parse_snapshot(snapshot):
    """Require original attested 16x closed 15m OHLCV bars and one exact T0."""
    if not isinstance(snapshot,dict):
        raise EvidenceError("snapshot must be object")
    if not all(str(snapshot.get(k) or "").strip() for k in ("prediction_id","venue","symbol","market_type")):
        raise EvidenceError("missing source identity")
    provenance=str(snapshot.get("source_sha256") or "")
    if len(provenance)!=64 or any(c not in "0123456789abcdef" for c in provenance):
        raise EvidenceError("invalid source custody digest")
    t0=utc(snapshot.get("decision_ts"))
    bars=snapshot.get("ohlcv")
    if not isinstance(bars,list) or len(bars)!=16:
        raise EvidenceError("history must have exactly 16 consecutive 15m bars")
    validated=[]
    for row in bars:
        if not isinstance(row,(list,tuple)) or len(row)<6:
            raise EvidenceError("malformed OHLCV row")
        raw_ts=row[0]
        if isinstance(raw_ts,bool) or not isinstance(raw_ts,int):
            raise EvidenceError("bar open timestamp must be integer milliseconds")
        try:
            o,h,l,c,v=map(float,row[1:6])
        except (TypeError,ValueError) as exc:
            raise EvidenceError("non-numeric OHLCV") from exc
        if not all(map(math.isfinite,(o,h,l,c,v))) or l<=0 or v<0 or h<max(o,c,l) or l>min(o,c) or o<=0 or c<=0:
            raise EvidenceError("invalid OHLCV ranges")
        if raw_ts%900000:
            raise EvidenceError("15m UTC alignment violation")
        if validated and raw_ts!=validated[-1]["open_ms"]+900000:
            raise EvidenceError("duplicate, gap, or reversed 15m bar")
        close_ms=raw_ts+900000
        if len(row)>=7 and row[6] is not None and row[6]!=close_ms:
            raise EvidenceError("explicit close timestamp conflicts with 15m boundary")
        if close_ms>int(t0.timestamp()*1000):
            raise EvidenceError("post T0 or incomplete candle")
        validated.append({"open_ms":raw_ts,"close_ms":close_ms,"open":o,"high":h,"low":l,"close":c,"volume":v})
    if validated[0]["open_ms"]%3600000:
        raise EvidenceError("hourly aggregation boundary misaligned")
    hours=[]
    for i in range(0,16,4):
        block=validated[i:i+4]
        hours.append({"open":block[0]["open"],"high":max(b["high"] for b in block),
                      "low":min(b["low"] for b in block),"close":block[-1]["close"],
                      "volume":sum(b["volume"] for b in block),"open_ms":block[0]["open_ms"],
                      "close_ms":block[-1]["close_ms"]})
    last=datetime.fromtimestamp(validated[-1]["close_ms"]/1000,tz=timezone.utc).isoformat().replace("+00:00","Z")
    return {"prediction_id":str(snapshot["prediction_id"]),"source_sha256":provenance,
            "snapshot_sha256":digest(snapshot),"hourly":hours,"last_close_ts":last,
            "decision_ts":t0.isoformat().replace("+00:00","Z"),
            "venue":snapshot["venue"],"symbol":snapshot["symbol"],"market_type":snapshot["market_type"]}
