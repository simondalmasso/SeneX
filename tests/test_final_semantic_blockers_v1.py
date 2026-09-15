from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from senecio_polymarket.backend import authority_snapshot as snap
from senecio_polymarket.backend import supabase_client as sc


def _row() -> dict:
    return {
        "id": "1",
        "ts": "2026-09-15T12:00:00+00:00",
        "symbol": "BTCUSDT",
        "prediction": "LONG",
        "confidence": 0.61,
        "price_now": 100.0,
        "outcome": "WIN",
        "exchange_used": "test",
        "audit": {},
    }


def _gate(_score: dict) -> dict:
    return {"trade_mode": "PAPER", "live_capital_locked": True, "orders_enabled": False}


def test_runtime_authority_seal_is_hmac_authenticated(monkeypatch) -> None:
    monkeypatch.setenv("SENEX_AUTHORITY_SEAL_KEY", "K" * 48)
    monkeypatch.setattr(sc, "_runtime_identity", lambda: {
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "build_digest": "sha256:" + "c" * 64,
    })
    rows = {"1": _row()}
    cursor = (_row()["ts"], "1")
    seal = sc._seal_state("BTCUSDT", rows, cursor)
    assert str(seal.get("seal_hash", "")).startswith("hmac-sha256:")
    state = {"seal": seal, "rows": rows, "cursor": cursor}
    sc._validate_state_metadata("BTCUSDT", state)
    monkeypatch.setenv("SENEX_AUTHORITY_SEAL_KEY", "Z" * 48)
    with pytest.raises(sc.AuthorityHistoryIncompleteError, match="AUTHORITY_RUNTIME_SEAL_HASH_MISMATCH"):
        sc._validate_state_metadata("BTCUSDT", state)


def test_production_entrypoint_requires_strong_authority_seal_key() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "senecio_polymarket" / "start_single_authority.sh").read_text(encoding="utf-8")
    assert "SENEX_AUTHORITY_SEAL_KEY" in script
    assert "AUTHORITY_SEAL_KEY" in script and "32" in script


def test_authority_snapshot_rejects_recent_prediction_transport_failure() -> None:
    rows = [_row()]

    async def fake_count(symbol=None):
        return 1

    async def strict_failure(*_args, **_kwargs):
        raise sc.D1TransientUnavailableError("D1_TRANSIENT_UNAVAILABLE")

    store = snap.AuthoritySnapshotStore(ttl_s=60.0)
    with mock.patch.object(sc, "fetch_authority_history", new=mock.AsyncMock(return_value=rows)), \
         mock.patch.object(sc, "count_predictions_exact", new=fake_count), \
         mock.patch.object(sc, "fetch_predictions", new=mock.AsyncMock(return_value=[])), \
         mock.patch.object(sc, "fetch_predictions_strict", new=strict_failure, create=True), \
         mock.patch.object(snap, "runtime_provenance", return_value={"exact": True}):
        with pytest.raises(snap.AuthoritySnapshotRefreshError, match="RECENT_PREDICTIONS"):
            asyncio.run(store.get("BTCUSDT", live_gate_builder=_gate, force=True))


def test_legitimate_empty_recent_predictions_remain_valid() -> None:
    rows = [_row()]

    async def fake_count(symbol=None):
        return 1

    store = snap.AuthoritySnapshotStore(ttl_s=60.0)
    with mock.patch.object(sc, "fetch_authority_history", new=mock.AsyncMock(return_value=rows)), \
         mock.patch.object(sc, "count_predictions_exact", new=fake_count), \
         mock.patch.object(sc, "fetch_predictions", new=mock.AsyncMock(return_value=[])), \
         mock.patch.object(sc, "fetch_predictions_strict", new=mock.AsyncMock(return_value=[]), create=True), \
         mock.patch.object(snap, "runtime_provenance", return_value={"exact": True}):
        result = asyncio.run(store.get("BTCUSDT", live_gate_builder=_gate, force=True))

    assert result.authority_history_rows == 1
    assert store.recent_predictions("BTCUSDT", limit=50) == []
    observed, refresh = store.observe("BTCUSDT")
    assert observed is not None
    assert refresh["last_refresh_error"] is None


def test_cached_snapshot_stays_degraded_not_empty_on_recent_read_failure() -> None:
    rows = [_row()]

    async def fake_count(symbol=None):
        return 1

    store = snap.AuthoritySnapshotStore(ttl_s=60.0)
    with mock.patch.object(sc, "fetch_authority_history", new=mock.AsyncMock(return_value=rows)), \
         mock.patch.object(sc, "count_predictions_exact", new=fake_count), \
         mock.patch.object(sc, "fetch_predictions_strict", new=mock.AsyncMock(return_value=rows)), \
         mock.patch.object(snap, "runtime_provenance", return_value={"exact": True}):
        first = asyncio.run(store.get("BTCUSDT", live_gate_builder=_gate, force=True))

    async def strict_failure(*_args, **_kwargs):
        raise sc.D1TransientUnavailableError("D1_TRANSIENT_UNAVAILABLE")

    with mock.patch.object(sc, "fetch_authority_history", new=mock.AsyncMock(return_value=rows)), \
         mock.patch.object(sc, "count_predictions_exact", new=fake_count), \
         mock.patch.object(sc, "fetch_predictions_strict", new=strict_failure), \
         mock.patch.object(snap, "runtime_provenance", return_value={"exact": True}):
        second = asyncio.run(store.get("BTCUSDT", live_gate_builder=_gate, force=True))

    assert second.snapshot_id == first.snapshot_id
    assert store.recent_predictions("BTCUSDT", limit=50) == rows
    observed, refresh = store.observe("BTCUSDT")
    assert observed is not None
    assert refresh["last_refresh_error"] is not None
    assert "RECENT_PREDICTIONS" in str(refresh["last_refresh_error"])
