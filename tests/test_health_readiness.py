"""Deterministic health / readiness tests for the public read-only runtime.

HEALTH = process liveness (200 while alive, exposes safety invariants).
READINESS = authority/provenance/safety state (200 only when every required
gate is true; otherwise 503 with machine-readable reasons).

These tests call the ASGI app WITHOUT entering the lifespan (no network,
no adapters, no Supabase) so they stay deterministic. The authority store
has no valid generation in this state -> readiness must be fail-closed 503.
"""
from __future__ import annotations

import asyncio
import unittest

import httpx

from senecio_polymarket.backend import main_real
from senecio_polymarket.backend import readiness_contract
from senecio_polymarket.backend.paper_lock import safety_projection


def _get(path: str) -> httpx.Response:
    async def _call() -> httpx.Response:
        transport = httpx.ASGITransport(app=main_real.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)
    return asyncio.run(_call())


def _post(path: str, body: dict) -> httpx.Response:
    async def _call() -> httpx.Response:
        transport = httpx.ASGITransport(app=main_real.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(path, json=body)
    return asyncio.run(_call())


class HealthReadinessTests(unittest.TestCase):
    def test_healthz_alive_with_safety_invariants(self) -> None:
        response = _get("/healthz")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "alive")
        safety = payload["safety"]
        self.assertEqual(safety["trade_mode"], "PAPER")
        self.assertFalse(safety["orders_enabled"])
        self.assertTrue(safety["live_capital_locked"])
        self.assertTrue(safety["hard_paper_lock"])

    def test_readyz_fail_closed_without_authority(self) -> None:
        response = _get("/readyz")
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["status"], "not_ready")
        self.assertEqual(payload["reason"], "NO_VALID_AUTHORITY_GENERATION")
        # safety invariants still exposed on the failure path
        self.assertEqual(payload["safety"]["trade_mode"], "PAPER")
        self.assertTrue(payload["safety"]["hard_paper_lock"])

    def test_health_not_red_because_authority_unavailable(self) -> None:
        # Liveness must not absorb readiness failures: /healthz stays 200 even
        # when /readyz is 503 (checked together).
        self.assertEqual(_get("/healthz").status_code, 200)
        self.assertEqual(_get("/readyz").status_code, 503)

    def test_authority_routes_fail_closed_503_without_generation(self) -> None:
        for path in (
            "/api/oracle/score",
            "/api/authority/snapshot",
            "/api/oracle/state",
            "/api/portfolio/live_gate",
        ):
            response = _get(path)
            self.assertEqual(response.status_code, 503, path)
            self.assertIn("detail", response.json())

    def test_paper_view_honest_when_pipeline_absent(self) -> None:
        response = _get("/api/paper/state")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "NO_PORTFOLIO_PIPELINE")
        self.assertEqual(payload["execution"]["status"], "UNKNOWN")
        self.assertEqual(payload["edge"]["status"], "UNPROVEN")
        self.assertTrue(payload["hypothetical"])
        safety = payload["safety"]
        self.assertTrue(safety["hard_paper_lock"])

    def test_paper_trades_bounded_and_hypothetical(self) -> None:
        response = _get("/api/paper/trades?limit=5")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["bounded"])
        self.assertLessEqual(payload["count"], 5)
        self.assertTrue(payload["hypothetical"])

    def test_public_method_guard_rejects_mutations(self) -> None:
        response = _post("/api/paper/state", {"x": 1})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            response.headers.get("X-Senex-Public-Decision"), "DENY_UNSAFE_METHOD"
        )

    def test_readiness_contract_requires_provenance_exact_and_locks(self) -> None:
        snapshot = {
            "authority_history_complete": True,
            "exact_count_complete": True,
            "provenance": {"exact": False},
            "live_gate": {
                "trade_mode": "PAPER",
                "live_capital_locked": True,
                "orders_enabled": False,
            },
            "snapshot_id": "s",
            "generation": 1,
            "canonical_sha256": "sha256:" + "1" * 64,
        }
        payload = readiness_contract.build_readiness_contract(
            snapshot,
            {"snapshot_stale": False, "last_refresh_error": None},
            oracle_started=True,
            adapters={},
        )
        self.assertEqual(payload["status"], "not_ready")
        self.assertFalse(payload["checks"]["provenance_exact"])
        self.assertTrue(payload["safety"]["hard_paper_lock"])

    def test_readiness_contract_ready_when_all_gates_true(self) -> None:
        snapshot = {
            "authority_history_complete": True,
            "exact_count_complete": True,
            "provenance": {"exact": True},
            "live_gate": {
                "trade_mode": "PAPER",
                "live_capital_locked": True,
                "orders_enabled": False,
            },
            "snapshot_id": "s",
            "generation": 7,
            "canonical_sha256": "sha256:" + "2" * 64,
        }
        payload = readiness_contract.build_readiness_contract(
            snapshot,
            {"snapshot_stale": False, "last_refresh_error": None},
            oracle_started=True,
            adapters={"polymarket": {"status": "LIVE_REST", "stale": False}},
        )
        self.assertEqual(payload["status"], "ready")
        self.assertIsNone(payload["reason"])
        self.assertEqual(payload["safety"], safety_projection())

    def test_runtime_provenance_endpoint_fail_closed_without_identity(self) -> None:
        response = _get("/api/runtime/provenance")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["exact"])
        self.assertFalse(payload["provider_oci_attestation"]["self_proof"])
        self.assertEqual(
            payload["provider_oci_attestation"]["role"], "EXTERNAL_ATTESTATION_ONLY"
        )


if __name__ == "__main__":
    unittest.main()
