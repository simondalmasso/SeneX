from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any, Iterable, Sequence

from edge_lab.arq2_score_denominator_007 import deterministic_nonoverlap, rank_ic
from edge_lab.arq2_score_readback_004 import roc_auc

SOURCE_DATASET_SHA256 = "606eda9b102243b08fc21fafed954c1da9a22d28edbeb89054204983d7221e22"
REFERENCE_CORE_HEAD = "c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a"
EXPECTED_NONOVERLAP_N = 32
EXPECTED_NONOVERLAP_ID_SHA256 = "0a31c990bc28313429dff96df086f6da41db448ea7f76c686f0e32fbc51cf8bd"
REPLICATES = 10000
BOOTSTRAP_SEED = 20260923
PERMUTATION_SEED = 20260923


class PressurePersistenceInsufficient(ValueError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    x = (len(ordered) - 1) * p
    lo = int(math.floor(x))
    hi = int(math.ceil(x))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - x) + ordered[hi] * (x - lo)


def load_source_dataset(path: Path) -> list[dict[str, Any]]:
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SOURCE_DATASET_SHA256:
        raise ValueError(
            f"SOURCE_DATASET_HASH_MISMATCH:{digest}:{SOURCE_DATASET_SHA256}"
        )
    rows = [
        json.loads(line)
        for line in raw.decode("utf-8").splitlines()
        if line.strip()
    ]
    return rows


def source_nonoverlap_ids(rows: Iterable[dict[str, Any]]) -> list[int]:
    selected = deterministic_nonoverlap(list(rows))
    ids = [int(row["source_prediction_id"]) for row in selected]
    digest = hashlib.sha256(",".join(map(str, ids)).encode("utf-8")).hexdigest()
    if len(ids) != EXPECTED_NONOVERLAP_N or digest != EXPECTED_NONOVERLAP_ID_SHA256:
        raise ValueError(
            f"NONOVERLAP_IDENTITY_MISMATCH:{len(ids)}:{digest}"
        )
    return ids


def build_feature_scores(
    *,
    total_pressure: float,
    orderflow_pressure: float,
    funding_pressure: float,
    oi_pressure: float,
) -> dict[str, float]:
    values = {
        "total_pressure": total_pressure,
        "orderflow_pressure": orderflow_pressure,
        "funding_pressure": funding_pressure,
        "oi_pressure": oi_pressure,
    }
    parsed: dict[str, float] = {}
    for key, value in values.items():
        if value is None:
            raise PressurePersistenceInsufficient(f"MISSING_{key.upper()}")
        parsed_value = float(value)
        if not math.isfinite(parsed_value):
            raise PressurePersistenceInsufficient(f"INVALID_{key.upper()}")
        parsed[key] = parsed_value

    top2 = (
        parsed["orderflow_pressure"]
        + parsed["funding_pressure"]
        + parsed["oi_pressure"]
    )
    total = parsed["total_pressure"]
    return {
        "full_score": total,
        "top2_pressure": top2,
        "no_top2_score": total - top2,
        "micro_only_score": top2,
    }


def _decode_cold(
    source_row: dict[str, Any],
    cold_row: dict[str, Any] | None,
) -> dict[str, Any]:
    pid = int(source_row["source_prediction_id"])
    if cold_row is None:
        return {"prediction_id": pid, "integrity": "COLD_MISSING"}

    if int(cold_row.get("prediction_id") or -1) != pid:
        return {"prediction_id": pid, "integrity": "PREDICTION_ID_MISMATCH"}

    payload = str(cold_row.get("payload") or "")
    expected_payload_hash = str(cold_row.get("payload_sha256") or "")
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if not expected_payload_hash or payload_hash != expected_payload_hash:
        return {"prediction_id": pid, "integrity": "PAYLOAD_HASH_MISMATCH"}

    try:
        document = json.loads(payload)
    except json.JSONDecodeError:
        return {"prediction_id": pid, "integrity": "PAYLOAD_JSON_INVALID"}

    audit = document.get("audit")
    if not isinstance(audit, dict):
        return {"prediction_id": pid, "integrity": "AUDIT_MISSING"}

    replay = audit.get("decision_replay_v1")
    provenance = replay.get("runtime_provenance") if isinstance(replay, dict) else None
    if not isinstance(provenance, dict):
        return {"prediction_id": pid, "integrity": "RUNTIME_PROVENANCE_MISSING"}
    if provenance.get("exact") is not True:
        return {"prediction_id": pid, "integrity": "RUNTIME_PROVENANCE_NOT_EXACT"}
    if provenance.get("source_commit") != REFERENCE_CORE_HEAD:
        return {"prediction_id": pid, "integrity": "RUNTIME_PROVENANCE_COMMIT_MISMATCH"}

    pipeline = audit.get("pipeline")
    step2 = pipeline.get("step2_features") if isinstance(pipeline, dict) else None
    if not isinstance(step2, dict):
        return {"prediction_id": pid, "integrity": "STEP2_FEATURES_MISSING"}

    pressures = step2.get("pressures")
    pressures_present = isinstance(pressures, dict)
    if not pressures_present:
        return {
            "prediction_id": pid,
            "integrity": "PASS",
            "pressures_present": False,
            "top2_complete": False,
        }

    component_values = {
        "orderflow": pressures.get("orderflow"),
        "funding": pressures.get("funding"),
        "oi": pressures.get("oi"),
    }
    top2_complete = (
        step2.get("total_pressure") is not None
        and all(value is not None for value in component_values.values())
    )
    return {
        "prediction_id": pid,
        "integrity": "PASS",
        "pressures_present": True,
        "top2_complete": top2_complete,
        "total_pressure": step2.get("total_pressure"),
        "orderflow_pressure": component_values["orderflow"],
        "funding_pressure": component_values["funding"],
        "oi_pressure": component_values["oi"],
    }


