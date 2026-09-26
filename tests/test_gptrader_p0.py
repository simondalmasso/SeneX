import json

import pytest

from senecio_polymarket.backend.gptrader.cursor import PacketCursor
from senecio_polymarket.backend.gptrader.sealer import (
    OutcomeContaminationError,
    PacketSealer,
    build_sealed_packet,
)


def _source(ts="2026-09-26T12:00:00+00:00", symbol="BTCUSDT"):
    return {
        "timestamp": ts,
        "symbol": symbol,
        "prediction": "LONG",
        "confidence": 0.61,
        "ev": 0.0125,
        "price_now": 65000.0,
        "price_15m_later": None,
        "outcome": None,
        "exchange_used": "binance",
        "_audit": {
            "candle_ts": 1790424000000,
            "origin_price_v1": {"version": "origin-price-v1", "price": 65000.0},
            "confidence_semantics_v1": {"semantics": "RAW_CONVICTION"},
            "pipeline": {
                "step1_market": {"unused": True},
                "step2_features": {"up_prob": 0.63, "pressures": {"book": 0.2}},
                "step4_ev": {"adjusted_ev": 0.0125},
                "step5_feasibility": {"unused": True},
            },
            "execution_state": {"spread_bps": 2.0, "slippage_bps": 1.0},
            "decision_replay_v1": {
                "version": "decision-replay-v1",
                "market": {"ticker": {"bid": 65000.0}, "future_price": None},
                "outcome": None,
            },
            "outcomes_dual": {},
        },
        "unapproved_top_level": "drop-me",
    }


def test_future_and_outcome_placeholders_are_removed_by_projection():
    packet = build_sealed_packet(_source(), packet_seq=1)
    encoded = json.dumps(packet, sort_keys=True)
    assert "price_15m_later" not in encoded
    assert '"outcome"' not in encoded
    assert "future_price" not in encoded
    assert "outcomes_dual" not in encoded
    assert "unapproved_top_level" not in encoded
    assert "step1_market" not in encoded
    assert "step5_feasibility" not in encoded
    assert packet["candle_ts"] == 1790424000000


def test_packet_hash_and_id_are_stable_for_semantically_identical_input():
    first = _source()
    second = _source()
    second["_audit"]["pipeline"]["step2_features"] = {
        "pressures": {"book": 0.2},
        "up_prob": 0.63,
    }
    a = build_sealed_packet(first, packet_seq=1)
    b = build_sealed_packet(second, packet_seq=99)
    assert a["packet_hash"] == b["packet_hash"]
    assert a["packet_id"] == b["packet_id"]
    assert a["packet_seq"] != b["packet_seq"]


def test_outcome_contaminated_source_is_rejected():
    settled = _source()
    settled["outcome"] = "CORRECT"
    settled["price_15m_later"] = 65123.0
    with pytest.raises(OutcomeContaminationError, match="PACKET_REFUSED_OUTCOME_PRESENT"):
        build_sealed_packet(settled, packet_seq=1)


def test_restart_is_idempotent_and_sequence_cursor_does_not_skip(tmp_path):
    first = PacketSealer(root=tmp_path)
    p1 = first.seal(_source("2026-09-26T12:00:00+00:00"))
    p2 = first.seal(_source("2026-09-26T12:15:00+00:00"))

    restarted = PacketSealer(root=tmp_path)
    duplicate = restarted.seal(_source("2026-09-26T12:15:00+00:00"))
    p3 = restarted.seal(_source("2026-09-26T12:30:00+00:00"))

    assert [p1["packet_seq"], p2["packet_seq"], p3["packet_seq"]] == [1, 2, 3]
    assert duplicate["packet_id"] == p2["packet_id"]
    assert duplicate["packet_seq"] == 2

    cursor = PacketCursor(packet_seq=1)
    restored = PacketCursor.from_token(cursor.token)
    batch = restarted.read_after(restored, limit=16)
    assert [p["packet_seq"] for p in batch] == [2, 3]

    lines = [line for line in (tmp_path / "sealed_packets.jsonl").read_text().splitlines() if line]
    assert len(lines) == 3
    assert (tmp_path / "packet_seq").read_text().strip() == "3"


def test_oracle_runner_seals_after_local_prediction_persistence_before_remote_mirror():
    from pathlib import Path

    source = Path("senecio_polymarket/backend/oracle_runner.py").read_text(encoding="utf-8")
    persisted = source.index("await asyncio.to_thread(log_prediction")
    sealed = source.index("seal_prediction_t0")
    remote = source.index("from . import supabase_client")
    assert persisted < sealed < remote
