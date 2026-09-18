from __future__ import annotations

from typing import Any

import httpx


BASE_URL = "https://data-api.polymarket.com/v2"
ALLOWED_ENDPOINTS = frozenset(
    {
        "/trades",
        "/activity",
        "/positions",
        "/user-pnl",
        "/user-stats",
        "/user-volume",
        "/holders",
        "/oi",
        "/live-volume",
        "/prices-history",
        "/resolutions",
        "/leaderboard",
        "/biggest-winners",
        "/status",
    }
)


class PolymarketV2Adapter:
    """Public, unauthenticated, GET-only Data API v2 client."""

    def __init__(self, *, timeout_s: float = 10.0) -> None:
        self.timeout_s = float(timeout_s)

    def url_for(self, endpoint: str) -> str:
        if not isinstance(endpoint, str) or "://" in endpoint:
            raise ValueError("endpoint must be a relative Data API v2 path")
        normalized = "/" + endpoint.lstrip("/")
        if normalized not in ALLOWED_ENDPOINTS:
            raise ValueError(f"endpoint is not allowed for EDGE LAB: {normalized}")
        return BASE_URL + normalized

    async def get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        url = self.url_for(endpoint)
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=False) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("unexpected Polymarket Data API response")
        return payload
