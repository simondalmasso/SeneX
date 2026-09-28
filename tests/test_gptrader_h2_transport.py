from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from senecio_polymarket.backend.gptrader.mcp_http import build_mcp_app
from senecio_polymarket.backend.gptrader.sealer import (
    OutcomeContaminationError,
    PacketSealer,
    PacketSequenceError,
    PacketSizeError,
    build_sealed_packet,
)
from senecio_polymarket.backend.gptrader.store import GPTraderStore
from senecio_polymarket.backend.gptrader.transport import (
    PacketReplicationError,
    replicate_pending_t0,
)
from senecio_polymarket.backend.gptrader.decisions import DecisionService


def source(ts: str = "2026-09-27T03:00:00Z") -> dict:
    return {
        "timestamp": ts,
        "symbol": "BTCUSDT",
        "prediction": "LONG",
        "confidence": 0.61,
        "ev": 0.01,
        "price_now": 65000.0,
        "_audit": {"confidence_semantics_v1": {"semantics": "RAW_CONVICTION"}},
        "outcome": None,
        "price_1h_later": None,
    }


def service(root: Path) -> DecisionService:
    return DecisionService(GPTraderStore(root))


def test_ingest_duplicate_identical_is_noop(tmp_path: Path) -> None:
    packet = build_sealed_packet(source(), 1)
    sealer = PacketSealer(root=tmp_path)
    first = sealer.ingest(packet)
    second = sealer.ingest(packet)
    assert first == second == packet
    assert len(sealer.read_after(None)) == 1
    assert (tmp_path / "packet_seq").read_text().strip() == "1"


def test_ingest_same_packet_id_with_conflicting_hash_fails_closed(tmp_path: Path) -> None:
    packet = build_sealed_packet(source(), 1)
    sealer = PacketSealer(root=tmp_path)
    sealer.ingest(packet)
    conflict = dict(packet)
    conflict["packet_hash"] = "0" * 64
    with pytest.raises(PacketSequenceError, match="conflict|hash"):
        sealer.ingest(conflict)
    assert len(sealer.read_after(None)) == 1


@pytest.mark.parametrize("seq", [2, 3])
def test_ingest_missing_or_out_of_order_seq_fails_closed(tmp_path: Path, seq: int) -> None:
    packet = build_sealed_packet(source(), seq)
    with pytest.raises(PacketSequenceError, match="expected 1"):
        PacketSealer(root=tmp_path).ingest(packet)


def test_ingest_bad_packet_hash_fails_closed(tmp_path: Path) -> None:
    packet = build_sealed_packet(source(), 1)
    packet["packet_hash"] = "f" * 64
    with pytest.raises(PacketSequenceError, match="hash"):
        PacketSealer(root=tmp_path).ingest(packet)


def test_ingest_oversized_packet_fails_closed(tmp_path: Path) -> None:
    packet = build_sealed_packet(source(), 1)
    packet["extra"] = "x" * 9000
    with pytest.raises(PacketSizeError):
        PacketSealer(root=tmp_path).ingest(packet)


def test_ingest_future_or_outcome_contamination_fails_closed(tmp_path: Path) -> None:
    packet = build_sealed_packet(source(), 1)
    packet["outcome"] = "CORRECT"
    with pytest.raises(OutcomeContaminationError):
        PacketSealer(root=tmp_path).ingest(packet)


def test_retry_after_crash_repairs_checkpoint_without_duplicate(tmp_path: Path) -> None:
    packet = build_sealed_packet(source(), 1)
    sealer = PacketSealer(root=tmp_path)
    with patch(
        "senecio_polymarket.backend.gptrader.sealer._atomic_write_text",
        side_effect=RuntimeError("checkpoint crash"),
    ):
        with pytest.raises(RuntimeError, match="checkpoint crash"):
            sealer.ingest(packet)
    assert len(PacketSealer(root=tmp_path).read_after(None)) == 1
    restarted = PacketSealer(root=tmp_path)
    assert restarted.ingest(packet) == packet
    assert (tmp_path / "packet_seq").read_text().strip() == "1"
    assert len(restarted.read_after(None)) == 1


