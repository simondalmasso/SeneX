[Reading 280 lines from start (total: 280 lines, 0 remaining)]

from __future__ import annotations

import asyncio
import copy

import pytest

from senecio_polymarket.backend import oracle_runner
from senecio_polymarket.backend.prediction_persistence import (
    PredictionPersistenceError,
    PredictionPersistenceStore,
)


def _prediction():
    return {
        "timestamp": "2026-10-06T18:00:00Z",
        "symbol": "BTCUSDT",
        "prediction": "LONG",
        "confidence": 0.7,
        "ev": 0.01,
        "price_now": 100.0,
        "exchange_used": "okx",
        "_audit": {
            "origin_price_v1": {
                "version": "origin-price-v1",
                "price": 100.0,
                "timestamp": "2026-10-06T18:00:00Z",
                "source": "okx",
            }
        },
    }


def _packet():
    return {
        "packet_id": "gptrader-t0-" + "a" * 24,
        "packet_hash": "b" * 64,
        "packet_seq": 1,
    }


def test_store_restart_preserves_failed_original_t0(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path))
    store = PredictionPersistenceStore()
    store.enqueue(_packet(), _prediction())
    store.mark_failed(_packet()["packet_hash"], "NETWORK")

    restarted = PredictionPersistenceStore()
    pending = restarted.pending(limit=10)

    assert len(pending) == 1
    assert pending[0]["source_packet_hash"] == _packet()["packet_hash"]
    assert pending[0]["prediction"] == _prediction()
    assert pending[0]["status"] == "FAILED"
    assert (tmp_path / "prediction_persistence_receipts.jsonl").exists()


def test_store_rejects_same_packet_hash_with_different_original_t0(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path))
    store = PredictionPersistenceStore()
    store.enqueue(_packet(), _prediction())
    changed = _prediction()
    changed["price_now"] = 101.0

    with pytest.raises(PredictionPersistenceError, match="payload"):
        store.enqueue(_packet(), changed)


def test_failed_authority_persistence_quarantines_paper_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path))
    store = PredictionPersistenceStore()
    calls = {"portfolio": 0, "sim": 0}

    async def no_persist(_prediction):
        return None

    async def portfolio(_prediction, _market_data):
        calls["portfolio"] += 1

    def sim(_prediction):
        calls["sim"] += 1

    monkeypatch.setattr(
        "senecio_polymarket.backend.supabase_client.ensure_prediction_persisted",
        no_persist,
        raising=False,
    )
    monkeypatch.setattr(oracle_runner, "_route_to_portfolio", portfolio)
    monkeypatch.setattr(oracle_runner, "_route_to_binance_sim", sim)

    prediction = _prediction()
    ok = asyncio.run(
        oracle_runner._persist_and_route_prediction(
            prediction,
            {"best_bid": 99.0, "best_ask": 101.0},
            _packet(),
            store=store,
        )
    )

    assert ok is False
    assert "id" not in prediction
    assert calls == {"portfolio": 0, "sim": 0}
    assert store.pending(limit=10)[0]["status"] == "FAILED"


def test_successful_authority_persistence_binds_id_before_paper_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path))
    store = PredictionPersistenceStore()
    calls = {"portfolio_id": None, "sim_id": None}

    async def persisted(_prediction):
        return {"id": 9123, "ts": _prediction["timestamp"], "symbol": _prediction["symbol"]}

    async def portfolio(prediction, _market_data):
        calls["portfolio_id"] = prediction.get("id")

    def sim(prediction):
        calls["sim_id"] = prediction.get("id")

    monkeypatch.setattr(
        "senecio_polymarket.backend.supabase_client.ensure_prediction_persisted",
        persisted,
        raising=False,
    )
    monkeypatch.setattr(oracle_runner, "_route_to_portfolio", portfolio)
    monkeypatch.setattr(oracle_runner, "_route_to_binance_sim", sim)

    prediction = _prediction()
    ok = asyncio.run(
        oracle_runner._persist_and_route_prediction(
            prediction,
            {"best_bid": 99.0, "best_ask": 101.0},
            _packet(),
            store=store,
        )
    )

    assert ok is True
    assert prediction["id"] == 9123
    assert calls == {"portfolio_id": 9123, "sim_id": 9123}
    assert store.pending(limit=10) == []


