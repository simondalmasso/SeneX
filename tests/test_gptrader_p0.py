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


def test_oracle_runner_seals_receipt_before_local_dedupe_marker_and_remote_authority():
    from pathlib import Path

    source = Path("senecio_polymarket/backend/oracle_runner.py").read_text(encoding="utf-8")
    start = source.index("async def _run_one_prediction")
    end = source.index("async def _fetch_current_price")
    body = source[start:end]

    preview = body.index("preview_packet = await asyncio.to_thread(")
    receipt = body.index("receipt_store.enqueue(preview_packet, prediction)")
    sealed = body.index(
        "sealed_packet = await asyncio.to_thread(seal_prediction_t0, prediction)"
    )
    persisted = body.index("await asyncio.to_thread(log_prediction")
    counted = body.index('_state["predictions_count"] += 1')
    remote = body.index("await _persist_and_route_prediction")
    assert preview < receipt < sealed < persisted < counted < remote
    assert "sealed packet identity diverged from receipt outbox" in body
    assert "store=receipt_store" in body


def _oversized_source():
    source = _source()
    source["_audit"]["decision_replay_v1"].update({
        "captured_at": source["timestamp"],
        "market": {
            "ohlcv": [[i, i, i, i, i, i] for i in range(500)],
            "orderbook": {
                "bids": [[1, 2]] * 500,
                "asks": [[2, 1]] * 500,
            },
        },
        "runtime_provenance": {
            "source_commit": "abc",
            "source_tree": "def",
            "exact": True,
        },
        "feature_source_identity": {
            "symbol": "BTCUSDT",
            "exchange_used": "binance",
            "timeframe": "15m",
        },
        "snapshot_hash": "x" * 64,
        "query_observed_at_epoch": 123.0,
    })
    source["_audit"]["external_markets_v1"] = {"blob": "x" * 12000}
    return source


def test_torn_final_tail_is_recovered_append_only_and_observable(tmp_path):
    sealer = PacketSealer(root=tmp_path)
    p1 = sealer.seal(_source("2026-09-26T12:00:00+00:00"))
    with open(tmp_path / "sealed_packets.jsonl", "ab") as handle:
        handle.write(b'{"packet_seq":2,"broken":')

    restarted = PacketSealer(root=tmp_path)
    p2 = restarted.seal(_source("2026-09-26T12:15:00+00:00"))
    assert [p1["packet_seq"], p2["packet_seq"]] == [1, 2]
    assert restarted.health()["log_status"] == "RECOVERED_TORN_TAIL"

    restarted_again = PacketSealer(root=tmp_path)
    p3 = restarted_again.seal(_source("2026-09-26T12:30:00+00:00"))
    assert p3["packet_seq"] == 3
    assert restarted_again.health()["recovered_torn_tails"] == 1


def test_unmarked_middle_corruption_still_fails_closed(tmp_path):
    from senecio_polymarket.backend.gptrader.sealer import PacketSequenceError

    sealer = PacketSealer(root=tmp_path)
    sealer.seal(_source())
    path = tmp_path / "sealed_packets.jsonl"
    with open(path, "ab") as handle:
        handle.write(b"{bad-json}\n")
        handle.write(
            (
                json.dumps(
                    build_sealed_packet(_source("2026-09-26T12:15:00+00:00"), 2)
                )
                + "\n"
            ).encode("utf-8")
        )

    with pytest.raises(PacketSequenceError):
        PacketSealer(root=tmp_path).read_after(None)


def test_oversized_real_shaped_packet_is_compacted_under_target_and_hard_limit():
    packet = build_sealed_packet(_oversized_source(), packet_seq=1)
    encoded = json.dumps(
        packet,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert len(encoded) <= 4096
    assert len(encoded) <= 8192
    assert packet["prediction"] == "LONG"
    assert packet["confidence"] == 0.61
    assert packet["_audit"]["confidence_semantics_v1"]["semantics"] == "RAW_CONVICTION"
    assert packet["_audit"]["provenance_v1"]["runtime_provenance"]["source_commit"] == "abc"
    assert "decision_replay_v1" not in packet["_audit"]
    assert "external_markets_v1" not in packet["_audit"]


def test_uncompactable_required_payload_fails_closed_at_hard_limit():
    from senecio_polymarket.backend.gptrader.sealer import PacketSizeError

    oversized = _source()
    oversized["_audit"]["confidence_semantics_v1"]["notes"] = "z" * 9000
    with pytest.raises(PacketSizeError, match="exceeds hard limit"):
        build_sealed_packet(oversized, packet_seq=1)