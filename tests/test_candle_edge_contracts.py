from datetime import datetime, timezone
import pytest
from research.edge.candle_economic_challenger.contracts import parse_snapshot, EvidenceError

START = 1791331200000  # 2026-10-07 00:00 UTC
def sample():
    bars = [[START + 900000 * i, 100.0+i*.1, 101+i*.1, 99+i*.1, 100.2+i*.1, 5.0] for i in range(16)]
    return {"prediction_id": "p1", "decision_ts": "2026-10-07T04:00:00Z",
            "venue":"BINANCE", "symbol":"BTCUSDT", "market_type":"SPOT",
            "source_sha256":"a"*64, "ohlcv":bars}

def test_exactly_four_completed_hours_and_deterministic_hash():
    a=parse_snapshot(sample()); b=parse_snapshot(sample())
    assert len(a["hourly"])==4 and a["snapshot_sha256"]==b["snapshot_sha256"]
    assert a["last_close_ts"]=="2026-10-07T04:00:00Z"
    assert a["hourly"][-1]["close"]==pytest.approx(101.7)

@pytest.mark.parametrize("mutation",[
    lambda s: s["ohlcv"].pop(),
    lambda s: s["ohlcv"].__setitem__(3,s["ohlcv"][2]),
    lambda s: s["ohlcv"][3].__setitem__(0,s["ohlcv"][3][0]+300000),
    lambda s: s["ohlcv"][2].__setitem__(2,0.0),
    lambda s: s["ohlcv"].reverse(),
    lambda s: s.update(decision_ts="2026-10-07T03:59:59Z"),
    lambda s: s.update(decision_ts="2026-10-07T04:00:00"),
    lambda s: s.update(venue=""),
    lambda s: s.update(source_sha256="not-a-digest"),
])
def test_fails_closed_on_bad_causal_candle(mutation):
    s=sample(); mutation(s)
    with pytest.raises(EvidenceError): parse_snapshot(s)

def test_unknown_is_not_pattern_absence_when_missing_history():
    s=sample(); s["ohlcv"]=s["ohlcv"][:12]
    with pytest.raises(EvidenceError, match="history"): parse_snapshot(s)

def test_durable_receipt_is_plain_utf8_json_and_hash_bound():
    from pathlib import Path
    import hashlib, json
    project=Path(__file__).resolve().parents[1]/"research"/"edge"/"candle_economic_challenger"
    raw=(project/"ORDER197_RECEIPT.json").read_bytes()
    assert not raw.startswith(b"\\xef\\xbb\\xbf")
    receipt=json.loads(raw.decode("utf-8"))
    for field,name in (
        ("feature_extractor_sha256","candle_features.py"),
        ("config_sha256","FEATURE_SCHEMA_V1.json"),
        ("cost_model_sha256","economic_labels.py"),
    ):
        assert hashlib.sha256((project/name).read_bytes()).hexdigest()==receipt[field]