def test_restart_retry_persists_original_t0_without_retroactive_paper_route(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path))
    store = PredictionPersistenceStore()
    store.enqueue(_packet(), _prediction())
    store.mark_failed(_packet()["packet_hash"], "NETWORK")
    routes = {"portfolio": 0, "sim": 0}

    async def persisted(prediction):
        assert prediction == _prediction()
        return {"id": 77, "ts": prediction["timestamp"], "symbol": prediction["symbol"]}

    async def portfolio(_prediction, _market_data):
        routes["portfolio"] += 1

    def sim(_prediction):
        routes["sim"] += 1

    monkeypatch.setattr(
        "senecio_polymarket.backend.supabase_client.ensure_prediction_persisted",
        persisted,
    )
    monkeypatch.setattr(oracle_runner, "_route_to_portfolio", portfolio)
    monkeypatch.setattr(oracle_runner, "_route_to_binance_sim", sim)

    result = asyncio.run(
        oracle_runner._retry_pending_authority_persistence(
            limit=4,
            store=PredictionPersistenceStore(),
        )
    )

    assert result == {"attempted": 1, "persisted": 1, "remaining": 0}
    assert routes == {"portfolio": 0, "sim": 0}
    assert PredictionPersistenceStore().pending(limit=10) == []


def test_receipt_store_recovers_torn_final_tail_append_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path))
    store = PredictionPersistenceStore()
    store.enqueue(_packet(), _prediction())
    path = tmp_path / "prediction_persistence_receipts.jsonl"
    with open(path, "ab") as handle:
        handle.write(b'{"contract":"senex-prediction-persistence-receipt-v1","broken":')

    packet2 = {
        "packet_id": "gptrader-t0-" + "c" * 24,
        "packet_hash": "d" * 64,
        "packet_seq": 2,
    }
    prediction2 = _prediction()
    prediction2["timestamp"] = "2026-10-06T18:15:00Z"
    restarted = PredictionPersistenceStore()
    restarted.enqueue(packet2, prediction2)

    states = restarted.states()
    assert len(states) == 2
    raw = path.read_text(encoding="utf-8")
    assert "_prediction_persistence_recovery_v1" in raw


def test_receipt_store_repairs_valid_unterminated_final_record(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path))
    store = PredictionPersistenceStore()
    store.enqueue(_packet(), _prediction())
    path = tmp_path / "prediction_persistence_receipts.jsonl"
    path.write_bytes(path.read_bytes().rstrip(b"\r\n"))

    packet2 = {
        "packet_id": "gptrader-t0-" + "e" * 24,
        "packet_hash": "f" * 64,
        "packet_seq": 2,
    }
    prediction2 = _prediction()
    prediction2["timestamp"] = "2026-10-06T18:15:00Z"

    restarted = PredictionPersistenceStore()
    restarted.enqueue(packet2, prediction2)

    states = restarted.states()
    assert len(states) == 2
    raw = path.read_bytes()
    assert b"}\n{" in raw
    assert not raw.endswith(b"}{")


def test_pending_retry_rotation_does_not_starve_newer_receipts(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path))
    store = PredictionPersistenceStore()
    hashes = []
    for idx in range(6):
        packet_hash = f"{idx + 1:064x}"
        hashes.append(packet_hash)
        packet = {
            "packet_id": f"gptrader-t0-{idx + 1:024x}",
            "packet_hash": packet_hash,
            "packet_seq": idx + 1,
        }
        prediction = _prediction()
        prediction["timestamp"] = f"2026-10-06T18:{idx:02d}:00Z"
        store.enqueue(packet, prediction)

    first = store.pending(limit=2)
    assert [row["source_packet_hash"] for row in first] == hashes[:2]
    for row in first:
        store.mark_failed(row["source_packet_hash"], "PERMANENT_CONFLICT")

    second = store.pending(limit=2)
    assert [row["source_packet_hash"] for row in second] == hashes[2:4]


def test_restart_retry_keeps_unresolved_receipt_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path))
    store = PredictionPersistenceStore()
    store.enqueue(_packet(), _prediction())
    store.mark_failed(_packet()["packet_hash"], "NETWORK")

    async def unresolved(_prediction):
        return None

    monkeypatch.setattr(
        "senecio_polymarket.backend.supabase_client.ensure_prediction_persisted",
        unresolved,
    )

    result = asyncio.run(
        oracle_runner._retry_pending_authority_persistence(
            limit=4,
            store=PredictionPersistenceStore(),
        )
    )

    assert result == {"attempted": 1, "persisted": 0, "remaining": 1}
    pending = PredictionPersistenceStore().pending(limit=10)
    assert len(pending) == 1
    assert pending[0]["status"] == "FAILED"

[executed on device: DESKTOP-DPH3941 (f5db7315-cdea-42b4-b067-243411e4a115)]