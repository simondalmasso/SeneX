"""CLI for local-only REA historical paper evaluation."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from .contracts import CostProfile
from .evaluation import (
    DEFAULT_MAX_FILL_QTY, DEFAULT_MAX_INVENTORY, DEFAULT_OOS_END,
    DEFAULT_OOS_START, DEFAULT_STARTING_CASH, EvaluationError,
    canonical_report_sha256, evaluate_strategies, load_ohlcv_csv,
)
from .replay import canonical_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rea-engine-lab",
        description="Run offline, historical-paper evaluation on an explicitly supplied local OHLCV CSV.",
    )
    parser.add_argument("--input", required=True, help="Path to a local OHLCV CSV; no download fallback exists.")
    parser.add_argument("--expected-sha256", required=True, help="Expected SHA-256 of the exact input bytes.")
    parser.add_argument("--output", required=True, help="Path to a new local JSON report.")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing report file.")
    parser.add_argument("--no-benchmarks", action="store_true", help="Omit buy-and-hold benchmark runs.")
    parser.add_argument("--starting-cash", default=str(DEFAULT_STARTING_CASH))
    parser.add_argument("--max-inventory", default=str(DEFAULT_MAX_INVENTORY))
    parser.add_argument("--max-fill-qty", default=str(DEFAULT_MAX_FILL_QTY))
    parser.add_argument("--fee-bps", default="10")
    parser.add_argument("--spread-bps", default="5")
    parser.add_argument("--slippage-bps", default="5")
    parser.add_argument("--oos-start", default=DEFAULT_OOS_START)
    parser.add_argument("--oos-end", default=DEFAULT_OOS_END)
    parser.add_argument("--quote-benchmark", default="100")
    parser.add_argument("--fixed-qty-benchmark", default="0.02")
    parser.add_argument("--cost-stress-factors", nargs="+", default=["1", "2", "3"])
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    source = Path(args.input).expanduser()
    output = Path(args.output).expanduser()
    try:
        if source.resolve() == output.resolve():
            raise EvaluationError("output path must not overwrite the source CSV")
        if output.exists() and not args.overwrite:
            raise EvaluationError(f"output file already exists (pass --overwrite to replace): {output}")
        if not output.parent.exists() or not output.parent.is_dir():
            raise EvaluationError(f"output parent directory must already exist: {output.parent}")
        candles, source_digest = load_ohlcv_csv(source, expected_sha256=args.expected_sha256)
        costs = CostProfile(
            fee_bps=args.fee_bps,
            spread_bps=args.spread_bps,
            slippage_bps=args.slippage_bps,
            profile_id="rea-cli-costs-v1",
        )
        report = evaluate_strategies(
            candles,
            source_digest,
            starting_cash=args.starting_cash,
            max_inventory=args.max_inventory,
            max_fill_qty=args.max_fill_qty,
            costs=costs,
            oos_start=args.oos_start,
            oos_end=args.oos_end,
            include_benchmarks=not args.no_benchmarks,
            cost_stress_factors=tuple(args.cost_stress_factors),
            quote_benchmark=args.quote_benchmark,
            fixed_qty_benchmark=args.fixed_qty_benchmark,
        )
        if report.get("report_sha256") != canonical_report_sha256(report):
            raise EvaluationError("report SHA-256 self-check failed")
        if output.exists() and not args.overwrite:
            raise EvaluationError(f"output file already exists (pass --overwrite to replace): {output}")
        output.write_text(canonical_json(report) + "\n", encoding="utf-8")
    except (EvaluationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(canonical_json({
        "status": "completed",
        "output": str(output.resolve()),
        "source_sha256": source_digest,
        "report_sha256": report["report_sha256"],
        "strategies": list(report["strategies"]),
        "dataset_rows": report["dataset"]["source_rows"],
        "evaluated_rows": report["dataset"]["evaluated_rows"],
        "EDGE": report["safety"]["EDGE"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
