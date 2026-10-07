"""ORDER099 preregistered tabular falsification for ORDER097.

This module deliberately stays simple. It reuses ORDER097's target-alignment,
causal split, and nested-model implementation, then adds:

- one descriptive row per unique Polymarket 5m market;
- TRAIN-only bucket cut points;
- fixed UTC session buckets;
- market-cluster bootstrap uncertainty for nested holdout loss deltas;
- a conservative deterministic edge verdict.

No HMM, regime fitting, predictor mutation, runtime, execution, or network
surface exists here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[3]
    repo_root_text = str(repo_root)
    if repo_root_text not in sys.path:
        sys.path.insert(0, repo_root_text)

import numpy as np
from scipy.stats import binomtest

from research.edge.order097 import market_prior_calibration as order097
from research.edge.order098 import polymarket_5m_resolutions as order098_resolutions
from senecio_polymarket.backend.research.statistical_validation import (
    multiple_hypothesis_correction,
)


PRACTICAL_BRIER_GAIN = 0.005
DEFAULT_BOOTSTRAP = 10_000
DEFAULT_SEED = 7
MIN_INFERENCE_CELL = 30
MIN_TOTAL_UNIQUE_MARKETS = 200


def minimum_market_gate(total_unique_markets: int) -> str | None:
    """Return a blocker reason until the preregistered market floor is met."""
    if int(total_unique_markets) < MIN_TOTAL_UNIQUE_MARKETS:
        return (
            f"at least {MIN_TOTAL_UNIQUE_MARKETS} unique resolved markets "
            "are required for an ORDER099 edge verdict"
        )
    return None


class ArtifactContractError(ValueError):
    """ORDER098 artifacts/manifests violate the frozen input contract."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_manifest(path: str | Path) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactContractError(f"invalid manifest: {path}") from exc
    if not isinstance(value, dict):
        raise ArtifactContractError(f"manifest is not an object: {path}")
    return value


