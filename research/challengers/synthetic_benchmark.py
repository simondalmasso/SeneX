[Reading 128 lines from start (total: 128 lines, 0 remaining)]

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import (
    Observation,
    load_manifest,
    manifest_sha256,
    purged_walk_forward_splits,
)
from .recency_challenger_v1 import FEATURE_ORDER
from .selection import (
    evaluate_historical_folds,
    evaluate_market_residual_folds,
    select_candidates,
)


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _synthetic_fixture(
    *,
    n_rows: int = 180,
    seed: int = 7,
) -> tuple[list[Observation], dict[str, dict[str, float]]]:
    """Deterministic bug-finding fixture. It is not market evidence."""
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows: list[Observation] = []
    features: dict[str, dict[str, float]] = {}

    for index in range(n_rows):
        decision = start + timedelta(hours=index)
        label_end = decision + timedelta(hours=24)
        base_signal = math.sin(index / 9.0) * 0.8
        recency_signal = math.cos(index / 13.0) * 0.55
        true_p = _sigmoid(base_signal + recency_signal)
        p_market = _sigmoid(base_signal)
        # Deliberately miscalibrated but informative SENEX-like score.
        senex_raw = _sigmoid(1.55 * (base_signal + recency_signal) + 0.2)
        label = int(rng.random() < true_p)
        market_id = f"synthetic-{index:04d}"
        rows.append(
            Observation(
                market_id=market_id,
                decision_ts=decision.isoformat().replace("+00:00", "Z"),
                label_end_ts=label_end.isoformat().replace("+00:00", "Z"),
                label=label,
                p_market=p_market,
                senex_raw_up=senex_raw,
            )
        )
        payload = {name: 0.0 for name in FEATURE_ORDER}
        payload.update(
            {
                "document_count_6h": 6.0,
                "document_count_1h": 2.0,
                "source_diversity_6h": 3.0,
                "source_concentration_6h": 1.0 / 3.0,
                "sentiment_mean_6h": recency_signal,
                "sentiment_dispersion_6h": 0.2,
                "sentiment_direction_balance_6h": 1.0 if recency_signal > 0 else -1.0,
                "crypto_attention_fraction_6h": 1.0,
                "macro_attention_fraction_6h": 0.0,
                "breaking_event_flag_6h": float(abs(recency_signal) > 0.45),
                "missing_all_sources": 0.0,
            }
        )
        features[market_id] = payload

    return rows, features


def run_synthetic() -> dict:
    rows, features = _synthetic_fixture()
    root = Path(__file__).resolve().parent
    manifest_hashes = {
        "WOLFRAM_RECAL_V1": manifest_sha256(
            load_manifest(root / "manifests" / "WOLFRAM_RECAL_V1.json")
        ),
        "RECENCY_CHALLENGER_V1": manifest_sha256(
            load_manifest(root / "manifests" / "RECENCY_CHALLENGER_V1.json")
        ),
    }
    splits = purged_walk_forward_splits(
        rows,
        n_splits=4,
        min_train_size=80,
        embargo_seconds=24 * 3600,
    )
    reports = evaluate_historical_folds(rows, features, splits)
    residual_diagnostic = evaluate_market_residual_folds(rows, splits)
    selected = select_candidates(reports, max_candidates=2)
    return {
        "contract": "senex-challenger-synthetic-benchmark-v1",
        "synthetic_only": True,
        "seed": 7,
        "n_rows": len(rows),
        "n_splits": len(splits),
        "validation_contract": "PURGED_WALK_FORWARD_ONLY",
        "market_baseline": "p_market",
        "manifest_sha256": manifest_hashes,
        "reports": reports,
        "market_residual_diagnostic": residual_diagnostic,
        "selected_candidates": selected,
        "prospective_t_star": None,
        "prospective_n": None,
        "edge_claim": "NONE",
        "paper_only": True,
        "live": False,
        "real_orders": 0,
        "capital": 0,
    }


def main() -> int:
    print(json.dumps(run_synthetic(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

[executed on device: DESKTOP-DPH3941 (f5db7315-cdea-42b4-b067-243411e4a115)]