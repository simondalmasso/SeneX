"""Reproduce the pinned ETHUSDT Spot 4h second-asset paper evaluation offline."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.rea_engines.contracts import CostProfile
from research.rea_engines.evaluation import (
    canonical_report_sha256,
    evaluate_strategies,
    load_ohlcv_csv,
)
from research.rea_engines.replay import canonical_json
from research.rea_engines.strategies import (
    ComboStrategy,
    DCAConfig,
    DCAStrategy,
    GridConfig,
    GridStrategy,
)

DEFAULT_CSV_SHA256 = "d30016ce01c969fbac61b792f0862759a787fa6d2acf74973f3cc4d1c03620a7"
DEFAULT_MANIFEST_SHA256 = "2ba32145fcd46b8cc1cd9332b60e65054277f19dbf55249dd9194aa0da55aec4"
OOS_START = "2022-01-01T00:00:00Z"
OOS_END = "2026-09-30T20:00:00Z"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(2 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a fully local, pinned ETHUSDT 4h paper evaluation.")
    parser.add_argument("--input", default=str(ROOT / "research" / "rea_engines" / "data" / "binance" / "ETHUSDT-4h-2020-01_to_2026-09.csv"))
    parser.add_argument("--manifest", default=str(ROOT / "research" / "rea_engines" / "data" / "binance" / "ETHUSDT-4h-dataset-manifest.json"))
    parser.add_argument("--expected-sha256", default=DEFAULT_CSV_SHA256)
    parser.add_argument("--expected-manifest-sha256", default=DEFAULT_MANIFEST_SHA256)
    parser.add_argument("--output", default=str(ROOT / "research" / "rea_engines" / "runs" / "ethusdt-4h-second-asset-evaluation-20261014.json"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    source = Path(args.input).expanduser()
    manifest_path = Path(args.manifest).expanduser()
    output = Path(args.output).expanduser()
    try:
        if not source.is_file() or not manifest_path.is_file():
            raise ValueError("input CSV and manifest must already exist locally")
        if output.resolve() in {source.resolve(), manifest_path.resolve()}:
            raise ValueError("output cannot overwrite source CSV or manifest")
        if output.exists() and not args.overwrite:
            raise ValueError(f"output already exists (use --overwrite explicitly): {output}")
        actual_manifest_sha = file_sha256(manifest_path)
        expected_manifest_sha = args.expected_manifest_sha256.strip().lower()
        if actual_manifest_sha != expected_manifest_sha:
            raise ValueError(f"manifest SHA-256 mismatch: expected {expected_manifest_sha}, observed {actual_manifest_sha}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if manifest.get("symbol") != "ETHUSDT" or manifest.get("interval") != "4h" or manifest.get("product") != "SPOT":
            raise ValueError("manifest does not describe Binance Spot ETHUSDT 4h")
        if manifest.get("canonicalCsv") != source.name:
            raise ValueError("input filename does not match manifest canonicalCsv")

        candles, source_sha = load_ohlcv_csv(source, expected_sha256=args.expected_sha256)
        cutoff = "2022-01-01T00:00:00.000000Z"
        training = tuple(candle for candle in candles if candle.timestamp < cutoff)
        oos = tuple(candle for candle in candles if candle.timestamp >= cutoff and candle.timestamp <= "2026-09-30T20:00:00.000000Z")
        if not training or not oos:
            raise ValueError("training or fixed out-of-sample window is empty")
        grid_lower = min(candle.low for candle in training)
        grid_upper = max(candle.high for candle in training)
        if grid_lower <= 0 or grid_upper <= grid_lower:
            raise ValueError("training-derived grid range is invalid")

        grid = GridConfig(
            lower_price=grid_lower, upper_price=grid_upper, levels=10,
            quote_per_order="100", take_profit_pct="0.01",
            config_id="eth-training-range-grid-v1",
        )
        dca = DCAConfig(
            initial_quote="100", dca_quote="50", adverse_step_pct="0.05",
            take_profit_pct="0.03", max_adds=3,
            config_id="eth-frozen-quote-dca-v1",
        )
        factories = {
            "grid": lambda: GridStrategy(grid),
            "dca": lambda: DCAStrategy(dca),
            "combo": lambda: ComboStrategy(grid_config=grid, dca_config=dca),
        }
        costs = CostProfile(fee_bps="10", spread_bps="5", slippage_bps="5", profile_id="rea-cli-costs-v1")
        report = evaluate_strategies(
            candles, source_sha, starting_cash="10000",
            max_inventory="0.2", max_fill_qty="0.2", costs=costs,
            oos_start=OOS_START, oos_end=OOS_END,
            strategy_factories=factories, include_benchmarks=True,
            cost_stress_factors=("1", "2", "3"),
            quote_benchmark="100", fixed_qty_benchmark="0.02",
        )
        report["experiment"] = {
            "protocol_id": "ethusdt-4h-second-asset-fixed-oos-v1",
            "asset_market": "Binance Spot ETHUSDT",
            "interval": "4h",
            "training_window": {
                "start": training[0].timestamp, "end": training[-1].timestamp, "rows": len(training),
            },
            "oos_window": {"start": oos[0].timestamp, "end": oos[-1].timestamp, "rows": len(oos)},
            "grid_range_derivation": "lower_price=min(training low); upper_price=max(training high); training only, all parameters frozen before OOS",
            "grid_config": {
                "lower_price": str(grid_lower), "upper_price": str(grid_upper), "levels": 10,
                "quote_per_order": "100", "take_profit_pct": "0.01",
            },
            "dca_combo_config": {
                "initial_quote": "100", "dca_quote": "50", "adverse_step_pct": "0.05",
                "take_profit_pct": "0.03", "max_adds": 3,
            },
            "risk_caps": {
                "starting_cash_usdt": "10000", "max_inventory_eth": "0.2",
                "max_fill_qty_eth": "0.2", "all_long_only_cash_backed": True,
            },
            "source_csv": str(source.resolve()),
            "source_csv_sha256": source_sha,
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": actual_manifest_sha,
            "official_archive_checksums_verified": all(item.get("checksum_verified") is True for item in manifest["archives"]),
            "monthly_archives": len(manifest["archives"]),
            "dataset_license": "CC BY-NC-SA 4.0 / Binance Vision dataset terms; for non-commercial personal non-production research only. Do not integrate into a commercial product or use for live/proprietary execution without separate written license.",
            "parameters_tuned_on_oos": False,
            "caveat": "Second asset robustness check only; strategy returns are not directly risk-comparable with BTC due different base-asset caps and fixed-price grid bounds derived from ETH training period.",
        }
        report["report_sha256"] = canonical_report_sha256(report)
        if report["report_sha256"] != canonical_report_sha256(report):
            raise ValueError("canonical report hash self-check failed")
        if not output.parent.is_dir():
            raise ValueError(f"output parent directory must exist: {output.parent}")
        if output.exists() and not args.overwrite:
            raise ValueError(f"output already exists (use --overwrite explicitly): {output}")
        output.write_text(canonical_json(report) + "\n", encoding="utf-8")
        reread = json.loads(output.read_text(encoding="utf-8"))
        if reread.get("report_sha256") != canonical_report_sha256(reread):
            raise ValueError("written report failed canonical SHA-256 self-check")
        print(json.dumps({
            "status": "completed",
            "output": str(output.resolve()),
            "output_bytes": output.stat().st_size,
            "output_sha256": file_sha256(output),
            "report_payload_sha256": reread["report_sha256"],
            "source_sha256": source_sha,
            "manifest_sha256": actual_manifest_sha,
            "training_rows": len(training),
            "oos_rows": len(oos),
            "EDGE": reread["safety"]["EDGE"],
        }, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
