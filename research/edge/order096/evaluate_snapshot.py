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

_INTERVAL_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
}


def _base_interval_ms(dataset: dict[str, Any]) -> int:
    interval = str((dataset.get("query") or {}).get("interval") or "").strip()
    value = _INTERVAL_MS.get(interval)
    if value is None:
        raise ValueError(f"unsupported frozen interval: {interval!r}")
    return value


def _directional_metrics(
    candles: list[dict[str, Any]],
    signal: list[int],
    *,
    availability: list[bool] | None = None,
    start_fraction: float = 0.60,
) -> dict[str, Any]:
    start = max(1, int(len(candles) * start_fraction))
    if availability is None:
        availability = [True] * len(candles)
    if len(availability) != len(candles):
        raise ValueError("availability length must match candles")
    n = correct = 0
    signed_sum = 0.0
    common = agreement = disagreement_n = disagreement_correct = 0

    eligible_n = 0
    for i in range(start, len(candles) - 1):
        if not availability[i]:
            continue
        eligible_n += 1
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

    return {
        "eligible_n": eligible_n,
        "n": n,
        "coverage": round(n / eligible_n, 6) if eligible_n else None,
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
    base_interval_ms = _base_interval_ms(dataset)
    matrix = baselines.compute_baseline_matrix(
        candles,
        base_interval_ms=base_interval_ms,
    )
    availability = baselines.compute_baseline_availability_matrix(
        candles,
        base_interval_ms=base_interval_ms,
    )
    return {
        "id": dataset["id"],
        "provider": dataset["provider"],
        "query": dataset["query"],
        "count": len(candles),
        "first_open_time": candles[0]["open_time"] if candles else None,
        "last_open_time": candles[-1]["open_time"] if candles else None,
        "evaluation": {
            name: _directional_metrics(
                candles,
                signal,
                availability=availability[name],
            )
            for name, signal in sorted(matrix.items())
        },
    }


def matched_signal_agreement(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    """Compare initialized indicator states on timestamps present in both venues.

    Warm-up/unavailable states are excluded explicitly. Once initialized, zero
    remains a real neutral/abstain state and participates in state agreement.
    """
    left_all = [dict(row) for row in left["candles"]]
    right_all = [dict(row) for row in right["candles"]]
    left_by_ts = {int(row["open_time"]): row for row in left_all}
    right_by_ts = {int(row["open_time"]): row for row in right_all}
    common = sorted(set(left_by_ts) & set(right_by_ts))

    # Stateful indicators must see identical timestamp prehistory on both venues;
    # otherwise unequal snapshot lengths contaminate the comparison.
    left_rows = [left_by_ts[ts] for ts in common]
    right_rows = [right_by_ts[ts] for ts in common]
    left_base_ms = _base_interval_ms(left)
    right_base_ms = _base_interval_ms(right)
    left_matrix = baselines.compute_baseline_matrix(
        left_rows,
        base_interval_ms=left_base_ms,
    )
    right_matrix = baselines.compute_baseline_matrix(
        right_rows,
        base_interval_ms=right_base_ms,
    )
    left_available = baselines.compute_baseline_availability_matrix(
        left_rows,
        base_interval_ms=left_base_ms,
    )
    right_available = baselines.compute_baseline_availability_matrix(
        right_rows,
        base_interval_ms=right_base_ms,
    )

    li = {int(row["open_time"]): i for i, row in enumerate(left_rows)}
    ri = {int(row["open_time"]): i for i, row in enumerate(right_rows)}

    per_rule: dict[str, Any] = {}
    for name in sorted(set(left_matrix) & set(right_matrix)):
        compared = agreed = nonzero_both = directional_agreed = 0
        for ts in common:
            left_index = li[ts]
            right_index = ri[ts]
            if (
                not left_available[name][left_index]
                or not right_available[name][right_index]
            ):
                continue

            lv = int(left_matrix[name][left_index] or 0)
            rv = int(right_matrix[name][right_index] or 0)
            compared += 1
            agreed += int(lv == rv)
            if lv != 0 and rv != 0:
                nonzero_both += 1
                directional_agreed += int(lv == rv)

        per_rule[name] = {
            "compared_n": compared,
            "availability_excluded_n": len(common) - compared,
            "both_nonzero_n": nonzero_both,
            "signal_agreement": round(agreed / compared, 6) if compared else None,
            "directional_agreement": (
                round(directional_agreed / nonzero_both, 6)
                if nonzero_both else None
            ),
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
