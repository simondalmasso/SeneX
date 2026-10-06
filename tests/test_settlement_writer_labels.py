from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

import pytest

from senecio_polymarket.backend import settlement_contract as contract
from senecio_polymarket.backend import supabase_client as sc


class _Response:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.content = b"x"
        self.text = ""

    def json(self):
        return self._payload


class _Client:
    def __init__(self):
        self.patch = mock.AsyncMock(return_value=_Response(200, [{"id": 7}]))


def _evidence(ts: str, window_seconds: int, price: float) -> dict:
    target = contract.target_epoch_ms(ts, window_seconds)
    assert target is not None
    open_ms = target
    close_ms = open_ms + contract.CANDLE_INTERVAL_MS
    observed = datetime.fromtimestamp(
        (close_ms + 1) / 1000.0,
        tz=timezone.utc,
    ).isoformat()
    return {
        "version": "historical-price-evidence-v1",
        "source": "okx",
        "symbol": "BTC/USDT",
        "window_seconds": window_seconds,
        "target_epoch_ms": target,
        "candle_open_epoch_ms": open_ms,
        "candle_close_epoch_ms": close_ms,
        "candle_interval_ms": contract.CANDLE_INTERVAL_MS,
        "price": price,
        "observed_at": observed,
    }


def _existing(direction: str = "LONG") -> dict:
    ts = "2026-01-01T00:00:00+00:00"
    return {
        "id": 7,
        "ts": ts,
        "symbol": "BTCUSDT",
        "prediction": direction,
        "price_now": 100.0,
        "exchange_used": "okx",
        "outcome": None,
        "audit": {
            "origin_price_v1": {
                "version": "origin-price-v1",
                "source": "okx",
                "symbol": "BTCUSDT",
                "timestamp": ts,
                "price": 100.0,
            }
        },
    }


@pytest.mark.asyncio
async def test_dual_writer_rejects_labels_that_conflict_with_valid_long_evidence(monkeypatch):
    client = _Client()
    row = _existing("LONG")
    monkeypatch.setattr(sc, "_get_client", lambda: client)
    monkeypatch.setattr(
        sc,
        "_d1_get",
        mock.AsyncMock(return_value=_Response(200, [row])),
    )
    monkeypatch.setattr(sc, "persist_authority_row_local", lambda row: True)

    ok = await sc.update_outcome_dual(
        7,
        "LOSS",
        "LOSS",
        110.0,
        120.0,
        price_evidence_15m=_evidence(row["ts"], contract.WINDOW_15M_S, 110.0),
        price_evidence_1h=_evidence(row["ts"], contract.WINDOW_1H_S, 120.0),
    )

    assert ok is False
    client.patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_dual_writer_derives_and_persists_valid_long_labels(monkeypatch):
    client = _Client()
    row = _existing("LONG")
    monkeypatch.setattr(sc, "_get_client", lambda: client)
    monkeypatch.setattr(
        sc,
        "_d1_get",
        mock.AsyncMock(return_value=_Response(200, [row])),
    )
    monkeypatch.setattr(sc, "persist_authority_row_local", lambda row: True)

    ok = await sc.update_outcome_dual(
        7,
        "WIN",
        "WIN",
        110.0,
        120.0,
        price_evidence_15m=_evidence(row["ts"], contract.WINDOW_15M_S, 110.0),
        price_evidence_1h=_evidence(row["ts"], contract.WINDOW_1H_S, 120.0),
    )

    assert ok is True
    kwargs = client.patch.await_args.kwargs
    assert kwargs["json"]["outcome"] == "WIN"
    dual = kwargs["json"]["audit"]["outcomes_dual"]
    assert dual["outcome_15m"] == "WIN"
    assert dual["outcome_1h"] == "WIN"


@pytest.mark.asyncio
async def test_dual_writer_rejects_labels_that_conflict_with_valid_short_evidence(monkeypatch):
    client = _Client()
    row = _existing("SHORT")
    monkeypatch.setattr(sc, "_get_client", lambda: client)
    monkeypatch.setattr(
        sc,
        "_d1_get",
        mock.AsyncMock(return_value=_Response(200, [row])),
    )

    ok = await sc.update_outcome_dual(
        7,
        "LOSS",
        "LOSS",
        90.0,
        80.0,
        price_evidence_15m=_evidence(row["ts"], contract.WINDOW_15M_S, 90.0),
        price_evidence_1h=_evidence(row["ts"], contract.WINDOW_1H_S, 80.0),
    )

    assert ok is False
    client.patch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome_15m,outcome_1h", [
    ("MAYBE", "WIN"),
    ("WIN", ""),
    ("UP", "DOWN"),
])
async def test_dual_writer_rejects_invalid_outcome_tokens(
    monkeypatch, outcome_15m, outcome_1h
):
    client = _Client()
    row = _existing("LONG")
    monkeypatch.setattr(sc, "_get_client", lambda: client)
    monkeypatch.setattr(
        sc,
        "_d1_get",
        mock.AsyncMock(return_value=_Response(200, [row])),
    )

    ok = await sc.update_outcome_dual(
        7,
        outcome_15m,
        outcome_1h,
        110.0,
        120.0,
        price_evidence_15m=_evidence(row["ts"], contract.WINDOW_15M_S, 110.0),
        price_evidence_1h=_evidence(row["ts"], contract.WINDOW_1H_S, 120.0),
    )

    assert ok is False
    client.patch.assert_not_awaited()
