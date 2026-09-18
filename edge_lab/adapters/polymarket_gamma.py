from __future__ import annotations

import json
from typing import Any

import httpx

from edge_lab.equivalence import PolymarketHourlyContract


BASE_URL = "https://gamma-api.polymarket.com"
SERIES_ID_BTC_HOURLY = "10114"


def _decoded_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    raise ValueError("expected JSON array")


def parse_btc_hourly_contract(payload: dict[str, Any]) -> PolymarketHourlyContract:
    source = str(payload.get("resolutionSource") or "")
    description = str(payload.get("description") or "")
    outcomes = _decoded_list(payload.get("outcomes"))
    if outcomes != ["Up", "Down"]:
        raise ValueError("expected Up/Down outcome ordering")
    if "binance.com" not in source.lower() or "btc_usdt" not in source.lower():
        raise ValueError("official hourly contract must resolve from Binance BTC/USDT")
    if "BTC/USDT" not in description or "1 hour candle" not in description:
        raise ValueError("hourly BTC/USDT resolution rule missing")
    if "greater than or equal to" not in description.lower():
        raise ValueError("tie semantics are not explicit")
    start_ts = payload.get("eventStartTime")
    end_ts = payload.get("endDate")
    if not start_ts or not end_ts:
        raise ValueError("eventStartTime and endDate are required")

    return PolymarketHourlyContract(
        asset="BTC/USDT",
        start_ts=str(start_ts),
        end_ts=str(end_ts),
        timezone="America/New_York",
        resolution="1H_CANDLE_CLOSE_VS_OPEN",
        data_source="binance",
        tie_semantics="UP_ON_EQUAL",
        condition_id=str(payload.get("conditionId") or "") or None,
        slug=str(payload.get("slug") or "") or None,
    )


class PolymarketGammaAdapter:
    """Official public Gamma reads only; no auth and no write methods."""

    def __init__(self, *, timeout_s: float = 10.0) -> None:
        self.timeout_s = float(timeout_s)

    async def _get(self, path: str, **params: Any) -> Any:
        if not path.startswith("/") or "://" in path:
            raise ValueError("path must be a relative Gamma path")
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=False) as client:
            response = await client.get(BASE_URL + path, params=params)
            response.raise_for_status()
            return response.json()

    async def event_by_slug(self, slug: str) -> dict[str, Any]:
        payload = await self._get("/events/slug/" + slug)
        if not isinstance(payload, dict):
            raise ValueError("unexpected Gamma event payload")
        return payload

    async def btc_hourly_events(self, *, closed: bool = False, limit: int = 20) -> list[dict[str, Any]]:
        payload = await self._get(
            "/events",
            series_id=SERIES_ID_BTC_HOURLY,
            closed=str(bool(closed)).lower(),
            limit=max(1, min(int(limit), 100)),
            order="endDate",
            ascending="true",
        )
        if not isinstance(payload, list):
            raise ValueError("unexpected Gamma events payload")
        return [dict(item) for item in payload if isinstance(item, dict)]
