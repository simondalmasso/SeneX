from __future__ import annotations

import asyncio
import importlib.util

import httpx

from senecio_polymarket.backend import main_real


LEGACY_MUTATING_ROUTES = {
    "/api/portfolio/kill_switch",
    "/api/portfolio/reset_kill_switch",
}
LEGACY_MODULES = (
    "senecio_polymarket.backend.main",
    "senecio_polymarket.backend.admin",
    "senecio_polymarket.backend.ws_server",
    "senecio_polymarket.backend.evidence_tracker",
)


def _post(path: str) -> httpx.Response:
    async def _call() -> httpx.Response:
        transport = httpx.ASGITransport(app=main_real.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(path, json={"reason": "isolation-test"})
    return asyncio.run(_call())


def test_public_router_does_not_mount_legacy_post_routes() -> None:
    mounted = {
        (getattr(route, "path", ""), method)
        for route in main_real.app.routes
        for method in (getattr(route, "methods", None) or set())
    }
    for path in LEGACY_MUTATING_ROUTES:
        assert (path, "POST") not in mounted


def test_public_guard_denies_legacy_kill_switch_post() -> None:
    response = _post("/api/portfolio/kill_switch")
    assert response.status_code == 405
    assert response.headers.get("X-Senex-Public-Decision") == "DENY_UNSAFE_METHOD"


def test_obsolete_legacy_modules_are_removed() -> None:
    for module_name in LEGACY_MODULES:
        assert importlib.util.find_spec(module_name) is None


def test_main_real_has_no_legacy_module_dependency() -> None:
    from pathlib import Path
    source = Path(main_real.__file__).read_text(encoding="utf-8")
    assert "from . import main" not in source
    assert "legacy." not in source
