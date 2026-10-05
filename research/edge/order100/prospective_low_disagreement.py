"""ORDER100 prospective confirmation on fresh Polymarket BTC 5m markets.

Frozen before prospective outcomes:
- start boundary;
- low-disagreement band;
- model coefficients;
- evidence floor;
- bootstrap seed/count;
- verdict rule.

No fitting path exists here. Research/PAPER only.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Iterable

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[3]
    repo_root_text = str(repo_root)
    if repo_root_text not in sys.path:
        sys.path.insert(0, repo_root_text)

from research.edge.order097 import market_prior_calibration as order097
from research.edge.order099 import tabular_falsification as order099


PROSPECTIVE_START_TS = 1791090000
LOW_DISAGREEMENT_MAX = 0.17693099999999995
HIGH_DISAGREEMENT_MIN = 0.60501925
MIN_PROSPECTIVE_MARKETS = 300
DEFAULT_BOOTSTRAP = 10_000
DEFAULT_SEED = 7
PRACTICAL_BRIER_GAIN = 0.005

MARKET_ONLY_INTERCEPT = -0.029977798153386935
MARKET_ONLY_COEFFICIENTS = (0.4346033993273502,)

AUGMENTED_INTERCEPT = -0.016707909800067956
AUGMENTED_COEFFICIENTS = (
    0.42444216403460056,
    0.04187479127974901,
)


def frozen_models() -> tuple[order097.LogisticModel, order097.LogisticModel]:
    return (
        order097.LogisticModel(
            intercept=MARKET_ONLY_INTERCEPT,
            coefficients=MARKET_ONLY_COEFFICIENTS,
        ),
        order097.LogisticModel(
            intercept=AUGMENTED_INTERCEPT,
            coefficients=AUGMENTED_COEFFICIENTS,
        ),
    )


def _market_key(item: order097.JoinedObservation) -> tuple[str, str]:
    return (item.pair.market_slug, item.pair.condition_id)


def _earliest_unique_market_rows(
    observations: Iterable[order097.JoinedObservation],
) -> list[order097.JoinedObservation]:
    selected: dict[tuple[str, str], order097.JoinedObservation] = {}
    for item in observations:
        key = _market_key(item)
        existing = selected.get(key)
        if existing is None or item.pair.decision_ts < existing.pair.decision_ts:
            selected[key] = item
    return sorted(
        selected.values(),
        key=lambda item: (
            item.pair.market_start_ts,
            item.pair.market_slug,
            item.pair.decision_ts,
        ),
    )


def prospective_primary_subset(
    observations: Iterable[order097.JoinedObservation],
) -> list[order097.JoinedObservation]:
    """Fresh, low-disagreement sample using each market's earliest causal T0 row."""
    earliest = _earliest_unique_market_rows(
        item
        for item in observations
        if item.pair.market_start_ts >= PROSPECTIVE_START_TS
    )
    return [
        item
        for item in earliest
        if abs(item.pair.senex_raw_up - item.pair.p_market)
        <= LOW_DISAGREEMENT_MAX
    ]


def prospective_high_disagreement_subset(
    observations: Iterable[order097.JoinedObservation],
) -> list[order097.JoinedObservation]:
    """Descriptive-only band using each market's earliest causal T0 row."""
    earliest = _earliest_unique_market_rows(
        item
        for item in observations
        if item.pair.market_start_ts >= PROSPECTIVE_START_TS
    )
    return [
        item
        for item in earliest
        if abs(item.pair.senex_raw_up - item.pair.p_market)
        > HIGH_DISAGREEMENT_MIN
    ]


def prospective_sample_gate(n_markets: int) -> str | None:
    if int(n_markets) < MIN_PROSPECTIVE_MARKETS:
        return (
            f"at least {MIN_PROSPECTIVE_MARKETS} unique resolved fresh "
            "low-disagreement markets are required"
        )
    return None


def _require_frozen_eval_parameters(n_bootstrap: int, seed: int) -> None:
    if int(n_bootstrap) != DEFAULT_BOOTSTRAP or int(seed) != DEFAULT_SEED:
        raise ValueError(
            "ORDER100 frozen evaluator requires "
            f"bootstrap={DEFAULT_BOOTSTRAP} and seed={DEFAULT_SEED}"
        )


def prospective_verdict(
    bootstrap: dict[str, object],
    *,
    n_markets: int,
) -> str:
    if prospective_sample_gate(n_markets) is not None:
        return "COLLECTING_PROSPECTIVE_DATA"
    try:
        brier = bootstrap["brier"]
        log_loss = bootstrap["log_loss"]
        brier_mean = float(brier["mean_delta"])
        brier_high = float(brier["ci95_high"])
        log_loss_high = float(log_loss["ci95_high"])
    except (KeyError, TypeError, ValueError):
        return "PROSPECTIVE_INCREMENTAL_EDGE_NOT_CONFIRMED"
    if not all(math.isfinite(value) for value in (
        brier_mean,
        brier_high,
        log_loss_high,
    )):
        return "PROSPECTIVE_INCREMENTAL_EDGE_NOT_CONFIRMED"
    if (
        brier_mean <= -PRACTICAL_BRIER_GAIN
        and brier_high < 0.0
        and log_loss_high < 0.0
    ):
        return "PROSPECTIVE_EDGE_CONFIRMED"
    return "PROSPECTIVE_INCREMENTAL_EDGE_NOT_CONFIRMED"


