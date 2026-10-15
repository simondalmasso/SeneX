"""TDD tests for local CSV evaluation and report provenance."""
import hashlib
import importlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from research.rea_engines.contracts import Candle, CostProfile, OrderIntent


class OneShotMarketStrategy:
    strategy_id = "one-shot-market-v1"

    def __init__(self):
        self.submitted = False

    def on_candle(self, candle, portfolio_snapshot, open_orders):
        if self.submitted:
            return []
        self.submitted = True
        return [OrderIntent("buy", "market", "0.1", tag="initial")]


class EvaluationApiTests(unittest.TestCase):
    def test_evaluation_public_api_is_exposed(self):
        module = importlib.import_module("research.rea_engines.evaluation")
        for name in ("load_ohlcv_csv", "evaluate_strategies", "canonical_report_sha256", "EvaluationError"):
            with self.subTest(api=name):
                self.assertTrue(hasattr(module, name), f"missing evaluation API: {name}")

    def _csv_bytes(self):
        return (
            b"timestamp,open,high,low,close,volume\n"
            b"2026-01-01T00:00:00.000Z,100,102,98,100,10\n"
            b"2026-01-01T04:00:00.000Z,100,103,99,102,12\n"
            b"2026-01-01T08:00:00.000Z,102,104,101,103,11\n"
        )

    def _load(self, path, expected_sha256=None):
        module = importlib.import_module("research.rea_engines.evaluation")
        try:
            return module.load_ohlcv_csv(path, expected_sha256=expected_sha256)
        except NotImplementedError as exc:
            self.fail(f"local CSV loader is not implemented: {exc}")

    def test_csv_loader_preserves_rows_and_checks_exact_source_digest(self):
        raw = self._csv_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "candles.csv"
            path.write_bytes(raw)
            candles, observed = self._load(path, expected_sha256=digest)
            self.assertEqual(len(candles), 3)
            self.assertEqual(observed, digest)
            self.assertEqual(candles[0].timestamp, "2026-01-01T00:00:00.000000Z")
            self.assertEqual(candles[1].close, Decimal("102"))
            with self.assertRaises(importlib.import_module("research.rea_engines.evaluation").EvaluationError):
                self._load(path, expected_sha256="0" * 64)

    def test_missing_or_malformed_csv_fails_closed(self):
        module = importlib.import_module("research.rea_engines.evaluation")
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.csv"
            try:
                module.load_ohlcv_csv(missing)
            except module.EvaluationError:
                pass
            except NotImplementedError as exc:
                self.fail(f"missing-file validation is not implemented: {exc}")
            else:
                self.fail("missing input file must be rejected")
            bad = Path(td) / "bad.csv"
            bad.write_text("timestamp,open,high,low,close,volume\nnot-a-time,1,1,1,1,0\n", encoding="utf-8")
            with self.assertRaises(module.EvaluationError):
                self._load(bad)

    def test_canonical_report_hash_excludes_its_own_digest(self):
        module = importlib.import_module("research.rea_engines.evaluation")
        payload = {"schema_version": 1, "value": "10", "report_sha256": "old"}
        try:
            digest = module.canonical_report_sha256(payload)
        except NotImplementedError as exc:
            self.fail(f"canonical report hashing is not implemented: {exc}")
        self.assertEqual(len(digest), 64)
        payload["report_sha256"] = digest
        self.assertEqual(module.canonical_report_sha256(payload), digest)

    def test_cli_requires_hash_and_refuses_to_overwrite_without_flag(self):
        try:
            cli = importlib.import_module("research.rea_engines.cli")
        except ModuleNotFoundError as exc:
            self.fail(f"local-only evaluation CLI is missing: {exc}")
        import contextlib
        import io
        import json
        raw = self._csv_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "candles.csv"
            output = Path(td) / "report.json"
            source.write_bytes(raw)
            argv = [
                "--input", str(source), "--expected-sha256", digest,
                "--output", str(output), "--no-benchmarks",
                "--starting-cash", "1000", "--max-inventory", "1", "--max-fill-qty", "1",
                "--oos-start", "2026-01-01T00:00:00Z", "--oos-end", "2026-01-01T08:00:00Z",
            ]
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(cli.main(argv), 0)
            saved = output.read_bytes()
            payload = json.loads(saved.decode("utf-8"))
            self.assertEqual(payload["safety"]["network_calls"], 0)
            self.assertEqual(payload["safety"]["live_orders_submitted"], 0)
            self.assertEqual(payload["report_sha256"], cli.canonical_report_sha256(payload))
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(cli.main(argv), 2)
            self.assertEqual(output.read_bytes(), saved)
            self.assertIn("already exists", stderr.getvalue())

    def test_benchmarks_are_separate_and_quote_name_matches_allocation(self):
        module = importlib.import_module("research.rea_engines.evaluation")
        candles = [
            Candle("2026-01-01T00:00:00Z", "100", "101", "99", "100", "10"),
            Candle("2026-01-01T04:00:00Z", "100", "103", "99", "102", "10"),
            Candle("2026-01-01T08:00:00Z", "102", "104", "101", "103", "10"),
        ]
        result = module.evaluate_strategies(
            candles, "b" * 64,
            starting_cash="1000", max_inventory="1", max_fill_qty="1",
            costs=CostProfile(fee_bps="10", spread_bps="5", slippage_bps="5"),
            strategy_factories={"one-shot": OneShotMarketStrategy},
            include_benchmarks=True, cost_stress_factors=(1,),
            quote_benchmark="200", fixed_qty_benchmark="0.5",
        )
        self.assertEqual(set(result["benchmarks"]), {"buy_and_hold_quote_200", "buy_and_hold_fixed_qty"})
        self.assertIn("buy_and_hold_quote_200", result["benchmark_notes"])
        self.assertIn("buy_and_hold_fixed_qty", result["benchmark_notes"])

    def test_evaluation_runs_cost_stress_and_keeps_paper_safety_flags(self):
        module = importlib.import_module("research.rea_engines.evaluation")
        candles = [
            Candle("2026-01-01T00:00:00Z", "100", "101", "99", "100", "10"),
            Candle("2026-01-01T04:00:00Z", "100", "103", "99", "102", "10"),
            Candle("2026-01-01T08:00:00Z", "102", "104", "101", "103", "10"),
        ]
        try:
            result = module.evaluate_strategies(
                candles,
                "a" * 64,
                starting_cash="1000",
                max_inventory="1",
                max_fill_qty="1",
                costs=CostProfile(fee_bps="10", spread_bps="5", slippage_bps="5"),
                strategy_factories={"one-shot": OneShotMarketStrategy},
                include_benchmarks=False,
                cost_stress_factors=(1, 2, 3),
            )
        except NotImplementedError as exc:
            self.fail(f"cost-aware evaluation is not implemented: {exc}")
        self.assertEqual(result["safety"]["network_calls"], 0)
        self.assertEqual(result["safety"]["live_orders_submitted"], 0)
        self.assertEqual(result["safety"]["EDGE"], "UNPROVEN")
        self.assertEqual(result["dataset"]["source_sha256"], "a" * 64)
        self.assertIn("config_sha256", result)
        self.assertIn("cost_profile_sha256", result)
        self.assertIn("fill_policy_sha256", result)
        self.assertEqual(len(result["config_sha256"]), 64)
        self.assertEqual(len(result["cost_profile_sha256"]), 64)
        self.assertEqual(len(result["fill_policy_sha256"]), 64)
        runs = result["strategies"]["one-shot"]["cost_stress"]
        base = Decimal(runs["1x"]["metrics"]["total_fees"]) + Decimal(runs["1x"]["metrics"]["total_spread_cost"]) + Decimal(runs["1x"]["metrics"]["total_slippage_cost"])
        double = Decimal(runs["2x"]["metrics"]["total_fees"]) + Decimal(runs["2x"]["metrics"]["total_spread_cost"]) + Decimal(runs["2x"]["metrics"]["total_slippage_cost"])
        triple = Decimal(runs["3x"]["metrics"]["total_fees"]) + Decimal(runs["3x"]["metrics"]["total_spread_cost"]) + Decimal(runs["3x"]["metrics"]["total_slippage_cost"])
        self.assertLessEqual(base, double)
        self.assertLessEqual(double, triple)
        self.assertEqual(result["report_sha256"], module.canonical_report_sha256(result))


if __name__ == "__main__":
    unittest.main()
