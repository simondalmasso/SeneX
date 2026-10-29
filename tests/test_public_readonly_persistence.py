from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from senecio_polymarket.backend import authority_seal as seal
from senecio_polymarket.backend import supabase_client as sc

IDENTITY = {
    "source_commit": "a" * 40,
    "source_tree": "b" * 40,
    "build_digest": "sha256:" + "c" * 64,
}
ROW = {
    "id": "1",
    "ts": "2026-10-01T00:00:00Z",
    "symbol": "BTCUSDT",
    "prediction": "FLAT",
}
CURSOR = {"ts": ROW["ts"], "id": ROW["id"]}


class PublicReadOnlyPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prior_policy = sc.set_persistent_writes_allowed(True)
        sc.reset_r7b_incremental_state_for_tests()

    def tearDown(self) -> None:
        sc.reset_r7b_incremental_state_for_tests()
        sc.set_persistent_writes_allowed(self.prior_policy)

    def test_authority_refresh_never_rewrites_existing_seal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
        ), mock.patch.object(sc, "_runtime_identity", return_value=IDENTITY):
            seal.save_authority_state(
                "BTCUSDT", [ROW], CURSOR,
                identity=IDENTITY, writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
            )
            path = seal.authority_path("BTCUSDT")
            before = path.read_bytes()
            before_mtime = path.stat().st_mtime_ns
            sc.set_persistent_writes_allowed(False)
            with (
                mock.patch.object(sc, "_fetch_authority_delta_raw", new=mock.AsyncMock(return_value=[])),
                mock.patch.object(sc, "_mutable_ids", return_value=[]),
                mock.patch.object(
                    seal, "save_authority_state",
                    side_effect=AssertionError("read-only refresh attempted to rewrite the seal"),
                ),
            ):
                rows = asyncio.run(sc.fetch_authority_history("BTCUSDT"))
            self.assertEqual(rows, [ROW])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_mtime_ns, before_mtime)


    def test_low_level_authority_seal_writer_rejects_read_only_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
        ):
            sc.set_persistent_writes_allowed(False)
            with self.assertRaisesRegex(
                seal.AuthoritySealWritesDisabledError, "AUTHORITY_SEAL_WRITE_DISABLED"
            ):
                seal.save_authority_state(
                    "BTCUSDT", [ROW], CURSOR,
                    identity=IDENTITY, writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
                )
            self.assertFalse(seal.authority_path("BTCUSDT").exists())

    def test_missing_authority_seal_fails_closed_without_bootstrap_writes(self) -> None:
        sc.set_persistent_writes_allowed(False)
        with (
            mock.patch.object(sc, "_runtime_identity", return_value=IDENTITY),
            mock.patch.object(
                seal, "load_authority_state",
                side_effect=seal.AuthoritySealMissingError("missing"),
            ),
            mock.patch.object(seal, "assert_bootstrap_permitted") as allowed,
            mock.patch.object(seal, "record_bootstrap_attempt") as recorded,
            mock.patch.object(seal, "save_authority_state") as saved,
        ):
            with self.assertRaisesRegex(
                sc.AuthorityHistoryIncompleteError, "AUTHORITY_DURABLE_SEAL_MISSING_READ_ONLY"
            ):
                asyncio.run(sc.fetch_authority_history("BTCUSDT"))
        allowed.assert_not_called()
        recorded.assert_not_called()
        saved.assert_not_called()

    def test_exact_count_refresh_does_not_rewrite_existing_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
        ), mock.patch.object(sc, "_runtime_identity", return_value=IDENTITY):
            seal.save_count_state(
                1000, CURSOR, identity=IDENTITY,
                writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
            )
            path = seal.count_path()
            before = path.read_bytes()
            before_mtime = path.stat().st_mtime_ns
            sc.set_persistent_writes_allowed(False)
            with (
                mock.patch.object(sc, "_global_id_delta", new=mock.AsyncMock(return_value=[])),
                mock.patch.object(
                    seal, "save_count_state",
                    side_effect=AssertionError("read-only refresh attempted to rewrite count"),
                ),
            ):
                self.assertEqual(asyncio.run(sc.count_predictions_exact()), 1000)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_mtime_ns, before_mtime)

    def test_missing_count_sidecar_fails_closed_without_bootstrap_writes(self) -> None:
        sc.set_persistent_writes_allowed(False)
        with (
            mock.patch.object(sc, "_runtime_identity", return_value=IDENTITY),
            mock.patch.object(
                seal, "load_count_state",
                side_effect=seal.AuthoritySealMissingError("missing"),
            ),
            mock.patch.object(seal, "assert_bootstrap_permitted") as allowed,
            mock.patch.object(seal, "record_bootstrap_attempt") as recorded,
            mock.patch.object(seal, "save_count_state") as saved,
        ):
            with self.assertRaisesRegex(
                sc.ExactCountUnavailableError, "AUTHORITY_DURABLE_COUNT_MISSING_READ_ONLY"
            ):
                asyncio.run(sc.count_predictions_exact())
        allowed.assert_not_called()
        recorded.assert_not_called()
        saved.assert_not_called()

    def test_quota_breaker_files_are_not_created_or_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d1-quota-breaker.json"
            with mock.patch.object(sc, "_quota_breaker_file", return_value=path):
                sc.set_persistent_writes_allowed(False)
                sc._persist_quota_breaker_state()
                self.assertFalse(path.exists())
                path.write_bytes(b"keep-this-file")
                sc._clear_quota_breaker_state()
                self.assertEqual(path.read_bytes(), b"keep-this-file")

    def test_direct_supabase_writer_apis_fail_closed_before_network(self) -> None:
        sc.set_persistent_writes_allowed(False)
        prediction = {"timestamp": "2026-10-01T00:00:00Z", "symbol": "BTCUSDT", "prediction": "FLAT"}
        with mock.patch.object(sc, "_get_client", side_effect=AssertionError("network client must not be used")) as client:
            self.assertIsNone(asyncio.run(sc.ensure_prediction_persisted(prediction)))
            self.assertIsNone(asyncio.run(sc.insert_prediction(prediction)))
            self.assertFalse(asyncio.run(sc.update_outcome_dual(1, "WIN", "WIN", 101.0, 102.0)))
        client.assert_not_called()


    def test_public_lifespan_disables_and_restores_persistent_writes(self) -> None:
        from senecio_polymarket.backend import main_real

        async def assert_policy_during_snapshot(*args, **kwargs):
            self.assertFalse(sc.persistent_writes_allowed())
            return None

        async def idle_refresh(_symbol: str) -> None:
            await asyncio.Event().wait()

        sc.set_persistent_writes_allowed(True)
        with (
            mock.patch.dict(os.environ, {"SENEX_ENABLE_SYNTHETIC_DEMO": ""}, clear=False),
            mock.patch.object(main_real._poly, "start", new=mock.AsyncMock()),
            mock.patch.object(main_real._poly, "stop", new=mock.AsyncMock()),
            mock.patch.object(main_real._boros, "start", new=mock.AsyncMock()),
            mock.patch.object(main_real._boros, "stop", new=mock.AsyncMock()),
            mock.patch.object(main_real._kalshi, "start", new=mock.AsyncMock()),
            mock.patch.object(main_real._kalshi, "stop", new=mock.AsyncMock()),
            mock.patch.object(main_real, "_snapshot", new=mock.AsyncMock(side_effect=assert_policy_during_snapshot)) as snapshot,
            mock.patch.object(main_real, "_authority_refresh_loop", new=idle_refresh),
        ):
            async def exercise_lifespan() -> None:
                async with main_real.real_lifespan(main_real.app):
                    self.assertFalse(sc.persistent_writes_allowed())

            asyncio.run(exercise_lifespan())
            snapshot.assert_awaited_once_with("BTCUSDT", force=True)

        self.assertTrue(sc.persistent_writes_allowed())


if __name__ == "__main__":
    unittest.main()