def _evaluate_subset(
    observations: list[order097.JoinedObservation],
    *,
    n_bootstrap: int,
    seed: int,
    minimum_markets: int = 2,
) -> dict[str, object]:
    n_markets = len({_market_key(item) for item in observations})
    if n_markets < int(minimum_markets):
        return {
            "n_markets": n_markets,
            "status": (
                "GATE_CLOSED_NO_INTERIM_METRICS"
                if int(minimum_markets) >= MIN_PROSPECTIVE_MARKETS
                else "INSUFFICIENT_FOR_BOOTSTRAP"
            ),
        }
    market_only, augmented = frozen_models()
    loss_rows = order099.nested_loss_rows(
        observations,
        market_only,
        augmented,
    )
    bootstrap = order099.cluster_bootstrap(
        loss_rows,
        n_bootstrap=n_bootstrap,
        random_seed=seed,
    )
    return {
        "n_markets": n_markets,
        "bootstrap": bootstrap,
    }


def run_offline(
    predictions_path: str | Path,
    resolutions_path: str | Path,
    *,
    predictions_manifest_path: str | Path,
    resolutions_manifest_path: str | Path,
    n_bootstrap: int = DEFAULT_BOOTSTRAP,
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    _require_frozen_eval_parameters(n_bootstrap, seed)
    provenance = order099.verify_order098_artifacts(
        predictions_path,
        predictions_manifest_path,
        resolutions_path,
        resolutions_manifest_path,
    )
    predictions = order097.read_jsonl(predictions_path)
    resolutions = order097.read_jsonl(resolutions_path)
    pairs = order097.extract_t0_pairs(predictions)
    joined = order097.join_resolutions(pairs, resolutions)

    primary = prospective_primary_subset(joined)
    high = prospective_high_disagreement_subset(joined)
    primary_eval = _evaluate_subset(
        primary,
        n_bootstrap=n_bootstrap,
        seed=seed,
        minimum_markets=MIN_PROSPECTIVE_MARKETS,
    )

    n_primary = int(primary_eval["n_markets"])
    if prospective_sample_gate(n_primary) is not None:
        high_eval = {
            "n_markets": len(high),
            "status": "GATE_CLOSED_NO_INTERIM_METRICS",
        }
        verdict = "COLLECTING_PROSPECTIVE_DATA"
    else:
        high_eval = _evaluate_subset(
            high,
            n_bootstrap=n_bootstrap,
            seed=seed + 101,
        )
        verdict = prospective_verdict(
            primary_eval["bootstrap"],
            n_markets=n_primary,
        )

    return {
        "order": "ORDER100",
        "analysis_status": (
            "EVALUATED_PROSPECTIVE"
            if prospective_sample_gate(n_primary) is None
            else "COLLECTING_PROSPECTIVE_DATA"
        ),
        "verdict": verdict,
        "prospective_start_ts": PROSPECTIVE_START_TS,
        "low_disagreement_max": LOW_DISAGREEMENT_MAX,
        "high_disagreement_min": HIGH_DISAGREEMENT_MIN,
        "minimum_prospective_markets": MIN_PROSPECTIVE_MARKETS,
        "practical_brier_gain_required": PRACTICAL_BRIER_GAIN,
        "bootstrap_replicates": int(n_bootstrap),
        "seed": int(seed),
        "frozen_models": {
            "market_only": {
                "intercept": MARKET_ONLY_INTERCEPT,
                "coefficients": list(MARKET_ONLY_COEFFICIENTS),
            },
            "market_plus_senex": {
                "intercept": AUGMENTED_INTERCEPT,
                "coefficients": list(AUGMENTED_COEFFICIENTS),
            },
        },
        "artifact_provenance": provenance,
        "primary_low_disagreement": primary_eval,
        "secondary_high_disagreement": {
            **high_eval,
            "interpretation": "DESCRIPTIVE_ONLY",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ORDER100 frozen prospective confirmation"
    )
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--resolutions", required=True)
    parser.add_argument("--predictions-manifest", required=True)
    parser.add_argument("--resolutions-manifest", required=True)
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    result = run_offline(
        args.predictions,
        args.resolutions,
        predictions_manifest_path=args.predictions_manifest,
        resolutions_manifest_path=args.resolutions_manifest,
        n_bootstrap=args.bootstrap,
        seed=args.seed,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
