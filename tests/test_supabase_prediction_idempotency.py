[Reading 159 lines from start (total: 159 lines, 0 remaining)]

from __future__ import annotations

import asyncio
import copy

import pytest

from senecio_polymarket.backend import supabase_client


class _Response:
    def __init__(self, rows, status_code=200):
        self._rows = rows
        self.status_code = status_code
        self.text = ""

    def json(self):
        return copy.deepcopy(self._rows)


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
            },
            "pipeline": {"step2_features": {"up_prob": 0.61}},
            "external_markets_v1": {
                "polymarket": {
                    "source": "POLYMARKET_PUBLIC",
                    "version": "polymarket-btc-5m-v1",
                    "slug": "btc-updown-5m-1791309600",
                    "condition_id": "cond-a",
                    "start_ts": 1791309600,
                    "end_ts": 1791309900,
                    "up_probability": 0.57,
                    "eligible_for_prediction": True,
                }
            },
        },
    }


def _existing(prediction, *, row_id=17):
    return {
        "id": row_id,
        "ts": prediction["timestamp"].replace("Z", "+00:00"),
        "symbol": prediction["symbol"],
        "prediction": prediction["prediction"],
        "confidence": prediction["confidence"],
        "ev": prediction["ev"],
        "price_now": prediction["price_now"],
        "exchange_used": prediction["exchange_used"],
        "audit": {
            **copy.deepcopy(prediction["_audit"]),
            "outcomes_dual": {"outcome_1h": "WIN"},
        },
    }


def test_existing_matching_t0_is_reused_without_post(monkeypatch):
    prediction = _prediction()
    existing = _existing(prediction)

    class Client:
        async def post(self, *args, **kwargs):
            raise AssertionError("POST must not run when exact T0 already exists")

    async def fake_get(_client, _path, **_kwargs):
        return _Response([existing])

    monkeypatch.setattr(supabase_client, "_get_client", lambda: Client())
    monkeypatch.setattr(supabase_client, "_d1_get", fake_get)
    monkeypatch.setattr(supabase_client, "persist_authority_row_local", lambda _row: None)

    result = asyncio.run(supabase_client.ensure_prediction_persisted(prediction))

    assert result["id"] == 17


def test_ack_lost_retry_recovers_same_committed_t0_without_second_post(monkeypatch):
    prediction = _prediction()
    existing = _existing(prediction, row_id=23)
    lookups = iter([[], [existing]])
    posts = {"n": 0}

    class Client:
        pass

    async def fake_get(_client, _path, **_kwargs):
        return _Response(next(lookups))

    async def fake_insert(_prediction):
        posts["n"] += 1
        return None

    monkeypatch.setattr(supabase_client, "_get_client", lambda: Client())
    monkeypatch.setattr(supabase_client, "_d1_get", fake_get)
    monkeypatch.setattr(supabase_client, "insert_prediction", fake_insert)
    monkeypatch.setattr(supabase_client, "persist_authority_row_local", lambda _row: None)

    result = asyncio.run(supabase_client.ensure_prediction_persisted(prediction))

    assert posts["n"] == 1
    assert result["id"] == 23


def test_insert_ack_with_wrong_t0_fails_closed_before_local_authority(monkeypatch):
    prediction = _prediction()
    wrong = _existing(prediction, row_id=91)
    wrong["price_now"] = 999.0
    persisted = {"n": 0}

    class Client:
        async def post(self, *args, **kwargs):
            return _Response([wrong], status_code=201)

    monkeypatch.setattr(supabase_client, "_get_client", lambda: Client())
    monkeypatch.setattr(
        supabase_client,
        "persist_authority_row_local",
        lambda _row: persisted.__setitem__("n", persisted["n"] + 1),
    )

    with pytest.raises(
        supabase_client.PredictionPersistenceConflictError,
        match="acknowledgement conflicts",
    ):
        asyncio.run(supabase_client.insert_prediction(prediction))

    assert persisted["n"] == 0


def test_conflicting_same_timestamp_symbol_fails_closed(monkeypatch):
    prediction = _prediction()
    existing = _existing(prediction)
    existing["price_now"] = 999.0

    class Client:
        pass

    async def fake_get(_client, _path, **_kwargs):
        return _Response([existing])

    monkeypatch.setattr(supabase_client, "_get_client", lambda: Client())
    monkeypatch.setattr(supabase_client, "_d1_get", fake_get)

    with pytest.raises(supabase_client.PredictionPersistenceConflictError):
        asyncio.run(supabase_client.ensure_prediction_persisted(prediction))

[executed on device: DESKTOP-DPH3941 (f5db7315-cdea-42b4-b067-243411e4a115)]