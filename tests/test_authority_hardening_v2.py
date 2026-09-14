from __future__ import annotations

import json
import os
import tempfile
import unittest

from senecio_polymarket.backend import authority_seal as aseal

IDENTITY = {
    "source_commit": "a" * 40,
    "source_tree": "b" * 40,
    "build_digest": "sha256:" + "c" * 64,
}
WRITER = "APPEND_ROWS_AND_MUTATE_DIRECTIONAL_ONLY_UNTIL_PROOF_QUALIFIED"
ROW = {"id": "1", "ts": "2026-09-13T20:00:00+00:00", "symbol": "BTCUSDT"}
CURSOR = {"ts": ROW["ts"], "id": ROW["id"]}
ENV_KEYS = (
    "SENEX_AUTHORITY_SEAL_DIR",
    "SENEX_AUTHORITY_SEAL_KEY",
    "SENEX_AUTHORITY_BOOTSTRAP_ALLOWED",
    "SENEX_AUTHORITY_BOOTSTRAP_COOLDOWN_SEC",
)


class AuthorityHardeningV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_env = {key: os.environ.get(key) for key in ENV_KEYS}
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["SENEX_AUTHORITY_SEAL_DIR"] = self._tmp.name
        os.environ.pop("SENEX_AUTHORITY_SEAL_KEY", None)

    def tearDown(self) -> None:
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()
    def test_save_rejects_duplicate_ts_id(self) -> None:
        duplicate = dict(ROW, outcome="WIN")
        with self.assertRaisesRegex(aseal.AuthoritySealCorruptError, "AUTHORITY_SEAL_DUPLICATE_CURSOR"):
            aseal.save_authority_state(
                "BTCUSDT", [ROW, duplicate], CURSOR,
                identity=IDENTITY, writer_contract=WRITER,
            )

    def test_load_rejects_duplicate_ts_id_even_if_hash_recomputed(self) -> None:
        duplicate = dict(ROW, outcome="LOSS")
        aseal.save_authority_state(
            "BTCUSDT", [ROW], CURSOR, identity=IDENTITY, writer_contract=WRITER
        )
        path = aseal.authority_path("BTCUSDT")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["rows"] = [dict(ROW), duplicate]
        payload["row_count"] = 2
        payload["rows_hash"] = aseal._sha(payload["rows"])
        unsigned = dict(payload)
        unsigned.pop("seal_hash", None)
        payload["seal_hash"] = aseal._sha(unsigned)
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(aseal.AuthoritySealCorruptError, "AUTHORITY_DURABLE_SEAL_DUPLICATE_CURSOR"):
            aseal.load_authority_state(
                "BTCUSDT", identity=IDENTITY, writer_contract=WRITER
            )

    def test_legacy_sha_is_rejected_when_key_is_configured(self) -> None:
        aseal.save_authority_state(
            "C1", [ROW], CURSOR, identity=IDENTITY, writer_contract=WRITER
        )
        os.environ["SENEX_AUTHORITY_SEAL_KEY"] = "A" * 48
        with self.assertRaisesRegex(aseal.AuthoritySealCorruptError, "HASH_MISMATCH"):
            aseal.load_authority_state("C1", identity=IDENTITY, writer_contract=WRITER)

    def test_forged_sha_is_rejected_when_key_is_configured(self) -> None:
        os.environ["SENEX_AUTHORITY_SEAL_KEY"] = "A" * 48
        aseal.save_authority_state(
            "C2", [ROW], CURSOR, identity=IDENTITY, writer_contract=WRITER
        )
        path = aseal.authority_path("C2")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["rows"][0]["symbol"] = "ETHUSDT"
        payload["rows_hash"] = aseal._sha(payload["rows"])
        unsigned = dict(payload)
        unsigned.pop("seal_hash", None)
        payload["seal_hash"] = aseal._sha(unsigned)
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(aseal.AuthoritySealCorruptError, "HASH_MISMATCH"):
            aseal.load_authority_state("C2", identity=IDENTITY, writer_contract=WRITER)

    def test_hmac_is_rejected_without_key(self) -> None:
        os.environ["SENEX_AUTHORITY_SEAL_KEY"] = "A" * 48
        aseal.save_authority_state(
            "C3", [ROW], CURSOR, identity=IDENTITY, writer_contract=WRITER
        )
        os.environ.pop("SENEX_AUTHORITY_SEAL_KEY", None)
        with self.assertRaisesRegex(aseal.AuthoritySealCorruptError, "HASH_MISMATCH"):
            aseal.load_authority_state("C3", identity=IDENTITY, writer_contract=WRITER)

    def test_key_rotation_is_rejected(self) -> None:
        os.environ["SENEX_AUTHORITY_SEAL_KEY"] = "A" * 48
        aseal.save_authority_state(
            "C4", [ROW], CURSOR, identity=IDENTITY, writer_contract=WRITER
        )
        os.environ["SENEX_AUTHORITY_SEAL_KEY"] = "B" * 48
        with self.assertRaisesRegex(aseal.AuthoritySealCorruptError, "HASH_MISMATCH"):
            aseal.load_authority_state("C4", identity=IDENTITY, writer_contract=WRITER)

    def test_prefixes_and_happy_paths(self) -> None:
        legacy = aseal.save_authority_state(
            "C5", [ROW], CURSOR, identity=IDENTITY, writer_contract=WRITER
        )
        self.assertTrue(str(legacy["seal_hash"]).startswith("sha256:"))
        self.assertTrue(aseal.load_authority_state("C5", identity=IDENTITY, writer_contract=WRITER)["seal_hash"].startswith("sha256:"))
        os.environ["SENEX_AUTHORITY_SEAL_KEY"] = "A" * 48
        keyed = aseal.save_authority_state(
            "C6", [ROW], CURSOR, identity=IDENTITY, writer_contract=WRITER
        )
        self.assertTrue(str(keyed["seal_hash"]).startswith("hmac-sha256:"))
        self.assertTrue(aseal.load_authority_state("C6", identity=IDENTITY, writer_contract=WRITER)["seal_hash"].startswith("hmac-sha256:"))

    def test_weak_key_rejected_on_save_and_load(self) -> None:
        os.environ["SENEX_AUTHORITY_SEAL_KEY"] = "x" * 8
        with self.assertRaisesRegex(aseal.AuthoritySealError, "KEY_TOO_SHORT"):
            aseal.save_authority_state(
                "C7S", [ROW], CURSOR, identity=IDENTITY, writer_contract=WRITER
            )
        os.environ["SENEX_AUTHORITY_SEAL_KEY"] = "A" * 48
        aseal.save_authority_state(
            "C7L", [ROW], CURSOR, identity=IDENTITY, writer_contract=WRITER
        )
        os.environ["SENEX_AUTHORITY_SEAL_KEY"] = "x" * 8
        with self.assertRaisesRegex(aseal.AuthoritySealError, "KEY_TOO_SHORT"):
            aseal.load_authority_state("C7L", identity=IDENTITY, writer_contract=WRITER)

    def test_bootstrap_guard_accepts_legacy_sha_during_key_migration(self) -> None:
        os.environ["SENEX_AUTHORITY_BOOTSTRAP_ALLOWED"] = "1"
        os.environ["SENEX_AUTHORITY_BOOTSTRAP_COOLDOWN_SEC"] = "0"
        aseal.record_bootstrap_attempt("BTCUSDT")
        os.environ["SENEX_AUTHORITY_SEAL_KEY"] = "A" * 48
        aseal.assert_bootstrap_permitted("BTCUSDT")


if __name__ == "__main__":
    unittest.main()