def _read_jsonl_objects(path: str | Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArtifactContractError(
                f"invalid JSONL at {path}:{line_number}"
            ) from exc
        if not isinstance(row, dict):
            raise ArtifactContractError(
                f"non-object JSONL row at {path}:{line_number}"
            )
        rows.append(row)
    return rows


def verify_order098_artifacts(
    predictions_path: str | Path,
    predictions_manifest_path: str | Path,
    resolutions_path: str | Path,
    resolutions_manifest_path: str | Path,
) -> dict[str, object]:
    """Verify exact ORDER098 bytes, contracts, coverage, and provenance."""
    predictions_sha = _sha256_file(predictions_path)
    resolutions_sha = _sha256_file(resolutions_path)
    p_manifest = _read_manifest(predictions_manifest_path)
    r_manifest = _read_manifest(resolutions_manifest_path)

    prediction_contract = str(p_manifest.get("contract") or "")
    if prediction_contract not in {
        "senex-order098-t0-audit-export-v1",
        "senex-order098-t0-audit-export-v2",
    }:
        raise ArtifactContractError("unexpected ORDER098 T0 export contract")
    if (
        r_manifest.get("contract")
        != "senex-order098-polymarket-5m-resolution-corpus-v1"
    ):
        raise ArtifactContractError("unexpected ORDER098 resolution contract")

    if p_manifest.get("output_file_sha256") != predictions_sha:
        raise ArtifactContractError("prediction JSONL SHA256 mismatch")
    if r_manifest.get("output_file_sha256") != resolutions_sha:
        raise ArtifactContractError("resolution JSONL SHA256 mismatch")
    if r_manifest.get("predictions_file_sha256") != predictions_sha:
        raise ArtifactContractError(
            "resolution manifest is not bound to this prediction JSONL SHA256"
        )

    prediction_rows = _read_jsonl_objects(predictions_path)
    row_hash_field = (
        "causal_t0_sha256"
        if prediction_contract == "senex-order098-t0-audit-export-v2"
        else "source_audit_sha256"
    )
    source_hashes = [row.get(row_hash_field) for row in prediction_rows]
    if any(not isinstance(value, str) or len(value) != 64 for value in source_hashes):
        raise ArtifactContractError("prediction row causal/source hashes are invalid")
    if (
        p_manifest.get("output_row_hashes_sha256")
        != _sha256_text(_canonical_json(source_hashes))
    ):
        raise ArtifactContractError(
            "prediction row source-audit hash manifest mismatch"
        )

    resolution_rows = _read_jsonl_objects(resolutions_path)
    if (
        r_manifest.get("resolution_records_sha256")
        != _sha256_text(_canonical_json(resolution_rows))
    ):
        raise ArtifactContractError(
            "resolution record hash manifest mismatch"
        )

    try:
        requested = int(r_manifest.get("requested_markets"))
        accepted = int(r_manifest.get("accepted_markets"))
        rejected = int(r_manifest.get("rejected_markets"))
    except (TypeError, ValueError) as exc:
        raise ArtifactContractError(
            "resolution coverage counters are invalid"
        ) from exc

    if requested <= 0 or accepted <= 0:
        raise ArtifactContractError("resolution corpus is empty")
    if rejected != 0 or accepted != requested:
        raise ArtifactContractError(
            "partial resolution corpus is not admissible for ORDER099"
        )

    try:
        projected = int(p_manifest.get("projected_rows"))
    except (TypeError, ValueError) as exc:
        raise ArtifactContractError("projected_rows is invalid") from exc

    if projected != len(prediction_rows) or projected <= 0:
        raise ArtifactContractError(
            "prediction manifest projected_rows does not match JSONL"
        )
    if accepted != len(resolution_rows):
        raise ArtifactContractError(
            "resolution manifest accepted_markets does not match JSONL"
        )

    result = {
        "predictions_sha256": predictions_sha,
        "resolutions_sha256": resolutions_sha,
        "prediction_contract": prediction_contract,
        "resolution_contract": r_manifest["contract"],
        "prediction_rows": len(prediction_rows),
        "requested_markets": requested,
        "accepted_markets": accepted,
        "rejected_markets": rejected,
    }

    if prediction_contract == "senex-order098-t0-audit-export-v2":
        if p_manifest.get("causal_hash_contract") != "CAUSAL_T0_ALLOWLIST_V2":
            raise ArtifactContractError("unexpected causal hash contract")

        snapshot_start_ts = p_manifest.get("snapshot_start_ts")
        snapshot_end_ts = p_manifest.get("snapshot_end_ts")
        if not isinstance(snapshot_start_ts, str) or not isinstance(snapshot_end_ts, str):
            raise ArtifactContractError("prospective snapshot window is missing")
        try:
            start_dt = datetime.fromisoformat(snapshot_start_ts.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(snapshot_end_ts.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ArtifactContractError("prospective snapshot window is invalid") from exc
        if start_dt.tzinfo is None or end_dt.tzinfo is None or end_dt < start_dt:
            raise ArtifactContractError("prospective snapshot window is invalid")

        lineage = p_manifest.get("source_to_d1_lineage")
        if not isinstance(lineage, dict):
            raise ArtifactContractError("source-to-D1 lineage evidence is missing")
        if lineage.get("contract") != "senex-source-to-d1-lineage-v1":
            raise ArtifactContractError("unexpected source-to-D1 lineage contract")
        if lineage.get("window_start_ts") != snapshot_start_ts:
            raise ArtifactContractError("source-to-D1 lineage start does not match snapshot")
        if lineage.get("window_end_ts") != snapshot_end_ts:
            raise ArtifactContractError("source-to-D1 lineage end does not match snapshot")
        try:
            expected_generated = int(lineage.get("expected_generated_t0"))
            persisted_t0 = int(lineage.get("persisted_t0"))
            unresolved_t0 = int(lineage.get("unresolved_t0"))
            fetched_d1_rows = int(lineage.get("fetched_d1_rows"))
        except (TypeError, ValueError) as exc:
            raise ArtifactContractError("source-to-D1 lineage counters are invalid") from exc
        receipt_sha = lineage.get("receipt_file_sha256")
        if not isinstance(receipt_sha, str) or len(receipt_sha) != 64:
            raise ArtifactContractError("source-to-D1 receipt SHA256 is invalid")
        if (
            expected_generated <= 0
            or persisted_t0 != expected_generated
            or unresolved_t0 != 0
            or fetched_d1_rows != expected_generated
        ):
            raise ArtifactContractError("source-to-D1 lineage is incomplete")

        allowed_v2_keys = {
            "id",
            "ts",
            "symbol",
            "audit",
            "source_audit_sha256",
            "causal_t0_sha256",
        }
        for row in prediction_rows:
            if set(row) != allowed_v2_keys:
                raise ArtifactContractError(
                    "prospective v2 T0 row contains unexpected top-level fields"
                )
            causal_payload = {
                "id": row["id"],
                "ts": row["ts"],
                "symbol": row["symbol"],
                "audit": row["audit"],
            }
            expected_causal_hash = _sha256_text(
                _canonical_json(causal_payload)
            )
            if row.get("causal_t0_sha256") != expected_causal_hash:
                raise ArtifactContractError(
                    "causal T0 hash does not match visible decision-time bytes"
                )

        try:
            max_prediction_id = int(p_manifest.get("snapshot_max_prediction_id"))
        except (TypeError, ValueError) as exc:
            raise ArtifactContractError("snapshot_max_prediction_id is invalid") from exc
        if max_prediction_id < 0 or any(
            int(row.get("id")) > max_prediction_id for row in prediction_rows
        ):
            raise ArtifactContractError("prediction row exceeds frozen snapshot boundary")

        broad_identities = order098_resolutions.extract_market_identities(prediction_rows)
        broad_keys = {
            (str(item["slug"]), str(item["condition_id"]))
            for item in broad_identities
        }
        admissible_pairs = order097.extract_t0_pairs(prediction_rows)
        admissible_keys = {
            (pair.market_slug, pair.condition_id)
            for pair in admissible_pairs
        }
        resolution_keys = {
            (str(row.get("slug") or ""), str(row.get("condition_id") or ""))
            for row in resolution_rows
        }

        missing_admissible = sorted(admissible_keys - resolution_keys)
        if missing_admissible:
            raise ArtifactContractError(
                f"resolution corpus missing admissible scientific identities: {missing_admissible[:5]}"
            )
        missing_broad = sorted(broad_keys - resolution_keys)
        extra_resolutions = sorted(resolution_keys - broad_keys)
        if missing_broad or extra_resolutions:
            raise ArtifactContractError(
                "resolution corpus does not exactly cover broad exported identities"
            )

        if requested != len(broad_keys):
            raise ArtifactContractError(
                "resolution requested_markets does not match broad exported identities"
            )

        if r_manifest.get("source") != order098_resolutions.SOURCE:
            raise ArtifactContractError("resolution source provenance mismatch")
        if r_manifest.get("gamma_base") != order098_resolutions.GAMMA_BASE:
            raise ArtifactContractError("resolution gamma_base provenance mismatch")
        expected_collector_sha = _sha256_file(Path(order098_resolutions.__file__).resolve())
        if r_manifest.get("collector_file_sha256") != expected_collector_sha:
            raise ArtifactContractError("resolution collector provenance mismatch")

        expected_queries = [
            {
                "slug": str(item["slug"]),
                "condition_id": item.get("condition_id"),
            }
            for item in broad_identities
        ]
        if r_manifest.get("queries") != expected_queries:
            raise ArtifactContractError("resolution queries provenance mismatch")

        result.update({
            "broad_collector_markets": len(broad_keys),
            "admissible_scientific_markets": len(admissible_keys),
            "extra_resolution_markets": len(extra_resolutions),
        })

    return result


def _market_key(item: order097.JoinedObservation) -> tuple[str, str]:
    return (item.pair.market_slug, item.pair.condition_id)


def unique_market_observations(
    observations: Iterable[order097.JoinedObservation],
) -> list[order097.JoinedObservation]:
    """Choose the earliest causal T0 row for each exact market identity."""
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


def _quartiles(values: list[float]) -> list[float]:
    if not values:
        raise ValueError("cannot derive cut points from empty TRAIN data")
    arr = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(arr)):
        raise ValueError("TRAIN values must be finite")
    return [
        float(value)
        for value in np.quantile(arr, [0.25, 0.50, 0.75], method="linear")
    ]


def derive_train_cutpoints(
    observations: Iterable[order097.JoinedObservation],
) -> dict[str, object]:
    """Freeze all data-dependent descriptive cut points from TRAIN only."""
    rows = unique_market_observations(observations)
    if not rows:
        raise ValueError("TRAIN contains no unique markets")

    p_market = [float(item.pair.p_market) for item in rows]
    senex = [float(item.pair.senex_raw_up) for item in rows]
    absolute = [
        abs(float(item.pair.senex_raw_up) - float(item.pair.p_market))
        for item in rows
    ]
    near_zero = float(
        np.quantile(np.asarray(absolute, dtype=float), 1.0 / 3.0, method="linear")
    )
    return {
        "p_market_quartiles": _quartiles(p_market),
        "senex_raw_quartiles": _quartiles(senex),
        "abs_disagreement_quartiles": _quartiles(absolute),
        "near_zero_disagreement_epsilon": near_zero,
    }


def _quartile_bucket(value: float, cuts: list[float]) -> str:
    if len(cuts) != 3:
        raise ValueError("quartile cut points must contain exactly three values")
    if value <= cuts[0]:
        return "Q1"
    if value <= cuts[1]:
        return "Q2"
    if value <= cuts[2]:
        return "Q3"
    return "Q4"


def utc_session_bucket(timestamp: float) -> str:
    hour = datetime.fromtimestamp(float(timestamp), timezone.utc).hour
    if hour <= 5:
        return "00-05"
    if hour <= 11:
        return "06-11"
    if hour <= 17:
        return "12-17"
    return "18-23"


def tabular_rows(
    observations: Iterable[order097.JoinedObservation],
    *,
    split: str,
    cutpoints: dict[str, object],
) -> list[dict[str, object]]:
    """Build one deterministic descriptive row per exact market."""
    p_cuts = [float(x) for x in cutpoints["p_market_quartiles"]]
    s_cuts = [float(x) for x in cutpoints["senex_raw_quartiles"]]
    d_cuts = [float(x) for x in cutpoints["abs_disagreement_quartiles"]]
    epsilon = float(cutpoints["near_zero_disagreement_epsilon"])

    result: list[dict[str, object]] = []
    for item in unique_market_observations(observations):
        p_market = float(item.pair.p_market)
        senex = float(item.pair.senex_raw_up)
        signed = senex - p_market
        absolute = abs(signed)
        if signed < -epsilon:
            sign = "NEGATIVE"
        elif signed > epsilon:
            sign = "POSITIVE"
        else:
            sign = "NEAR_ZERO"

        result.append({
            "market_slug": item.pair.market_slug,
            "condition_id": item.pair.condition_id,
            "decision_ts": float(item.pair.decision_ts),
            "market_start_ts": int(item.pair.market_start_ts),
            "market_end_ts": int(item.pair.market_end_ts),
            "label_up": int(item.label_up),
            "p_market": p_market,
            "senex_raw_up": senex,
            "market_logit": order097._logit(p_market),
            "senex_logit": order097._logit(senex),
            "signed_disagreement": signed,
            "abs_disagreement": absolute,
            "p_market_quartile": _quartile_bucket(p_market, p_cuts),
            "senex_raw_quartile": _quartile_bucket(senex, s_cuts),
            "abs_disagreement_quartile": _quartile_bucket(absolute, d_cuts),
            "disagreement_sign": sign,
            "utc_session": utc_session_bucket(item.pair.decision_ts),
            "split": str(split).upper(),
        })
    return result


def _binary_log_loss(probability: float, label: int) -> float:
    p = min(1.0 - 1e-12, max(1e-12, float(probability)))
    y = int(label)
    return -(y * math.log(p) + (1 - y) * math.log(1.0 - p))


def summarize_rows(rows: Iterable[dict[str, object]]) -> dict[str, object]:
    data = list(rows)
    if not data:
        return {
            "n": 0,
            "inference_eligible": False,
        }
    labels = [int(row["label_up"]) for row in data]
    market = [float(row["p_market"]) for row in data]
    senex = [float(row["senex_raw_up"]) for row in data]
    n = len(data)
    return {
        "n": n,
        "inference_eligible": n >= MIN_INFERENCE_CELL,
        "up_rate": float(sum(labels) / n),
        "mean_p_market": float(np.mean(market)),
        "mean_senex_raw_up": float(np.mean(senex)),
        "market_brier": float(np.mean([(p - y) ** 2 for p, y in zip(market, labels)])),
        "senex_raw_brier": float(np.mean([(p - y) ** 2 for p, y in zip(senex, labels)])),
        "market_log_loss": float(np.mean([
            _binary_log_loss(p, y) for p, y in zip(market, labels)
        ])),
        "senex_raw_log_loss": float(np.mean([
            _binary_log_loss(p, y) for p, y in zip(senex, labels)
        ])),
    }


def grouped_summaries(
    rows: Iterable[dict[str, object]],
    field: str,
) -> dict[str, dict[str, object]]:
    data = list(rows)
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in data:
        groups[str(row[field])].append(row)
    return {
        key: summarize_rows(groups[key])
        for key in sorted(groups)
    }


def descriptive_report(
    train_rows: list[dict[str, object]],
    holdout_rows: list[dict[str, object]],
) -> dict[str, object]:
    combined = [*train_rows, *holdout_rows]
    return {
        "whole_sample": summarize_rows(combined),
        "split": {
            "TRAIN": summarize_rows(train_rows),
            "HOLDOUT": summarize_rows(holdout_rows),
        },
        "p_market_quartile": grouped_summaries(combined, "p_market_quartile"),
        "senex_raw_quartile": grouped_summaries(combined, "senex_raw_quartile"),
        "abs_disagreement_quartile": grouped_summaries(
            combined, "abs_disagreement_quartile"
        ),
        "disagreement_sign": grouped_summaries(combined, "disagreement_sign"),
        "utc_session": grouped_summaries(combined, "utc_session"),
    }


def nested_loss_rows(
    observations: Iterable[order097.JoinedObservation],
    market_only: order097.LogisticModel,
    market_plus_senex: order097.LogisticModel,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in observations:
        market_logit = order097._logit(float(item.pair.p_market))
        senex_logit = order097._logit(float(item.pair.senex_raw_up))
        market_probability = market_only.predict(market_logit)
        augmented_probability = market_plus_senex.predict(
            market_logit,
            senex_logit,
        )
        label = int(item.label_up)
        market_brier = (market_probability - label) ** 2
        augmented_brier = (augmented_probability - label) ** 2
        market_log_loss = _binary_log_loss(market_probability, label)
        augmented_log_loss = _binary_log_loss(augmented_probability, label)
        rows.append({
            "market_key": f"{item.pair.market_slug}|{item.pair.condition_id}",
            "market_slug": item.pair.market_slug,
            "condition_id": item.pair.condition_id,
            "prediction_id": item.pair.prediction_id,
            "decision_ts": float(item.pair.decision_ts),
            "market_probability": market_probability,
            "augmented_probability": augmented_probability,
            "label_up": label,
            "brier_delta": augmented_brier - market_brier,
            "log_loss_delta": augmented_log_loss - market_log_loss,
        })
    return rows


def _bootstrap_metric(
    groups: dict[str, list[dict[str, object]]],
    market_keys: list[str],
    *,
    metric: str,
    n_bootstrap: int,
    random_seed: int,
) -> dict[str, float]:
    rng = np.random.default_rng(random_seed)
    observed_rows = [
        row for key in market_keys for row in groups[key]
    ]
    observed = float(np.mean([float(row[metric]) for row in observed_rows]))

    replicate_means = np.empty(n_bootstrap, dtype=float)
    n_markets = len(market_keys)
    for index in range(n_bootstrap):
        sampled = rng.integers(0, n_markets, size=n_markets)
        values: list[float] = []
        for sampled_index in sampled:
            key = market_keys[int(sampled_index)]
            values.extend(float(row[metric]) for row in groups[key])
        replicate_means[index] = float(np.mean(values))

    return {
        "mean_delta": observed,
        "ci95_low": float(np.quantile(replicate_means, 0.025)),
        "ci95_high": float(np.quantile(replicate_means, 0.975)),
        "fraction_augmented_better": float(np.mean(replicate_means < 0.0)),
    }


def cluster_bootstrap(
    loss_rows: Iterable[dict[str, object]],
    *,
    n_bootstrap: int = DEFAULT_BOOTSTRAP,
    random_seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    """Bootstrap unique markets, carrying every row within a sampled market."""
    data = list(loss_rows)
    if not data:
        raise ValueError("holdout loss rows are empty")
    if isinstance(n_bootstrap, bool) or int(n_bootstrap) <= 0:
        raise ValueError("n_bootstrap must be positive")

    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in data:
        key = str(row["market_key"])
        groups[key].append(row)
    market_keys = sorted(groups)
    if len(market_keys) < 2:
        raise ValueError("at least two holdout markets are required for bootstrap")

    return {
        "method": "UNIQUE_MARKET_CLUSTER_BOOTSTRAP",
        "n_rows": len(data),
        "n_markets": len(market_keys),
        "n_bootstrap": int(n_bootstrap),
        "random_seed": int(random_seed),
        "brier": _bootstrap_metric(
            groups,
            market_keys,
            metric="brier_delta",
            n_bootstrap=int(n_bootstrap),
            random_seed=int(random_seed),
        ),
        "log_loss": _bootstrap_metric(
            groups,
            market_keys,
            metric="log_loss_delta",
            n_bootstrap=int(n_bootstrap),
            random_seed=int(random_seed) + 1,
        ),
    }


def _enrich_loss_rows_with_tabular_buckets(
    loss_rows: list[dict[str, object]],
    holdout_table: list[dict[str, object]],
) -> list[dict[str, object]]:
    by_market = {
        f"{row['market_slug']}|{row['condition_id']}": row
        for row in holdout_table
    }
    result: list[dict[str, object]] = []
    fields = (
        "p_market_quartile",
        "senex_raw_quartile",
        "abs_disagreement_quartile",
        "disagreement_sign",
        "utc_session",
    )
    for loss in loss_rows:
        key = str(loss["market_key"])
        meta = by_market.get(key)
        if meta is None:
            raise ValueError(f"missing tabular metadata for holdout market {key}")
        row = dict(loss)
        for field in fields:
            row[field] = meta[field]
        result.append(row)
    return result


def corrected_subgroup_inference(
    loss_rows: Iterable[dict[str, object]],
    *,
    fields: tuple[str, ...] = (
        "p_market_quartile",
        "senex_raw_quartile",
        "abs_disagreement_quartile",
        "disagreement_sign",
        "utc_session",
    ),
) -> dict[str, object]:
    """Exploratory holdout subgroup tests with one family-wide correction.

    The one-sided exact sign test asks whether augmented Brier loss is lower
    than market-only more often than chance within each preregistered cell.
    Cells below MIN_INFERENCE_CELL remain descriptive-only and are excluded
    from the multiple-testing family.
    """
    data = list(loss_rows)
    cells: list[dict[str, object]] = []
    eligible_indices: list[int] = []
    raw_p_values: list[float] = []

    for field in fields:
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in data:
            if field not in row:
                raise ValueError(f"missing subgroup field: {field}")
            grouped[str(row[field])].append(row)

        for value in sorted(grouped):
            group = grouped[value]
            brier = [float(row["brier_delta"]) for row in group]
            log_loss = [float(row["log_loss_delta"]) for row in group]
            nonzero = [delta for delta in brier if abs(delta) > 1e-15]
            negative = sum(delta < 0.0 for delta in nonzero)
            inference_eligible = len(group) >= MIN_INFERENCE_CELL
            raw_p: float | None = None
            if inference_eligible:
                if nonzero:
                    raw_p = float(
                        binomtest(
                            negative,
                            n=len(nonzero),
                            p=0.5,
                            alternative="greater",
                        ).pvalue
                    )
                else:
                    raw_p = 1.0

            cell = {
                "field": field,
                "value": value,
                "n": len(group),
                "inference_eligible": inference_eligible,
                "mean_brier_delta": float(np.mean(brier)),
                "mean_log_loss_delta": float(np.mean(log_loss)),
                "augmented_brier_better_count": negative,
                "nonzero_brier_delta_count": len(nonzero),
                "raw_p_value": raw_p,
                "holm_rejected": False,
                "bh_rejected": False,
            }
            cells.append(cell)
            if raw_p is not None:
                eligible_indices.append(len(cells) - 1)
                raw_p_values.append(raw_p)

    correction = multiple_hypothesis_correction(
        raw_p_values,
        fdr_alpha=0.05,
        fwer_alpha=0.05,
    )
    for position, cell_index in enumerate(eligible_indices):
        cells[cell_index]["holm_rejected"] = bool(
            correction.holm_rejected[position]
        )
        cells[cell_index]["bh_rejected"] = bool(
            correction.bh_rejected[position]
        )

    return {
        "family": "PREREGISTERED_HOLDOUT_BRIER_EXACT_SIGN_TEST",
        "alternative": "AUGMENTED_BRIER_LOWER_MORE_OFTEN_THAN_CHANCE",
        "min_cell_n": MIN_INFERENCE_CELL,
        "n_hypotheses": len(raw_p_values),
        "holm_alpha": 0.05,
        "bh_alpha": 0.05,
        "n_rejected_holm": int(correction.n_rejected_holm),
        "n_rejected_bh": int(correction.n_rejected_bh),
        "cells": cells,
        "interpretation": (
            "Exploratory only. Corrected subgroup rejection does not override "
            "the primary nested holdout + bootstrap edge verdict."
        ),
    }


def edge_verdict(bootstrap: dict[str, object]) -> str:
    brier = bootstrap["brier"]
    log_loss = bootstrap["log_loss"]
    if (
        float(brier["mean_delta"]) <= -PRACTICAL_BRIER_GAIN
        and float(brier["ci95_high"]) < 0.0
        and float(log_loss["ci95_high"]) < 0.0
    ):
        return "EDGE_SUPPORTED"
    return "INCREMENTAL_EDGE_NOT_DEMONSTRATED"


def run_offline(
    predictions_path: str | Path,
    resolutions_path: str | Path,
    *,
    predictions_manifest_path: str | Path,
    resolutions_manifest_path: str | Path,
    train_fraction: float = 0.67,
    n_bootstrap: int = DEFAULT_BOOTSTRAP,
    random_seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    artifact_provenance = verify_order098_artifacts(
        predictions_path,
        predictions_manifest_path,
        resolutions_path,
        resolutions_manifest_path,
    )
    prediction_rows = order097.read_jsonl(predictions_path)
    resolution_rows = order097.read_jsonl(resolutions_path)
    pairs = order097.extract_t0_pairs(prediction_rows)
    status = order097.experiment_status(
        pairs,
        resolution_rows,
        train_fraction=train_fraction,
    )
    if status["status"] != "READY_FOR_CHRONOLOGICAL_CALIBRATION":
        return {
            **status,
            "order": "ORDER099",
            "analysis_status": "BLOCKED_DATA",
            "artifact_provenance": artifact_provenance,
        }

    joined = order097.join_resolutions(pairs, resolution_rows)
    train_source, holdout_source = order097.chronological_market_split(
        joined,
        train_fraction=train_fraction,
    )
    train = unique_market_observations(train_source)
    holdout = unique_market_observations(holdout_source)
    total_unique_markets = len(train) + len(holdout)
    minimum_market_blocker = minimum_market_gate(total_unique_markets)
    if minimum_market_blocker is not None:
        return {
            "order": "ORDER099",
            "analysis_status": "BLOCKED_DATA",
            "edge": "UNPROVEN",
            "artifact_provenance": artifact_provenance,
            "blocker_reason": minimum_market_blocker,
            "minimum_unique_markets": MIN_TOTAL_UNIQUE_MARKETS,
            "total_unique_markets": total_unique_markets,
            "train_source_rows": len(train_source),
            "holdout_source_rows": len(holdout_source),
            "train_markets": len(train),
            "holdout_markets": len(holdout),
        }
    if len(train) < 8:
        return {
            "order": "ORDER099",
            "analysis_status": "BLOCKED_DATA",
            "edge": "UNPROVEN",
            "artifact_provenance": artifact_provenance,
            "blocker_reason": (
                "at least 8 causally available unique training markets are required"
            ),
            "train_source_rows": len(train_source),
            "holdout_source_rows": len(holdout_source),
            "train_markets": len(train),
            "holdout_markets": len(holdout),
        }
    if {int(item.label_up) for item in train} != {0, 1}:
        return {
            "order": "ORDER099",
            "analysis_status": "BLOCKED_DATA",
            "edge": "UNPROVEN",
            "artifact_provenance": artifact_provenance,
            "blocker_reason": "unique training markets require both UP and DOWN labels",
            "train_source_rows": len(train_source),
            "holdout_source_rows": len(holdout_source),
            "train_markets": len(train),
            "holdout_markets": len(holdout),
        }
    if len(holdout) < 2:
        return {
            "order": "ORDER099",
            "analysis_status": "BLOCKED_DATA",
            "edge": "UNPROVEN",
            "artifact_provenance": artifact_provenance,
            "blocker_reason": "at least two unique holdout markets are required",
            "train_source_rows": len(train_source),
            "holdout_source_rows": len(holdout_source),
            "train_markets": len(train),
            "holdout_markets": len(holdout),
        }

    cutpoints = derive_train_cutpoints(train)
    train_table = tabular_rows(train, split="TRAIN", cutpoints=cutpoints)
    holdout_table = tabular_rows(
        holdout,
        split="HOLDOUT",
        cutpoints=cutpoints,
    )

    market_only, market_plus_senex = order097.fit_incremental_models(train)
    paired = order097.evaluate_incremental_models(
        holdout,
        market_only,
        market_plus_senex,
    )
    loss_rows = nested_loss_rows(
        holdout,
        market_only,
        market_plus_senex,
    )
    loss_rows = _enrich_loss_rows_with_tabular_buckets(
        loss_rows,
        holdout_table,
    )
    bootstrap = cluster_bootstrap(
        loss_rows,
        n_bootstrap=n_bootstrap,
        random_seed=random_seed,
    )
    subgroup_inference = corrected_subgroup_inference(loss_rows)

    return {
        "order": "ORDER099",
        "analysis_status": "EVALUATED_TABULAR_HOLDOUT",
        "edge": "UNPROVEN",
        "verdict": edge_verdict(bootstrap),
        "preregistered_practical_brier_gain": PRACTICAL_BRIER_GAIN,
        "artifact_provenance": artifact_provenance,
        "train_fraction": train_fraction,
        "train_source_rows": len(train_source),
        "holdout_source_rows": len(holdout_source),
        "train_rows": len(train),
        "holdout_rows": len(holdout),
        "train_markets": len(train_table),
        "holdout_markets": len(holdout_table),
        "market_weighting": "ONE_EARLIEST_CAUSAL_T0_ROW_PER_UNIQUE_MARKET",
        "cutpoints_fit_scope": "TRAIN_ONLY",
        "cutpoints": cutpoints,
        "descriptive": descriptive_report(train_table, holdout_table),
        "nested_holdout": paired,
        "bootstrap": bootstrap,
        "corrected_subgroup_inference": subgroup_inference,
        "model_scope": {
            "market_only": "TRAIN_ONLY",
            "market_plus_senex": "TRAIN_ONLY",
            "holdout": "UNTOUCHED_BY_FIT_AND_BUCKET_CUTPOINT_SELECTION",
            "market_weighting": "ONE_UNIQUE_MARKET_ONE_MODEL_ROW",
        },
        "interpretation": (
            "Research-only falsification. EDGE_SUPPORTED requires a preregistered "
            "practical Brier improvement plus both Brier and log-loss 95% market-"
            "cluster bootstrap upper bounds below zero. Subgroup inference is "
            "family-corrected and exploratory only. No HMM/regime rescue is "
            "allowed in ORDER099."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ORDER099 preregistered tabular falsification"
    )
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--resolutions", required=True)
    parser.add_argument("--predictions-manifest", required=True)
    parser.add_argument("--resolutions-manifest", required=True)
    parser.add_argument("--train-fraction", type=float, default=0.67)
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    try:
        result = run_offline(
            args.predictions,
            args.resolutions,
            predictions_manifest_path=args.predictions_manifest,
            resolutions_manifest_path=args.resolutions_manifest,
            train_fraction=args.train_fraction,
            n_bootstrap=args.bootstrap,
            random_seed=args.seed,
        )
    except ArtifactContractError as exc:
        result = {
            "order": "ORDER099",
            "analysis_status": "BLOCKED_DATA",
            "edge": "UNPROVEN",
            "blocker_reason": f"ARTIFACT_CONTRACT: {exc}",
        }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("analysis_status") != "BLOCKED_DATA" else 2


if __name__ == "__main__":
    raise SystemExit(main())
