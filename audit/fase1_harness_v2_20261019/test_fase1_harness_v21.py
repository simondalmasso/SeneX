import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fase1_harness as h


def fake_runtime(n=10):
    rows = [
        {"id": i, "symbol": "BTCUSDT", "outcome": "WIN" if i % 2 else "LOSS",
         "prediction": "LONG", "confidence": 0.6}
        for i in range(n)
    ]
    return rows, {
        "filter_proof_qualified": lambda xs: xs,
        "independent_1h_cohort": lambda xs: xs,
        "_confidence": lambda row: row.get("confidence"),
        "_wilson_lower": lambda wins, total: 0.0,
    }


class HarnessV21Tests(unittest.TestCase):
    def test_bad_parity_fails_closed_without_verdict(self):
        rows, rt = fake_runtime(10)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = h.run_pipeline(rows, rt, "20,10,10,10")
        output = out.getvalue()
        self.assertEqual(rc, 3)
        self.assertIn("PARITY_FAIL", output)
        self.assertIn("no se emite veredicto", output)
        self.assertNotIn("VEREDICTO FASE 1", output)

    def test_good_parity_continues_to_predeclared_sample_gate(self):
        rows, rt = fake_runtime(10)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = h.run_pipeline(rows, rt, "10,5,5,10")
        output = out.getvalue()
        self.assertEqual(rc, 2)
        self.assertIn("paridad n: vivo=10 export=10 (PASS)", output)
        self.assertIn("BLOQUEO: n<100", output)
        self.assertNotIn("PARITY_FAIL", output)
        self.assertNotIn("VEREDICTO FASE 1", output)

    def test_missing_identity_expectations_fail_closed_before_rows_loaded(self):
        payload = {
            "contract": "contract", "rows": [{"id": 1, "ts": "2026-01-01"}],
            "row_count": 1, "rows_hash": "hash", "cursor": {"id": 1},
            "seal_hash": "hash", "source_commit": "c" * 40,
            "source_tree": "a" * 40, "build_digest": "sha256:" + "b" * 64,
        }
        rt = {"SEAL_CONTRACT": "contract", "_row_key": lambda r: (r["ts"], r["id"]),
              "_sha": lambda value: "hash", "_cursor_for_rows": lambda rows: {"id": rows[-1]["id"]}}
        ok, checks = h.verify_seal(payload, rt, None)
        values = {name: value for name, value, _ in checks}
        self.assertFalse(ok)
        self.assertIsNone(values["C7a source_commit"])
        self.assertIsNone(values["C7b source_tree"])
        self.assertIsNone(values["C7c build_digest"])
        complete = all(values[k] is True for k in (
            "C7a source_commit", "C7b source_tree", "C7c build_digest"))
        self.assertFalse(complete)

        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as fh:
            json.dump(payload, fh)
            seal_path = fh.name
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                loaded = h.load_rows(seal_path, rt, None)
        finally:
            Path(seal_path).unlink(missing_ok=True)
        self.assertIsNone(loaded)
        self.assertIn("SKIP  C7a source_commit", out.getvalue())
        self.assertIn("SKIP  C7b source_tree", out.getvalue())
        self.assertIn("SKIP  C7c build_digest", out.getvalue())
        self.assertIn("IDENTITY_COMPLETE=NO", out.getvalue())
        self.assertIn("CHECKS_APPLICABLE=FAIL", out.getvalue())
        self.assertIn("BLOQUEO:", out.getvalue())

    def test_unsealed_export_cannot_produce_verdict(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", encoding="utf-8", delete=False) as fh:
            fh.write('{"id": 1, "outcome": "WIN"}\n')
            export_path = fh.name
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                loaded = h.load_rows(export_path, {}, "c" * 40, "a" * 40, "sha256:" + "b" * 64)
        finally:
            Path(export_path).unlink(missing_ok=True)
        self.assertIsNone(loaded)
        self.assertIn("CSV/JSONL", out.getvalue())
        self.assertNotIn("VEREDICTO FASE 1", out.getvalue())

    def test_identity_mismatch_fails_closed(self):
        expected_commit = "c" * 40
        expected_tree = "a" * 40
        expected_build = "sha256:" + "b" * 64
        payload = {
            "contract": "contract", "rows": [{"id": 1, "ts": "2026-01-01"}],
            "row_count": 1, "rows_hash": "hash", "cursor": {"id": 1},
            "seal_hash": "hash", "source_commit": expected_commit,
            "source_tree": expected_tree, "build_digest": expected_build,
        }
        rt = {"SEAL_CONTRACT": "contract", "_row_key": lambda r: (r["ts"], r["id"]),
              "_sha": lambda value: "hash", "_cursor_for_rows": lambda rows: {"id": rows[-1]["id"]}}
        good_ok, good_checks = h.verify_seal(
            payload, rt, expected_commit, expected_tree, expected_build)
        self.assertTrue(good_ok)
        self.assertTrue(all(value is True for _, value, _ in good_checks))

        for missing_field, check_name in (
            ("source_tree", "C7b source_tree"),
            ("build_digest", "C7c build_digest"),
        ):
            missing = dict(payload)
            missing.pop(missing_field)
            ok, checks = h.verify_seal(
                missing, rt, expected_commit, expected_tree, expected_build)
            self.assertFalse(ok)
            values = {name: value for name, value, _ in checks}
            self.assertFalse(values[check_name])

        payload["source_tree"] = "d" * 40
        payload["build_digest"] = "sha256:" + "e" * 64
        ok, checks = h.verify_seal(
            payload, rt, expected_commit, expected_tree, expected_build)
        values = {name: value for name, value, _ in checks}
        self.assertFalse(ok)
        self.assertTrue(values["C7a source_commit"])
        self.assertFalse(values["C7b source_tree"])
        self.assertFalse(values["C7c build_digest"])



if __name__ == "__main__":
    unittest.main(verbosity=2)