def paired_bootstrap_delta_auc(
    rows: Sequence[dict[str, Any]],
    *,
    replicates: int = REPLICATES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    material = list(rows)
    if not material:
        raise ValueError("EMPTY_INFERENTIAL_ROWS")

    full_pairs = [
        (float(row["full_score"]), int(row["y_up"]))
        for row in material
    ]
    no_top2_pairs = [
        (float(row["no_top2_score"]), int(row["y_up"]))
        for row in material
    ]
    auc_full = roc_auc(full_pairs)
    auc_no_top2 = roc_auc(no_top2_pairs)
    if auc_full is None or auc_no_top2 is None:
        raise ValueError("PRIMARY_AUC_UNDEFINED")

    rng = random.Random(seed)
    deltas: list[float] = []
    n = len(material)
    for _ in range(int(replicates)):
        indices = [rng.randrange(n) for _ in range(n)]
        labels = {int(material[index]["y_up"]) for index in indices}
        if len(labels) < 2:
            continue
        full_sample = [
            (float(material[index]["full_score"]), int(material[index]["y_up"]))
            for index in indices
        ]
        no_top2_sample = [
            (float(material[index]["no_top2_score"]), int(material[index]["y_up"]))
            for index in indices
        ]
        boot_full = roc_auc(full_sample)
        boot_no_top2 = roc_auc(no_top2_sample)
        if boot_full is None or boot_no_top2 is None:
            continue
        deltas.append(float(boot_full - boot_no_top2))

    low = _percentile(deltas, 0.025)
    high = _percentile(deltas, 0.975)
    if low is None or high is None:
        raise ValueError("BOOTSTRAP_CI_UNDEFINED")

    return {
        "auc_full": float(auc_full),
        "auc_no_top2": float(auc_no_top2),
        "delta_auc": float(auc_full - auc_no_top2),
        "ci95": [float(low), float(high)],
        "valid_replicates": len(deltas),
        "requested_replicates": int(replicates),
        "seed": int(seed),
        "paired_indices": True,
    }


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _permuted_delta_auc(
    rows: Sequence[dict[str, Any]],
    *,
    seed: int = PERMUTATION_SEED,
) -> float | None:
    material = list(rows)
    top2 = [float(row["top2_pressure"]) for row in material]
    rng = random.Random(seed)
    rng.shuffle(top2)
    full_pairs = [
        (float(row["full_score"]), int(row["y_up"]))
        for row in material
    ]
    no_top2_pairs = [
        (
            float(row["full_score"]) - float(permuted_top2),
            int(row["y_up"]),
        )
        for row, permuted_top2 in zip(material, top2)
    ]
    auc_full = roc_auc(full_pairs)
    auc_no_top2 = roc_auc(no_top2_pairs)
    if auc_full is None or auc_no_top2 is None:
        return None
    return float(auc_full - auc_no_top2)


def analyze_ablation(
    rows: Sequence[dict[str, Any]],
    cold_by_id: dict[int, dict[str, Any]],
    *,
    nonoverlap_ids: Sequence[int],
) -> dict[str, Any]:
    source_rows = list(rows)
    inferential_ids = [int(value) for value in nonoverlap_ids]
    source_by_id = {
        int(row["source_prediction_id"]): row
        for row in source_rows
    }
    if any(pid not in source_by_id for pid in inferential_ids):
        raise ValueError("NONOVERLAP_NOT_SUBSET_OF_SOURCE_DATASET")

    decoded: dict[int, dict[str, Any]] = {}
    integrity_failures: list[dict[str, Any]] = []
    pressures_present_n = 0
    top2_components_present_n = 0

    for row in source_rows:
        pid = int(row["source_prediction_id"])
        item = _decode_cold(row, cold_by_id.get(pid))
        decoded[pid] = item
        if item.get("integrity") != "PASS":
            integrity_failures.append(
                {"prediction_id": pid, "reason": item.get("integrity")}
            )
            continue
        if item.get("pressures_present") is True:
            pressures_present_n += 1
        if item.get("top2_complete") is True:
            top2_components_present_n += 1

    if integrity_failures:
        return {
            "status": "BLOCK_REAL",
            "integrity_failures": integrity_failures,
            "pressures_present_n": pressures_present_n,
            "top2_components_present_n": top2_components_present_n,
            "nonoverlap_top2_complete_n": 0,
            "delta_auc_top2": None,
            "top2_verdict": "UNMEASURED",
        }

    nonoverlap_top2_complete_n = sum(
        1
        for pid in inferential_ids
        if decoded[pid].get("top2_complete") is True
    )
    denominator_n = len(source_rows)
    completeness_rate = (
        top2_components_present_n / denominator_n
        if denominator_n
        else 0.0
    )
    if (
        completeness_rate < 0.95
        or nonoverlap_top2_complete_n != len(inferential_ids)
    ):
        missing_nonoverlap = [
            pid
            for pid in inferential_ids
            if decoded[pid].get("top2_complete") is not True
        ]
        return {
            "status": "PERSISTENCE_INSUFFICIENT",
            "pressures_present_n": pressures_present_n,
            "top2_components_present_n": top2_components_present_n,
            "nonoverlap_top2_complete_n": nonoverlap_top2_complete_n,
            "top2_completeness_rate": completeness_rate,
            "missing_nonoverlap_ids": missing_nonoverlap,
            "auc_full": None,
            "auc_no_top2": None,
            "delta_auc_top2": None,
            "delta_auc_ci95": None,
            "auc_micro_only": None,
            "rank_ic_micro_only": None,
            "top2_mean_abs_contribution": None,
            "top2_sign_agreement_rate": None,
            "top2_sign_flip_rate": None,
            "permuted_delta_auc": None,
            "top2_verdict": "UNMEASURED",
        }

    inferential: list[dict[str, Any]] = []
    for pid in inferential_ids:
        source = source_by_id[pid]
        persisted = decoded[pid]
        features = build_feature_scores(
            total_pressure=persisted["total_pressure"],
            orderflow_pressure=persisted["orderflow_pressure"],
            funding_pressure=persisted["funding_pressure"],
            oi_pressure=persisted["oi_pressure"],
        )
        inferential.append(
            {
                "source_prediction_id": pid,
                "y_up": int(source["y_up"]),
                **features,
            }
        )

    primary = paired_bootstrap_delta_auc(inferential)
    low, high = primary["ci95"]
    if low > 0:
        verdict = "POSITIVE_CONTRIBUTION_DIAGNOSTIC"
    elif high < 0:
        verdict = "NEGATIVE_CONTRIBUTION_DIAGNOSTIC"
    else:
        verdict = "INCONCLUSIVE"

    micro_pairs = [
        (float(row["micro_only_score"]), int(row["y_up"]))
        for row in inferential
    ]
    auc_micro = roc_auc(micro_pairs)
    micro_rank = rank_ic(micro_pairs)
    mean_abs = (
        sum(abs(float(row["top2_pressure"])) for row in inferential)
        / len(inferential)
    )
    sign_agreement = (
        sum(
            1
            for row in inferential
            if _sign(float(row["top2_pressure"]))
            == _sign(float(row["full_score"]))
        )
        / len(inferential)
    )
    sign_flip = (
        sum(
            1
            for row in inferential
            if _sign(float(row["full_score"]))
            != _sign(float(row["no_top2_score"]))
        )
        / len(inferential)
    )

    return {
        "status": "COMPLETE",
        "pressures_present_n": pressures_present_n,
        "top2_components_present_n": top2_components_present_n,
        "nonoverlap_top2_complete_n": nonoverlap_top2_complete_n,
        "top2_completeness_rate": completeness_rate,
        "nonoverlap_ids": inferential_ids,
        "n_inferential": len(inferential),
        "auc_full": primary["auc_full"],
        "auc_no_top2": primary["auc_no_top2"],
        "delta_auc_top2": primary["delta_auc"],
        "delta_auc_ci95": primary["ci95"],
        "bootstrap_valid_replicates": primary["valid_replicates"],
        "bootstrap_requested_replicates": primary["requested_replicates"],
        "bootstrap_seed": primary["seed"],
        "paired_indices": primary["paired_indices"],
        "auc_micro_only": auc_micro,
        "rank_ic_micro_only": micro_rank,
        "top2_mean_abs_contribution": mean_abs,
        "top2_sign_agreement_rate": sign_agreement,
        "top2_sign_flip_rate": sign_flip,
        "permuted_delta_auc": _permuted_delta_auc(inferential),
        "permutation_seed": PERMUTATION_SEED,
        "top2_verdict": verdict,
    }