def test_http_ingest_requires_separate_auth_and_keeps_four_mcp_tools(tmp_path: Path) -> None:
    mcp_token = "m" * 32
    ingest_token = "i" * 32
    app = build_mcp_app(service(tmp_path), token=mcp_token, ingest_token=ingest_token)
    client = TestClient(app)
    packet = build_sealed_packet(source(), 1)
    assert client.post("/ingest/t0", json={"packet": packet}).status_code == 401
    assert client.post(
        "/ingest/t0",
        headers={"Authorization": "Bearer bad"},
        json={"packet": packet},
    ).status_code == 401
    response = client.post(
        "/ingest/t0",
        headers={"Authorization": f"Bearer {ingest_token}"},
        json={"packet": packet},
    )
    assert response.status_code == 200
    assert response.json()["packet_seq"] == 1
    tools = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {mcp_token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ).json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "get_gptrader_health",
        "get_prediction_batch",
        "get_gptrader_state",
        "submit_paper_decisions",
    }


def test_producer_retry_is_durable_and_consumer_unavailable_does_not_advance(tmp_path: Path) -> None:
    producer_root = tmp_path / "producer"
    consumer_root = tmp_path / "consumer"
    producer = PacketSealer(root=producer_root)
    packet = producer.seal(source())
    calls = {"n": 0}

    def flaky(endpoint: str, token: str, value: dict) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("consumer unavailable")
        accepted = PacketSealer(root=consumer_root).ingest(value)
        return {
            "packet_id": accepted["packet_id"],
            "packet_seq": accepted["packet_seq"],
            "packet_hash": accepted["packet_hash"],
        }

    with pytest.raises(PacketReplicationError, match="consumer unavailable"):
        replicate_pending_t0(
            root=producer_root,
            endpoint="https://mcp.example/ingest/t0",
            token="i" * 32,
            post_json=flaky,
        )
    cursor = producer_root / "replication_cursor.json"
    assert not cursor.exists()

    result = replicate_pending_t0(
        root=producer_root,
        endpoint="https://mcp.example/ingest/t0",
        token="i" * 32,
        post_json=flaky,
    )
    assert result["replicated"] == 1
    assert json.loads(cursor.read_text())["packet_seq"] == packet["packet_seq"]
    assert len(PacketSealer(root=consumer_root).read_after(None)) == 1


def test_transport_surface_has_no_d1_current_price_or_outcome_fetch() -> None:
    root = Path(__file__).resolve().parents[1] / "senecio_polymarket" / "backend" / "gptrader"
    text = "\n".join(
        p.read_text(encoding="utf-8").lower()
        for p in (root / "transport.py", root / "mcp_http.py")
        if p.exists()
    )
    for forbidden in (
        "supabase",
        "d1",
        "current_price",
        "price_1h_later",
        "settlement",
        "outcome lookup",
        "binance",
        "bybit",
        "coinmarketcap",
        "tradingcursor",
        "subprocess",
        "os.system",
    ):
        assert forbidden not in text


def test_http_ingest_rejects_oversized_request_before_mutation(tmp_path: Path) -> None:
    mcp_token = "m" * 32
    ingest_token = "i" * 32
    app = build_mcp_app(service(tmp_path), token=mcp_token, ingest_token=ingest_token)
    client = TestClient(app)
    packet = build_sealed_packet(source(), 1)
    packet["extra"] = "x" * 20000

    response = client.post(
        "/ingest/t0",
        headers={"Authorization": f"Bearer {ingest_token}"},
        json={"packet": packet},
    )

    assert response.status_code == 413
    assert PacketSealer(root=tmp_path).read_after(None) == []


def test_large_realistic_t0_compacts_pipeline_to_decision_safe_subset() -> None:
    row = source()
    row["_audit"] = {
        "confidence_semantics_v1": {"semantics": "RAW_CONVICTION"},
        "pipeline": {
            "step2_features": {
                "up_prob": 0.57,
                **{f"feature_{idx}": "x" * 180 for idx in range(80)},
            },
            "step4_ev": {
                "ev": 0.01,
                "diagnostic_blob": "y" * 4000,
            },
        },
        "decision_replay_v1": {
            "version": "v1",
            "captured_at": "2026-09-28T03:22:44Z",
            "snapshot_hash": "a" * 64,
            "code_hash": "b" * 64,
            "config_hash": "c" * 64,
        },
    }

    packet = build_sealed_packet(row, 1)

    encoded = json.dumps(
        packet,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert len(encoded) <= 8192
    assert packet["_audit"]["pipeline"] == {
        "step2_features": {"up_prob": 0.57}
    }
    assert packet["_audit"]["provenance_v1"]["snapshot_hash"] == "a" * 64
