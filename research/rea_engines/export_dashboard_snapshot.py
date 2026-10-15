"""Create the local-only, summary-only REA research dashboard data asset."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT = Path("research/rea_engines/runs/audit-reproduction-final-20261014.json")
STATUS = Path("research/rea_engines/runs/FINAL_STATUS_2026-10-14.json")
COSTS = Path("research/rea_engines/runs/cost-stress-summary-20261014.csv")
ANNUAL = Path("research/rea_engines/runs/annual-metrics-20261014.csv")
OUTPUT = Path("senecio_polymarket/frontend/rea_research_snapshot.js")
BTC_NAME = "BTCUSDT-4h-2020-01_to_2026-09.csv"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"Expected an object in {path}")
    return obj


def num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def integer(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def audit_source_inventory(root: Path) -> tuple[int, int, int]:
    """Return current row count, missing paths, and size/hash mismatches."""
    inventory_path = root / "research" / "rea_engines" / "SOURCE_FILE_INVENTORY.csv"
    rows = read_csv(inventory_path)
    missing = 0
    mismatches = 0
    for row in rows:
        raw_path = row.get("path", "").strip()
        rel_path = row.get("relative_path", "").strip()
        path = Path(raw_path) if raw_path else root / rel_path
        if not path.is_file():
            missing += 1
            continue
        try:
            expected_bytes = int(row.get("bytes", ""))
        except (TypeError, ValueError):
            mismatches += 1
            continue
        expected_hash = str(row.get("sha256", "")).lower()
        if path.stat().st_size != expected_bytes or sha256(path).lower() != expected_hash:
            mismatches += 1
    return len(rows), missing, mismatches


def metrics_row(key: str, label: str, group: str, m: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": key, "label": label, "group": group,
        "return_pct": num(m.get("return_pct")),
        "net_pnl_usdt": num(m.get("net_pnl")),
        "ending_equity_usdt": num(m.get("ending_equity")),
        "max_drawdown_pct": num(m.get("max_drawdown_pct")),
        "fills": integer(m.get("fills")),
        "time_in_market_pct": num(m.get("time_in_market_pct")),
        "fees_usdt": num(m.get("total_fees")),
        "spread_cost_usdt": num(m.get("total_spread_cost")),
        "slippage_cost_usdt": num(m.get("total_slippage_cost")),
        "turnover_usdt": num(m.get("turnover")),
    }


def build_snapshot(root: Path) -> dict[str, Any]:
    report_path, status_path = root / REPORT, root / STATUS
    report, status = load_json(report_path), load_json(status_path)
    report_hash, status_hash = sha256(report_path), sha256(status_path)
    cost_hash, annual_hash = sha256(root / COSTS), sha256(root / ANNUAL)
    report_expected = str(status.get("btc_evaluation", {}).get("report_sha256", "")).lower()
    report_ok = bool(report_expected) and report_hash == report_expected
    market = report.get("dataset", {})
    expected_btc_hash = str(market.get("source_sha256", "")).lower()
    candidates = [
        root.parent / "trading-bot-rea-20261013" / "INDEPENDENT-PAPER-BOT" / "data" / "binance" / BTC_NAME,
        root / "research" / "rea_engines" / "data" / "binance" / BTC_NAME,
    ]
    btc_file = next((p for p in candidates if p.is_file()), None)
    actual_btc_hash = sha256(btc_file) if btc_file else None
    btc_ok = bool(actual_btc_hash and expected_btc_hash and actual_btc_hash == expected_btc_hash)

    labels = {"grid": "Grid clean-room", "dca": "DCA clean-room", "combo": "Combo clean-room"}
    results = [
        metrics_row(key, labels[key], "strategy", report["strategies"][key]["base"]["metrics"])
        for key in ("grid", "dca", "combo")
    ]
    benchmark_labels = {
        "buy_and_hold_quote_100": "Buy & hold · 100 USDT allocation",
        "buy_and_hold_fixed_qty": "Buy & hold · fixed 0.02 BTC",
    }
    results.extend(
        metrics_row(key, benchmark_labels[key], "benchmark", report["benchmarks"][key]["base"]["metrics"])
        for key in ("buy_and_hold_quote_100", "buy_and_hold_fixed_qty")
    )

    cost_rows = []
    for row in read_csv(root / COSTS):
        cost_rows.append({
            "group": row.get("group", "UNKNOWN"), "model": row.get("model", "UNKNOWN"),
            "stress_factor": row.get("stress_factor", "UNKNOWN"),
            "return_pct": num(row.get("return_pct")), "max_drawdown_pct": num(row.get("max_drawdown_pct")),
            "ending_equity_usdt": num(row.get("ending_equity")), "fills": integer(row.get("fills")),
            "turnover_usdt": num(row.get("turnover_usdt")), "time_in_market_pct": num(row.get("time_in_market_pct")),
            "fees_usdt": num(row.get("fees_usdt")), "spread_cost_usdt": num(row.get("spread_cost_usdt")),
            "slippage_cost_usdt": num(row.get("slippage_cost_usdt")),
            "market_regime": row.get("market_regime", "UNKNOWN"),
        })

    annual = read_csv(root / ANNUAL)
    dca_annual = [{
        "year": "2026 YTD" if row.get("year") == "2026" else row.get("year", "UNKNOWN"),
        "return_pct": num(row.get("return_pct")), "max_drawdown_pct": num(row.get("max_drawdown_pct")),
        "fills": integer(row.get("fills")), "time_in_market_pct": num(row.get("time_in_market_pct")),
        "slice_semantics": row.get("slice_semantics", "UNKNOWN"),
    } for row in annual if row.get("group") == "strategies" and row.get("model") == "dca"]

    annual_warnings = []
    for key in ("grid", "combo"):
        base = report["strategies"][key]["base"]["metrics"]
        yearly = [row for row in annual if row.get("group") == "strategies" and row.get("model") == key]
        first = next((row for row in yearly if row.get("year") == "2022"), None)
        later_activity = any(row.get("year") != "2022" and integer(row.get("fills")) not in (None, 0) for row in yearly)
        if first and later_activity and (
            num(first.get("return_pct")) == num(base.get("return_pct"))
            and num(first.get("max_drawdown_pct")) == num(base.get("max_drawdown_pct"))
            and integer(first.get("fills")) == integer(base.get("fills"))
        ):
            annual_warnings.append(
                f"{key.upper()}: 2022 slice matches aggregate base metrics while later slices show activity; withheld pending validation."
            )

    tests = status.get("tests", {})
    inventory = status.get("inventory", {})
    zip_info = status.get("gunbot_zip", {})
    resilience = status.get("resilience_module", {})
    eth = status.get("eth_evaluation", {})
    eth_file = root / "research" / "rea_engines" / "data" / "binance" / "ETHUSDT-4h-2020-01_to_2026-09.csv"
    eth_actual_hash = sha256(eth_file) if eth_file.is_file() else None
    eth_ok = bool(eth_actual_hash and eth_actual_hash == str(eth.get("dataset_sha256", "")).lower())
    inventory_row_count, inventory_missing, inventory_mismatches = audit_source_inventory(root)
    inventory_ok = (
        integer(inventory.get("rows")) == inventory_row_count
        and inventory.get("all_paths_present") is True
        and integer(inventory.get("size_and_sha256_mismatches")) == 0
        and inventory_missing == 0
        and inventory_mismatches == 0
    )
    latest = status.get("latest_continuation", {})
    latest_tests = latest.get("tests", {})
    effective_rea_suite = latest_tests.get("rea_engine_suite", tests.get("rea_engine_suite", {}))
    effective_paper_suite = latest_tests.get("paper_execution_contract_suite", {})
    effective_dashboard_suite = latest_tests.get("dashboard_snapshot_suite", {})
    effective_focused_bundle = latest_tests.get("latest_focused_bundle", {})
    effective_repo_suite = latest_tests.get("suite_excluding_collection_blocker", tests.get("suite_excluding_collection_blocker", {}))
    effective_unfiltered = latest_tests.get("full_unfiltered", tests.get("full_unfiltered", {}))
    zip_ok = integer(zip_info.get("manifest_rows")) == 48 and integer(zip_info.get("manifest_mismatches")) == 0
    integrity_ok = report_ok and btc_ok and inventory_ok and zip_ok and eth_ok
    status_timestamp = status.get("generated_at_local")
    generated_at_utc = None
    if status_timestamp:
        try:
            parsed_timestamp = datetime.fromisoformat(str(status_timestamp))
            if parsed_timestamp.tzinfo is not None:
                generated_at_utc = parsed_timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            generated_at_utc = None
    if not integrity_ok:
        checks = {
            "report_matches_status": report_ok, "btc_dataset_hash_matches": btc_ok,
            "source_inventory_ok": inventory_ok, "source_inventory_missing": inventory_missing == 0,
            "source_inventory_size_or_hash_mismatches": inventory_mismatches == 0,
            "gunbot_manifest_ok": zip_ok, "eth_dataset_hash_matches_status": eth_ok,
        }
        raise ValueError("Snapshot integrity check failed: " + ", ".join(k for k, v in checks.items() if not v))

    return {
        "schema_version": 1,
        "snapshot_kind": "local-static-summary",
        "generated_at_utc": generated_at_utc,
        "source_status_timestamp": status_timestamp,
        "source_report_path": REPORT.as_posix(),
        "report_sha256": report_hash,
        "expected_report_sha256": report_expected,
        "source_dataset_sha256": expected_btc_hash,
        "actual_dataset_sha256": actual_btc_hash,
        "report_bytes": report_path.stat().st_size,
        "supporting_source_hashes": {
            "final_status_sha256": status_hash, "cost_stress_csv_sha256": cost_hash,
            "annual_metrics_csv_sha256": annual_hash,
        },
        "status": "historical-paper", "edge": "UNPROVEN", "live": False,
        "live_orders_submitted": 0, "testnet_orders_submitted": 0,
        "network_calls_during_historical_replay": integer(report.get("safety", {}).get("network_calls")),
        "zero_spend": report.get("safety", {}).get("zero_spend") is True,
        "runtime_integration": status.get("runtime_integration", "UNKNOWN"),
        "production_files_modified": bool(status.get("production_files_modified", True)),
        "integrity": {
            "overall_ok": integrity_ok, "report_matches_final_status": report_ok,
            "dataset_file_found": btc_file is not None, "dataset_hash_matches_report": btc_ok,
            "actual_dataset_sha256": actual_btc_hash, "source_dataset_sha256": expected_btc_hash,
            "inventory_rows": integer(inventory.get("rows")), "inventory_ok": inventory_ok,
            "zip_manifest_rows": integer(zip_info.get("manifest_rows")),
            "zip_manifest_mismatches": integer(zip_info.get("manifest_mismatches")), "zip_manifest_ok": zip_ok,
            "eth_dataset_hash_matches_status": eth_ok,
        },
        "market": {
            "venue": "Binance Spot historical OHLCV", "symbol": "BTCUSDT", "timeframe": "4h",
            "source_rows": integer(market.get("source_rows")), "evaluated_rows": integer(market.get("evaluated_rows")),
            "source_timestamp_start": market.get("source_timestamp_start"),
            "source_timestamp_end": market.get("source_timestamp_end"),
            "oos_timestamp_start": market.get("oos_timestamp_start"), "oos_timestamp_end": market.get("oos_timestamp_end"),
            "interval_gaps": market.get("interval_gaps", []), "evaluated_interval_gaps": market.get("evaluated_interval_gaps", []),
            "fill_policy_id": report.get("fill_policy_id", "UNKNOWN"),
            "starting_cash_usdt": num(report.get("replay_config", {}).get("starting_cash")),
            "fees_bps": num(report.get("replay_config", {}).get("costs", {}).get("fee_bps")),
            "spread_bps": num(report.get("replay_config", {}).get("costs", {}).get("spread_bps")),
            "slippage_bps": num(report.get("replay_config", {}).get("costs", {}).get("slippage_bps")),
            "max_inventory_btc": num(report.get("replay_config", {}).get("max_inventory")),
            "max_fill_qty_btc": num(report.get("replay_config", {}).get("max_fill_qty")),
            "limitations": report.get("limitations", []),
        },
        "results": results, "cost_stress": cost_rows, "annual_dca": dca_annual,
        "annual_validation": {
            "grid_combo_slices_validated": False if annual_warnings else None,
            "warnings": annual_warnings,
            "display_policy": "DCA annual slices only; grid/combo annual comparisons withheld pending source validation.",
        },
        "tests": {
            "pytest_version": tests.get("pytest_version", "UNKNOWN"),
            "rea_engine_suite": effective_rea_suite,
            "focused_resilience": latest_tests.get("focused_resilience", tests.get("focused_resilience", {})),
            "paper_execution_contract_suite": effective_paper_suite,
            "dashboard_snapshot_suite": effective_dashboard_suite,
            "latest_focused_bundle": effective_focused_bundle,
            "suite_excluding_collection_blocker": effective_repo_suite,
            "full_unfiltered": effective_unfiltered,
            "compileall": tests.get("compileall", {}),
            "resilience_features": resilience.get("features", []),
            "resilience_test_passed": integer(latest_tests.get("resilience_test_passed", resilience.get("dedicated_tests_passed"))),
            "rea_test_passed": integer(latest_tests.get("rea_test_passed", resilience.get("integrated_rea_tests_passed"))),
            "rea_test_total": integer(latest_tests.get("rea_test_total", effective_rea_suite.get("test_count"))),
            "latest_verified_at_local": latest.get("verified_at_local"),
            "global_suite_status": latest_tests.get("global_suite_status", "NOT VERIFIED — collection blocked by missing sse_starlette; no dependencies installed"),
        },
        "inventory": {
            "rows": inventory_row_count, "all_paths_present": inventory_missing == 0,
            "size_and_sha256_mismatches": inventory_mismatches,
            "checked_at_local": inventory.get("last_checked_local"),
        },
        "gunbot_static_audit": {
            "zip_sha256": zip_info.get("sha256"), "zip_bytes": integer(zip_info.get("bytes")),
            "zip_entries": integer(zip_info.get("archive_entries")),
            "manifest_rows": integer(zip_info.get("manifest_rows")),
            "manifest_mismatches": integer(zip_info.get("manifest_mismatches")),
            "semantic_equivalence_verified": bool(zip_info.get("semantic_equivalence_verified", False)),
            "commercial_executable_analysis": status.get("commercial_executable_analysis", "UNKNOWN"),
        },
        "eth_ingestion_audit_only": {
            "symbol": "ETHUSDT", "timeframe": "4h",
            "dataset_sha256": eth.get("dataset_sha256"), "actual_dataset_sha256": eth_actual_hash,
            "dataset_hash_matches_status": eth_ok, "archive_count": integer(eth.get("archive_count")),
            "all_official_checksums_match": eth.get("all_official_checksums_match"),
            "data_rows": integer(eth.get("data_rows")), "oos_rows": integer(eth.get("oos_rows")),
            "historical_gap_count": integer(eth.get("historical_gap_count")),
            "evaluation_report_exists": (root / "research" / "rea_engines" / "runs" / "ethusdt-4h-second-asset-evaluation-20261014.json").is_file(),
            "performance_metrics_included": False,
            "reason": "ETH performance is not displayed in this BTC-focused panel under the current dataset-license boundary; evaluation artifacts remain local.",
        },
        "license": {
            "status": "RESEARCH ONLY — rights clearance required before commercial or production use",
            "notice": "NO DEPLOY / NO COMMERCIAL INTEGRATION UNTIL RIGHTS ARE CLEARED",
            "source_url": "https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md",
        },
        "safety_notice": "Historical PAPER evidence only. Clean-room models are not the original commercial bots and do not prove future profitability.",
    }


def export_snapshot(root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parents[2]
    snapshot = build_snapshot(base)
    output = base / OUTPUT
    encoded = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True)
    output.write_text(
        "/* GENERATED FILE — run research/rea_engines/export_dashboard_snapshot.py; do not edit by hand. */\n"
        "window.SENEX_REA_RESEARCH_SNAPSHOT = " + encoded + ";\n",
        encoding="utf-8", newline="\n",
    )
    print("snapshot:", output)
    print("report SHA-256:", snapshot["report_sha256"])
    print("dataset matches report:", snapshot["integrity"]["dataset_hash_matches_report"])
    print("inventory:", snapshot["integrity"]["inventory_rows"], "rows; ZIP manifest:", snapshot["integrity"]["zip_manifest_rows"], "rows")
    print("ETH dataset hash matches:", snapshot["integrity"]["eth_dataset_hash_matches_status"])
    print("aggregate results:", len(snapshot["results"]), "| cost-stress rows:", len(snapshot["cost_stress"]))
    print("annual Grid/Combo warnings:", len(snapshot["annual_validation"]["warnings"]))
    print("EDGE:", snapshot["edge"], "| LIVE:", snapshot["live"], "| runtime:", snapshot["runtime_integration"])
    return output


if __name__ == "__main__":
    export_snapshot()
