from __future__ import annotations

import asyncio

import httpx

from senecio_polymarket.backend import main_real


LEGACY_MUTATING_ROUTES = {
    "/api/portfolio/kill_switch",
    "/api/portfolio/reset_kill_switch",
}


def _post(path: str) -> httpx.Response:
    async def _call() -> httpx.Response:
        transport = httpx.ASGITransport(app=main_real.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(path, json={"reason": "isolation-test"})
    return asyncio.run(_call())


def test_main_real_router_does_not_mount_legacy_post_routes() -> None:
    mounted = {
        (getattr(route, "path", ""), method)
        for route in main_real.app.routes
        for method in (getattr(route, "methods", None) or set())
    }
    for path in LEGACY_MUTATING_ROUTES:
        assert (path, "POST") not in mounted


def test_main_real_public_guard_denies_legacy_kill_switch_post() -> None:
    response = _post("/api/portfolio/kill_switch")
    assert response.status_code == 405
    assert response.headers.get("X-Senex-Public-Decision") == "DENY_UNSAFE_METHOD"
