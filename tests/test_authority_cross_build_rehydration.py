from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from senecio_polymarket.backend import authority_seal as aseal
from senecio_polymarket.backend import supabase_client as sc

OLD_IDENTITY = {
    "source_commit": "a" * 40,
    "source_tree": "b" * 40,
    "build_digest": "sha256:" + "c" * 64,
}
NEW_IDENTITY = {
    "source_commit": "d" * 40,
    "source_tree": "e" * 40,
    "build_digest": "sha256:" + "f" * 64,
}
ROW = {
    "id": "1",
    "ts": "2026-09-13T20:00:00+00:00",
    "symbol": "BTCUSDT",
    "prediction": "FLAT",
}
CURSOR = {"ts": ROW["ts"], "id": ROW["id"]}


class CrossBuildAuthorityRehydrationTests(unittest.TestCase):
    def setUp(self) -> None:
        sc.reset_r7b_incremental_state_for_tests()

    def test_cross_build_authority_requires_live_delta_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
        ):
            aseal.save_authority_state(
                "BTCUSDT",
                [ROW],
                CURSOR,
                identity=OLD_IDENTITY,
                writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
            )
            with mock.patch.object(sc, "internal_identity_projection", return_value=NEW_IDENTITY):
                with mock.patch.object(
                    sc,
                    "_fetch_authority_delta_raw",
                    new=mock.AsyncMock(side_effect=sc.AuthorityHistoryIncompleteError("LIVE_DELTA_UNAVAILABLE")),
                ):
                    with self.assertRaisesRegex(
                        sc.AuthorityHistoryIncompleteError, "LIVE_DELTA_UNAVAILABLE"
                    ):
                        asyncio.run(sc.fetch_authority_history("BTCUSDT"))

    def test_cross_build_authority_reseals_under_current_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
        ):
            aseal.save_authority_state(
                "BTCUSDT",
                [ROW],
                CURSOR,
                identity=OLD_IDENTITY,
                writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
            )
            with mock.patch.object(sc, "internal_identity_projection", return_value=NEW_IDENTITY):
                with mock.patch.object(
                    sc, "_fetch_authority_delta_raw", new=mock.AsyncMock(return_value=[])
                ):
                    rows = asyncio.run(sc.fetch_authority_history("BTCUSDT"))
            self.assertEqual(rows, [ROW])
            persisted = aseal.load_authority_state(
                "BTCUSDT",
                identity=NEW_IDENTITY,
                writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
            )
            self.assertEqual(persisted["source_commit"], NEW_IDENTITY["source_commit"])
            self.assertEqual(persisted["source_tree"], NEW_IDENTITY["source_tree"])
            self.assertEqual(persisted["build_digest"], NEW_IDENTITY["build_digest"])

    def test_cross_build_exact_count_reseals_after_live_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
        ):
            aseal.save_count_state(
                1,
                CURSOR,
                identity=OLD_IDENTITY,
                writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
            )
            with mock.patch.object(sc, "internal_identity_projection", return_value=NEW_IDENTITY):
                with mock.patch.object(
                    sc, "_global_id_delta", new=mock.AsyncMock(return_value=[])
                ):
                    count = asyncio.run(sc.count_predictions_exact())
            self.assertEqual(count, 1)
            persisted = aseal.load_count_state(
                identity=NEW_IDENTITY,
                writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
            )
            self.assertEqual(persisted["source_commit"], NEW_IDENTITY["source_commit"])
            self.assertEqual(persisted["source_tree"], NEW_IDENTITY["source_tree"])
            self.assertEqual(persisted["build_digest"], NEW_IDENTITY["build_digest"])

    def test_cross_build_exact_count_stays_fail_closed_without_live_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
        ):
            aseal.save_count_state(
                1,
                CURSOR,
                identity=OLD_IDENTITY,
                writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
            )
            with mock.patch.object(sc, "internal_identity_projection", return_value=NEW_IDENTITY):
                with mock.patch.object(
                    sc,
                    "_global_id_delta",
                    new=mock.AsyncMock(side_effect=sc.ExactCountUnavailableError("LIVE_COUNT_DELTA_UNAVAILABLE")),
                ):
                    with self.assertRaisesRegex(
                        sc.ExactCountUnavailableError, "LIVE_COUNT_DELTA_UNAVAILABLE"
                    ):
                        asyncio.run(sc.count_predictions_exact())

    def test_cross_build_stale_authority_is_rejected_before_live_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
        ):
            aseal.save_authority_state(
                "BTCUSDT", [ROW], CURSOR, identity=OLD_IDENTITY,
                writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
                created_at="2020-01-01T00:00:00Z",
                verified_at="2020-01-01T00:00:00Z",
            )
            delta = mock.AsyncMock(return_value=[])
            with mock.patch.object(sc, "internal_identity_projection", return_value=NEW_IDENTITY):
                with mock.patch.object(sc, "_fetch_authority_delta_raw", new=delta):
                    with self.assertRaisesRegex(sc.AuthorityHistoryIncompleteError, "AUTHORITY_SEAL_STALE"):
                        asyncio.run(sc.fetch_authority_history("BTCUSDT"))
            delta.assert_not_awaited()

    def test_cross_build_rejects_malformed_producer_identity_even_with_valid_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
        ):
            aseal.save_authority_state(
                "BTCUSDT", [ROW], CURSOR, identity=OLD_IDENTITY,
                writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
            )
            path = aseal.authority_path("BTCUSDT")
            payload = __import__("json").loads(path.read_text(encoding="utf-8"))
            payload["source_commit"] = "not-a-commit"
            unsigned = dict(payload); unsigned.pop("seal_hash", None)
            payload["seal_hash"] = aseal._sha(unsigned)
            path.write_text(__import__("json").dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(aseal.AuthoritySealCorruptError, "PRODUCER_SOURCE_COMMIT_INVALID"):
                aseal.load_authority_state(
                    "BTCUSDT", identity=NEW_IDENTITY,
                    writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
                )


if __name__ == "__main__":
    unittest.main()
