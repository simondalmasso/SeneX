"""Tests for the local-only REA dashboard summary export."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "research" / "rea_engines" / "export_dashboard_snapshot.py"
PANEL_JS = ROOT / "senecio_polymarket" / "frontend" / "rea_research_panel.js"
SNAPSHOT_JS = ROOT / "senecio_polymarket" / "frontend" / "rea_research_snapshot.js"
INDEX_HTML = ROOT / "senecio_polymarket" / "frontend" / "index.html"
STYLES_CSS = ROOT / "senecio_polymarket" / "frontend" / "styles.css"

SPEC = importlib.util.spec_from_file_location("rea_dashboard_snapshot_generator", GENERATOR_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot import snapshot generator from {GENERATOR_PATH}")
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class ReaDashboardSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = GENERATOR.build_snapshot(ROOT)

    def test_source_integrity_is_verified_before_display(self) -> None:
        snap = self.snapshot
        self.assertTrue(snap["integrity"]["overall_ok"])
        self.assertTrue(snap["integrity"]["report_matches_final_status"])
        self.assertTrue(snap["integrity"]["dataset_file_found"])
        self.assertTrue(snap["integrity"]["dataset_hash_matches_report"])
        self.assertTrue(snap["integrity"]["inventory_ok"])
        self.assertEqual(snap["integrity"]["inventory_rows"], snap["inventory"]["rows"])
        self.assertGreaterEqual(snap["integrity"]["inventory_rows"], 75)
        self.assertTrue(snap["integrity"]["zip_manifest_ok"])
        self.assertEqual(snap["integrity"]["zip_manifest_rows"], 48)
        self.assertEqual(snap["integrity"]["zip_manifest_mismatches"], 0)
        self.assertTrue(snap["integrity"]["eth_dataset_hash_matches_status"])

    def test_canonical_report_hash_and_base_results(self) -> None:
        self.assertEqual(
            self.snapshot["report_sha256"],
            "08cdad21c7dbc0d2c1128aca21d955d274d85f451c0715b50edf6b5113b2d89d",
        )
        results = {row["key"]: row for row in self.snapshot["results"]}
        self.assertEqual(len(results), 5)
        self.assertAlmostEqual(results["grid"]["return_pct"], 0.1625251195, places=8)
        self.assertAlmostEqual(results["dca"]["return_pct"], 1.86448221775, places=8)
        self.assertAlmostEqual(results["combo"]["return_pct"], 0.32051941346, places=8)
        self.assertEqual(results["dca"]["fills"], 179)
        self.assertGreater(results["dca"]["time_in_market_pct"], 99)
        self.assertEqual(results["buy_and_hold_fixed_qty"]["group"], "benchmark")

    def test_cost_stress_is_aggregate_only_and_complete(self) -> None:
        rows = self.snapshot["cost_stress"]
        self.assertEqual(len(rows), 15)
        factors = {row["stress_factor"] for row in rows}
        self.assertEqual(factors, {"1x", "2x", "3x"})
        for row in rows:
            self.assertIn("fills", row)  # Aggregate fill count, not the fill tape.
            self.assertNotIn("order_id", row)
            self.assertNotIn("timestamp", row)
        self.assertFalse(any("equity_curve" in row for row in rows))

    def test_annual_anomaly_is_exposed_and_grid_combo_are_withheld(self) -> None:
        self.assertFalse(self.snapshot["annual_validation"]["grid_combo_slices_validated"])
        self.assertEqual(len(self.snapshot["annual_validation"]["warnings"]), 2)
        self.assertEqual(
            [row["year"] for row in self.snapshot["annual_dca"]],
            ["2022", "2023", "2024", "2025", "2026 YTD"],
        )

    def test_safety_and_license_state_are_fail_closed(self) -> None:
        snap = self.snapshot
        self.assertEqual(snap["edge"], "UNPROVEN")
        self.assertFalse(snap["live"])
        self.assertEqual(snap["live_orders_submitted"], 0)
        self.assertEqual(snap["testnet_orders_submitted"], 0)
        self.assertEqual(snap["network_calls_during_historical_replay"], 0)
        self.assertFalse(snap["production_files_modified"])
        self.assertEqual(snap["runtime_integration"], "NOT_CONNECTED")
        self.assertIn("NO DEPLOY", snap["license"]["notice"])
        self.assertIn("RIGHTS ARE CLEARED", snap["license"]["notice"])

    def test_eth_is_integrity_audit_only_on_this_dashboard(self) -> None:
        eth = self.snapshot["eth_ingestion_audit_only"]
        self.assertTrue(eth["dataset_hash_matches_status"])
        self.assertTrue(eth["all_official_checksums_match"])
        self.assertTrue(eth["evaluation_report_exists"])
        self.assertFalse(eth["performance_metrics_included"])
        self.assertEqual(eth["data_rows"], 14789)
        self.assertEqual(eth["historical_gap_count"], 1)

    def test_complete_suite_blocker_is_not_hidden(self) -> None:
        tests = self.snapshot["tests"]
        self.assertEqual(tests["rea_engine_suite"]["exit_code"], 0)
        self.assertEqual(tests["rea_engine_suite"]["result"], "49 passed, 1 warning")
        self.assertEqual(tests["focused_resilience"]["result"], "19 passed, 1 warning")
        self.assertEqual(tests["paper_execution_contract_suite"]["result"], "22 passed, 1 warning")
        self.assertEqual(tests["dashboard_snapshot_suite"]["result"], "9 passed")
        self.assertEqual(tests["latest_focused_bundle"]["result"], "80 passed, 1 warning")
        self.assertEqual(tests["suite_excluding_collection_blocker"]["result"], "685 passed, 92 warnings")
        self.assertEqual(tests["full_unfiltered"]["exit_code"], 2)
        self.assertIn("sse_starlette", tests["global_suite_status"])
        self.assertIn("NOT VERIFIED", tests["global_suite_status"])

    def test_frontend_is_static_and_uses_safe_text_rendering(self) -> None:
        panel = PANEL_JS.read_text(encoding="utf-8")
        snapshot_asset = SNAPSHOT_JS.read_text(encoding="utf-8")
        html = INDEX_HTML.read_text(encoding="utf-8")
        css = STYLES_CSS.read_text(encoding="utf-8")

        self.assertIn("textContent", panel)
        self.assertNotIn(".innerHTML", panel)
        self.assertNotIn("fetch(", panel)
        self.assertNotIn("new WebSocket", panel)
        self.assertNotIn("setInterval(", panel)
        self.assertNotIn("setTimeout(", panel)
        self.assertIn("window.SENEX_REA_RESEARCH_SNAPSHOT", snapshot_asset)
        self.assertEqual(html.count('id="rea-research-panel"'), 1)
        self.assertEqual(html.count('id="rea-panel-content"'), 1)
        self.assertEqual(html.count('/static/rea_research_snapshot.js'), 1)
        self.assertEqual(html.count('/static/rea_research_panel.js'), 1)
        self.assertIn("grid-column: 1 / -1", css)
        self.assertIn("@media (max-width: 600px)", css)

    def test_export_contains_no_individual_trade_tape_or_equity_curve(self) -> None:
        snap = self.snapshot
        self.assertNotIn("equity_curve", snap)
        self.assertNotIn("order_tape", snap)
        self.assertNotIn("fills_tape", snap)
        for row in snap["results"]:
            self.assertIn("fills", row)
            self.assertNotIn("order_id", row)
            self.assertNotIn("timestamp", row)


if __name__ == "__main__":
    unittest.main()
