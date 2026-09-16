from __future__ import annotations

import asyncio

import httpx

from senecio_polymarket.backend import main_real
from senecio_polymarket.backend import main as legacy_main


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


def test_legacy_app_does_not_publish_antifragility_routes() -> None:
    mounted_paths = {
        getattr(route, "path", "")
        for route in legacy_main.app.routes
    }
    assert not any(path.startswith("/api/antifragility/") for path in mounted_paths)


def test_legacy_app_does_not_publish_research_or_final_audit_routes() -> None:
    mounted_paths = {
        getattr(route, "path", "")
        for route in legacy_main.app.routes
    }
    assert not any(path.startswith("/api/research/") for path in mounted_paths)
    assert not any(path.startswith("/api/final_audit/") for path in mounted_paths)


def test_main_real_does_not_import_legacy_main_module() -> None:
    from pathlib import Path
    source = Path(main_real.__file__).read_text(encoding="utf-8")
    assert "from . import main as legacy" not in source
    assert "legacy." not in source


def test_legacy_and_public_runtime_share_single_wiring() -> None:
    from senecio_polymarket.backend import runtime_shared
    assert legacy_main._audit is runtime_shared._audit
    assert legacy_main._bus is runtime_shared._bus
    assert legacy_main._scheduler is runtime_shared._scheduler
    assert legacy_main._engine is runtime_shared._engine
    assert legacy_main._executor is runtime_shared._executor
