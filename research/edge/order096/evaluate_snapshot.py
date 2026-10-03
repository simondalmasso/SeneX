"""Reproduce ORDER096 exploratory indicator screen from frozen candles.

Research-only. No network I/O, no predictor import, no GPTrader import.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import indicator_baselines as baselines


HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE / "data" / "indicator_screen_v1.json"


def _directional_metrics(
    candles: list[dict[str, Any]],
    signal: list[int],
    *,
    start_fraction: float = 0.60,
) -> dict[str, Any]:
    start = max(1, int(len(candles) * start_fraction))
    n = correct = 0
    signed_sum = 0.0
    common = agreement = disagreement_n = disagreement_correct = 0

    for i in range(start, len(candles) - 1):
        side = int(signal[i] or 0)
        if side == 0:
            continue
        current = float(candles[i]["close"])
        following = float(candles[i + 1]["close"])
        realized = following / current - 1.0
        actual = 1 if realized > 0 else -1 if realized < 0 else 0

        n += 1
        correct += int(actual == side)
        signed_sum += side * realized

        previous = float(candles[i - 1]["close"])
        momentum = 1 if current > previous else -1 if current < previous else 0
        if momentum:
            common += 1
            if momentum == side:
                agreement += 1
            else:
                disagreement_n += 1
                disagreement_correct += int(actual == side)

    eligible = max(1, len(candles) - 1 - start)
    return {
        "n": n,
        "coverage": round(n / eligible, 6),
        "accuracy": round(correct / n, 6) if n else None,
        "mean_signed_bps": round(signed_sum / n * 10_000.0, 6) if n else None,
        "agreement_with_momentum": round(agreement / common, 6) if common else None,
        "disagreement_n": disagreement_n,
        "disagreement_accuracy": (
            round(disagreement_correct / disagreement_n, 6)
            if disagreement_n else None
        ),
    }


def evaluate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    candles = [dict(row) for row in dataset["candles"]]
    matrix = baselines.compute_baseline_matrix(candles)
    return {
        "id": dataset["id"],
        "provider": dataset["provider"],
        "query": dataset["query"],
        "count": len(candles),
        "first_open_time": candles[0]["open_time"] if candles else None,
        "last_open_time": candles[-1]["open_time"] if candles else None,
        "evaluation": {
            name: _directional_metrics(candles, signal)
            for name, signal in sorted(matrix.items())
        },
    }


def matched_signal_agreement(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    """Compare indicator states only on timestamps present in both venues."""
    left_rows = [dict(row) for row in left["candles"]]
    right_rows = [dict(row) for row in right["candles"]]
    left_matrix = baselines.compute_baseline_matrix(left_rows)
    right_matrix = baselines.compute_baseline_matrix(right_rows)

    li = {int(row["open_time"]): i for i, row in enumerate(left_rows)}
    ri = {int(row["open_time"]): i for i, row in enumerate(right_rows)}
    common = sorted(set(li) & set(ri))

    per_rule: dict[str, Any] = {}
    for name in sorted(set(left_matrix) & set(right_matrix)):
        compared = agreed = nonzero_both = 0
        for ts in common:
            lv = int(left_matrix[name][li[ts]] or 0)
            rv = int(right_matrix[name][ri[ts]] or 0)
            # Zero is also the warm-up/unavailable sentinel for these frozen
            # directional baselines. Cross-venue agreement must compare only
            # timestamps where both implementations are initialized and emit
            # an actionable directional state.
            if lv == 0 or rv == 0:
                continue
            compared += 1
            agreed += int(lv == rv)
            nonzero_both += 1
        per_rule[name] = {
            "compared_n": compared,
            "both_nonzero_n": nonzero_both,
            "signal_agreement": round(agreed / compared, 6) if compared else None,
        }

    return {
        "left_id": left["id"],
        "right_id": right["id"],
        "common_timestamp_n": len(common),
        "first_common_open_time": common[0] if common else None,
        "last_common_open_time": common[-1] if common else None,
        "per_rule": per_rule,
    }


def run(snapshot: dict[str, Any]) -> dict[str, Any]:
    datasets = list(snapshot.get("datasets") or [])
    evaluated = [evaluate_dataset(dataset) for dataset in datasets]

    by_key = {
        (str(dataset["provider"]), str(dataset["query"]["interval"])): dataset
        for dataset in datasets
    }
    matched = []
    for interval in ("15m", "1h"):
        left = by_key.get(("TraderSpy", interval))
        right = by_key.get(("Bybit", interval))
        if left is not None and right is not None:
            matched.append(matched_signal_agreement(left, right))

    return {
        "contract": "senex.order096.indicator_screen_result.v1",
        "input_contract": snapshot.get("contract"),
        "start_fraction": 0.60,
        "datasets": evaluated,
        "matched_cross_venue": matched,
        "promotion_evidence": False,
        "edge": "UNPROVEN",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    snapshot = json.loads(args.input.read_text(encoding="utf-8"))
    result = run(snapshot)
    encoded = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
