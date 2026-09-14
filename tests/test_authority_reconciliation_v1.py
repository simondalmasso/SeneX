from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest import mock

from senecio_polymarket.backend import authority_seal as aseal
from senecio_polymarket.backend import authority_snapshot as snap
from senecio_polymarket.backend import readiness_contract
from senecio_polymarket.backend import supabase_client as sc

IDENTITY = {
    "source_commit": "a" * 40,
    "source_tree": "b" * 40,
    "build_digest": "sha256:" + "c" * 64,
}
WC = sc.AUTHORITY_MUTATION_CONTRACT


def _row(row_id: int, ts: str) -> dict:
    return {
        "id": str(row_id),
        "ts": ts,
        "symbol": "BTCUSDT",
        "prediction": "LONG",
        "confidence": 0.6,
        "price_now": 100.0,
        "outcome": "WIN",
        "exchange_used": "okx",
        "audit": {},
    }


class ScopedCountSealTests(unittest.TestCase):
    def test_scoped_count_seal_isolated_from_global(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "os.environ", {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
        ):
            aseal.save_count_state(
                7,
                {"ts": "2026-09-13T20:00:00+00:00", "id": "9"},
                identity=IDENTITY,
                writer_contract=WC,
                scope="BTCUSDT_EXACT_COUNT",
            )
            self.assertNotEqual(
                aseal.count_path("BTCUSDT_EXACT_COUNT"), aseal.count_path()
            )
            loaded = aseal.load_count_state(
                identity=IDENTITY,
                writer_contract=WC,
                scope="BTCUSDT_EXACT_COUNT",
            )
            self.assertEqual(loaded["row_count"], 7)
            self.assertEqual(loaded["scope"], "BTCUSDT_EXACT_COUNT")


class AuthoritySnapshotReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        snap.STORE.clear()

    def tearDown(self) -> None:
        snap.STORE.clear()

    def test_snapshot_reports_scoped_count_mismatch_in_diagnostic_mode(self) -> None:
        rows = [
            _row(1, "2026-09-13T20:00:00+00:00"),
            _row(2, "2026-09-13T21:00:00+00:00"),
        ]

        async def fake_count(symbol=None):
            return 99 if symbol is None else 3

        with mock.patch.dict("os.environ", {"SENEX_AUTHORITY_RECONCILIATION_ENFORCE": "0"}, clear=False), \
             mock.patch.object(sc, "fetch_authority_history", new=mock.AsyncMock(return_value=rows)), \
             mock.patch.object(sc, "count_predictions_exact", new=fake_count), \
             mock.patch.object(sc, "fetch_predictions", new=mock.AsyncMock(return_value=rows)), \
             mock.patch.object(snap, "runtime_provenance", return_value={"exact": True}):
            captured = asyncio.run(
                snap.STORE._capture_complete(
                    "BTCUSDT", lambda score: {
                        "trade_mode": "PAPER",
                        "live_capital_locked": True,
                        "orders_enabled": False,
                    }
                )
            )
        self.assertFalse(captured.score["authority_reconciled"])
        self.assertEqual(captured.score["authority_scope_exact_count"], 3)

    def test_snapshot_rejects_scoped_count_mismatch_when_enforced(self) -> None:
        rows = [_row(1, "2026-09-13T20:00:00+00:00"), _row(2, "2026-09-13T21:00:00+00:00")]
        async def fake_count(symbol=None):
            return 99 if symbol is None else 3
        with mock.patch.dict("os.environ", {"SENEX_AUTHORITY_RECONCILIATION_ENFORCE": "1"}, clear=False), \
             mock.patch.object(sc, "fetch_authority_history", new=mock.AsyncMock(return_value=rows)), \
             mock.patch.object(sc, "count_predictions_exact", new=fake_count), \
             mock.patch.object(sc, "fetch_predictions", new=mock.AsyncMock(return_value=rows)), \
             mock.patch.object(snap, "runtime_provenance", return_value={"exact": True}):
            with self.assertRaisesRegex(snap.AuthoritySnapshotRefreshError, "AUTHORITY_RECONCILIATION_MISMATCH"):
                asyncio.run(snap.STORE._capture_complete("BTCUSDT", lambda score: {
                    "trade_mode": "PAPER", "live_capital_locked": True, "orders_enabled": False,
                }))

    def test_snapshot_accepts_matching_scoped_count_with_different_global_total(self) -> None:
        rows = [
            _row(1, "2026-09-13T20:00:00+00:00"),
            _row(2, "2026-09-13T21:00:00+00:00"),
        ]

        async def fake_count(symbol=None):
            return 99 if symbol is None else 2

        with mock.patch.object(sc, "fetch_authority_history", new=mock.AsyncMock(return_value=rows)), \
             mock.patch.object(sc, "count_predictions_exact", new=fake_count), \
             mock.patch.object(sc, "fetch_predictions", new=mock.AsyncMock(return_value=rows)), \
             mock.patch.object(snap, "runtime_provenance", return_value={"exact": True}):
            captured = asyncio.run(
                snap.STORE._capture_complete(
                    "BTCUSDT", lambda score: {
                        "trade_mode": "PAPER",
                        "live_capital_locked": True,
                        "orders_enabled": False,
                    }
                )
            )

        self.assertEqual(captured.authority_history_rows, 2)
        self.assertEqual(captured.exact_total_predictions, 99)
        self.assertTrue(captured.score["authority_reconciled"])
        self.assertEqual(captured.score["authority_scope_exact_count"], 2)

    def test_readiness_exposes_exact_count_but_does_not_require_it_yet(self) -> None:
        payload = readiness_contract.build_readiness_contract(
            {
                "authority_history_complete": True,
                "exact_count_complete": False,
                "provenance": {"exact": True},
                "live_gate": {
                    "trade_mode": "PAPER",
                    "live_capital_locked": True,
                    "orders_enabled": False,
                },
                "snapshot_id": "s",
                "generation": 1,
                "canonical_sha256": "sha256:" + "1" * 64,
            },
            {"snapshot_stale": False, "last_refresh_error": None},
            oracle_started=True,
            adapters={},
        )
        self.assertFalse(payload["checks"]["exact_count_required_for_readiness"])
        self.assertTrue(payload["checks"]["authority_history_complete"])


if __name__ == "__main__":
    unittest.main()


class ScopedIdDeltaTests(unittest.TestCase):
    def test_scoped_id_delta_filters_symbol_and_advances_by_id(self) -> None:
        calls = []

        class R:
            status_code = 200
            def json(self):
                return [{"id": "7", "ts": "2026-09-13T18:00:00+00:00"}]

        async def fake_get(_client, _path, *, params=None):
            calls.append(dict(params or {}))
            return R()

        with mock.patch.object(sc, "_get_client", return_value=object()), \
             mock.patch.object(sc, "_d1_get", new=fake_get):
            rows = asyncio.run(
                sc._scoped_id_delta("BTCUSDT", ("2026-09-13T20:00:00+00:00", "5"))
            )

        self.assertEqual(rows, [("2026-09-13T18:00:00+00:00", "7")])
        self.assertEqual(calls[0]["id"], "gt.5")
        self.assertEqual(calls[0]["order"], "id.asc")
        self.assertEqual(calls[0]["symbol"], "eq.BTCUSDT")
