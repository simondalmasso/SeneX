from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from senecio_polymarket.backend import authority_snapshot
from senecio_polymarket.backend import supabase_client as sc


class _Response:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = [] if payload is None else payload
        self.text = text
        self.content = json.dumps(self._payload).encode("utf-8")

    def json(self):
        return self._payload


def test_latest_fetch_is_pk_bounded_to_50():
    calls = []

    async def fake_get(client, path, **kwargs):
        calls.append((client, path, kwargs))
        return _Response(200, [])

    with patch.object(sc, "_get_client", return_value=object()), patch.object(sc, "_d1_get", side_effect=fake_get):
        rows = asyncio.run(sc.fetch_predictions(limit=500, symbol="BTC/USDT"))

    assert rows == []
    assert len(calls) == 1
    params = calls[0][2]["params"]
    assert params["id"] == "gt.0"
    assert params["order"] == "id.desc"
    assert params["limit"] == "50"
    assert params["symbol"] == "eq.BTCUSDT"
    assert "audit" not in params["select"]


def test_pending_scan_is_local_and_never_calls_d1():
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    rows = [
        {
            "id": "1",
            "ts": old,
            "symbol": "BTCUSDT",
            "prediction": "LONG",
            "confidence": 0.7,
            "price_now": 100.0,
            "outcome": None,
            "exchange_used": "test",
        },
        {
            "id": "2",
            "ts": old,
            "symbol": "BTCUSDT",
            "prediction": "NO_TRADE",
            "confidence": 0.2,
            "price_now": 100.0,
            "outcome": None,
            "exchange_used": "test",
        },
    ]
    d1_get = AsyncMock(side_effect=AssertionError("pending scan must not call D1"))
    with patch.object(sc, "get_local_authority_rows", return_value=rows), patch.object(sc, "_d1_get", d1_get):
        got = asyncio.run(sc.fetch_pending_outcomes(older_than_seconds=3600, limit=100, max_pages=2))

    assert [str(row["id"]) for row in got] == ["1"]
    assert d1_get.await_count == 0
    diag = sc.get_pending_scan_diagnostics()
    assert diag["d1_rows_scanned_last_pass"] == 0
    assert diag["fairness_scope"] == "DURABLE_AUTHORITY_LOCAL_ZERO_D1_ROWS_READ"


def test_authority_capture_reuses_one_snapshot_for_recent_rows():
    source = inspect.getsource(authority_snapshot.AuthoritySnapshotStore._capture_complete)
    assert "fetch_authority_history" in source
    assert "count_predictions_exact" in source
    assert "fetch_predictions" not in source


def test_mutable_refresh_is_hard_capped():
    assert sc.AUTHORITY_MUTABLE_ID_BATCH <= 50
    assert sc.AUTHORITY_MUTABLE_ID_MAX <= 200
    source = inspect.getsource(sc._refresh_mutable_rows)
    assert '"id": "in.(" + ",".join(batch) + ")"' in source
    assert '"limit": str(len(batch))' in source


def test_budget_gate_passes_normal_and_2x_stress():
    budget = sc.d1_rows_read_budget_projection()
    assert budget["full_table_count_rows_day"] == 0
    assert budget["offset_history_scan_rows_day"] == 0
    assert budget["pending_d1_scan_rows_day"] == 0
    assert budget["dual_backlog_d1_scan_rows_day"] == 0
    assert budget["cold_batch_hydration_rows_day"] == 0
    assert budget["projected_rows_read_day"] <= 1_000_000
    assert budget["stress_2x_rows_read_day"] <= 2_000_000
    assert budget["normal_budget_pass"] is True
    assert budget["stress_budget_pass"] is True


def test_quota_breaker_survives_in_memory_reset_and_suppresses_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_AUTHORITY_SEAL_DIR", str(tmp_path))
    sc.reset_r7b_incremental_state_for_tests()

    client = type("Client", (), {})()
    client.get = AsyncMock(return_value=_Response(429, [], "D1 quota exceeded"))

    with pytest.raises(sc.D1QuotaExceededError):
        asyncio.run(sc._d1_get(client, "/oracle_predictions", params={"limit": "1"}))

    assert client.get.await_count == 1
    breaker_path = tmp_path / "d1-quota-breaker.json"
    assert breaker_path.exists()

    # Simulate process-memory loss while preserving the durable breaker file.
    sc._d1_quota_breaker.update({"opened_at": None, "open_until": None, "reason": None})

    with pytest.raises(sc.D1QuotaExceededError) as exc:
        asyncio.run(sc._d1_get(client, "/oracle_predictions", params={"limit": "1"}))

    assert "network_call_suppressed=true" in str(exc.value)
    assert client.get.await_count == 1


def test_gateway_count_is_explicit_only_and_normal_get_is_count_free():
    path = Path("cloudflare/senex-order072-d1-gateway/src/gateway_order074.js")
    source = path.read_text(encoding="utf-8")
    count_sql = "SELECT COUNT(*) AS n FROM oracle_predictions_hot"
    assert source.count(count_sql) == 1
    assert "includeTotal ? await env.HOT.prepare" in source
    assert 'const includeTotal = /(?:^|,)\\s*count=exact' in source
    assert 'total == null ? "*" : total' in source


def test_gateway_has_no_offset_history_scan_in_steady_state_contract():
    path = Path("cloudflare/senex-order072-d1-gateway/src/gateway_order074.js")
    source = path.read_text(encoding="utf-8")
    # OFFSET remains a compatibility input, but steady-state callers do not emit it.
    steady_sources = (
        Path("senecio_polymarket/backend/supabase_client.py").read_text(encoding="utf-8")
        + Path("senecio_polymarket/backend/authority_snapshot.py").read_text(encoding="utf-8")
        + Path("senecio_polymarket/backend/oracle_runner.py").read_text(encoding="utf-8")
        + Path("senecio_polymarket/backend/settlement_reconciler.py").read_text(encoding="utf-8")
    )
    assert '"offset"' not in steady_sources
    assert "offset=" not in steady_sources