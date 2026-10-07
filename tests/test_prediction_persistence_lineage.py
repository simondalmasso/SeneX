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
