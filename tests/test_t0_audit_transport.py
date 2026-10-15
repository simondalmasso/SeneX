from __future__ import annotations

import json
from pathlib import Path

import pytest

from senecio_polymarket.backend.t0_audit.sealer import PacketSealer, build_sealed_packet
from senecio_polymarket.backend.t0_audit.transport import PacketReplicationError, _validate_endpoint, replicate_pending_t0


def source(ts: str = "2026-10-15T03:00:00Z") -> dict:
    return {
        "timestamp": ts, "symbol": "BTCUSDT", "prediction": "LONG",
        "confidence": 0.61, "ev": 0.01, "price_now": 65000.0,
        "_audit": {"confidence_semantics_v1": {"semantics": "RAW_CONVICTION"}},
        "outcome": None, "price_1h_later": None,
    }


def test_replication_requires_a_dedicated_https_ingest_path():
    assert _validate_endpoint("https://audit.example/ingest/t0") == "https://audit.example/ingest/t0"
    for endpoint in (
        "http://audit.example/ingest/t0", "https://audit.example/ingest/t0?x=1",
        "https://audit.example/other", "https://user:pass@audit.example/ingest/t0",
    ):
        with pytest.raises(PacketReplicationError):
            _validate_endpoint(endpoint)


def test_replication_retry_does_not_advance_cursor_on_transport_failure(tmp_path: Path):
    producer_root, consumer_root = tmp_path / "producer", tmp_path / "consumer"
    packet = PacketSealer(root=producer_root).seal(source())
    calls = {"n": 0}

    def flaky(endpoint: str, token: str, value: dict) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("consumer unavailable")
        accepted = PacketSealer(root=consumer_root).ingest(value)
        return {"packet_id": accepted["packet_id"], "packet_seq": accepted["packet_seq"], "packet_hash": accepted["packet_hash"]}

    with pytest.raises(PacketReplicationError, match="consumer unavailable"):
        replicate_pending_t0(root=producer_root, endpoint="https://audit.example/ingest/t0", token="i" * 32, post_json=flaky)
    cursor = producer_root / "replication_cursor.json"
    assert not cursor.exists()
    result = replicate_pending_t0(root=producer_root, endpoint="https://audit.example/ingest/t0", token="i" * 32, post_json=flaky)
    assert result["replicated"] == 1
    assert json.loads(cursor.read_text(encoding="utf-8"))["packet_seq"] == packet["packet_seq"]
    assert len(PacketSealer(root=consumer_root).read_after(None)) == 1


def test_replication_ack_mismatch_fails_closed_without_advancing_cursor(tmp_path: Path):
    root = tmp_path / "producer"
    PacketSealer(root=root).seal(source())
    with pytest.raises(PacketReplicationError, match="acknowledgement mismatch"):
        replicate_pending_t0(root=root, endpoint="https://audit.example/ingest/t0", token="i" * 32, post_json=lambda *_: {"packet_id": "wrong", "packet_seq": 1, "packet_hash": "bad"})
    assert not (root / "replication_cursor.json").exists()


def test_transport_has_no_market_or_order_execution_dependencies():
    path = Path(__file__).resolve().parents[1] / "senecio_polymarket" / "backend" / "t0_audit" / "transport.py"
    content = path.read_text(encoding="utf-8").lower()
    for forbidden in ("current_price", "price_1h_later", "outcome lookup", "binance", "subprocess", "os.system", "mcp_http"):
        assert forbidden not in content


def test_sealed_t0_receipt_format_stays_stable_for_archived_evidence():
    packet = build_sealed_packet(source(), 1)
    assert packet["schema_version"] == "gptrader.t0.v1"
    assert packet["packet_id"].startswith("gpt0-")
