from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from senecio_polymarket.backend import supabase_client as sc


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _Client:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def get(self, *_args, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _reset_breakers() -> None:
    sc._d1_quota_breaker.update({
        "opened_at": None,
        "open_until": None,
        "reason": None,
        "network_calls": 0,
        "suppressed_calls": 0,
    })
    state = getattr(sc, "_d1_transient_breaker", None)
    if isinstance(state, dict):
        state.update({
            "opened_at": None,
            "open_until": None,
            "reason": None,
            "consecutive_failures": 0,
            "backoff_seconds": 30,
            "open_count": 0,
            "suppressed_calls": 0,
        })


@pytest.fixture(autouse=True)
def _clean_breakers():
    _reset_breakers()
    yield
    _reset_breakers()


def test_429_with_arbitrary_body_keeps_daily_quota_breaker() -> None:
    client = _Client([_Response(429, "opaque upstream body")])
    with pytest.raises(sc.D1QuotaExceededError, match="D1_QUOTA_EXCEEDED"):
        asyncio.run(sc._d1_get(client, "/oracle_predictions"))
    status = sc.get_d1_quota_breaker_status()
    assert status["open"] is True
    assert client.calls == 1


def test_three_503s_open_transient_breaker_and_suppress_next_call() -> None:
    client = _Client([
        _Response(503, "a"), _Response(503, "b"), _Response(503, "c"),
        _Response(200, "should-not-be-called"),
    ])
    for _ in range(3):
        response = asyncio.run(sc._d1_get(client, "/oracle_predictions"))
        assert response.status_code == 503
    with pytest.raises(RuntimeError, match="D1_TRANSIENT"):
        asyncio.run(sc._d1_get(client, "/oracle_predictions"))
    assert client.calls == 3


def test_success_resets_transient_failure_streak() -> None:
    client = _Client([
        _Response(503, "a"), _Response(503, "b"), _Response(200, "ok"),
        _Response(503, "c"), _Response(200, "still-called"),
    ])
    assert asyncio.run(sc._d1_get(client, "/x")).status_code == 503
    assert asyncio.run(sc._d1_get(client, "/x")).status_code == 503
    assert asyncio.run(sc._d1_get(client, "/x")).status_code == 200
    assert asyncio.run(sc._d1_get(client, "/x")).status_code == 503
    assert asyncio.run(sc._d1_get(client, "/x")).status_code == 200
    assert client.calls == 5


def test_transport_errors_participate_in_transient_breaker() -> None:
    err = lambda: httpx.ConnectError("down")
    client = _Client([err(), err(), err(), _Response(200, "should-not-be-called")])
    for _ in range(3):
        with pytest.raises(httpx.TransportError):
            asyncio.run(sc._d1_get(client, "/x"))
    with pytest.raises(RuntimeError, match="D1_TRANSIENT"):
        asyncio.run(sc._d1_get(client, "/x"))
    assert client.calls == 3


def test_repeated_outage_escalates_transient_backoff(monkeypatch) -> None:
    now = [datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(sc, "_now_utc", lambda: now[0])
    client = _Client([_Response(503, str(i)) for i in range(6)] + [_Response(200, "sentinel")])

    for _ in range(3):
        asyncio.run(sc._d1_get(client, "/x"))
    with pytest.raises(RuntimeError, match="D1_TRANSIENT"):
        asyncio.run(sc._d1_get(client, "/x"))

    now[0] += timedelta(seconds=31)
    for _ in range(3):
        asyncio.run(sc._d1_get(client, "/x"))

    now[0] += timedelta(seconds=31)
    with pytest.raises(RuntimeError, match="D1_TRANSIENT"):
        asyncio.run(sc._d1_get(client, "/x"))
    assert client.calls == 6
