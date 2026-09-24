from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import edge_lab.arq2_top2_ablation_008 as a008
from edge_lab.arq2_score_denominator_007 import build_cold_query, rank_ic
from edge_lab.arq2_score_readback_004 import roc_auc

SOURCE_DATASET_SHA256 = a008.SOURCE_DATASET_SHA256
REFERENCE_CORE_HEAD = a008.REFERENCE_CORE_HEAD
EXPECTED_NONOVERLAP_N = a008.EXPECTED_NONOVERLAP_N
EXPECTED_NONOVERLAP_ID_SHA256 = a008.EXPECTED_NONOVERLAP_ID_SHA256
REPLICATES = a008.REPLICATES
BOOTSTRAP_SEED = a008.BOOTSTRAP_SEED
PERMUTATION_SEED = a008.PERMUTATION_SEED

RECOVERY_ROW_ID = 5858
RECOVERY_COMPONENT = "oi"
RECOVERY_EFFECTIVE_VALUE = 0.0
RECOVERY_VERDICT = "SEMANTICALLY_RECOVERABLE_WITHOUT_IMPUTATION"
RECOVERY_SEMANTICS = "MASKED_UNAVAILABLE_ZERO_EFFECTIVE_CONTRIBUTION"
ALGEBRA_TOLERANCE = 1e-6


def load_source_population(path: Path) -> tuple[list[dict[str, Any]], list[int]]:
    rows = a008.load_source_dataset(Path(path))
    ids = a008.source_nonoverlap_ids(rows)
    return rows, ids


def build_exact_cold_query(prediction_ids: Sequence[int]) -> str:
    ids = [int(value) for value in prediction_ids]
    if len(ids) != EXPECTED_NONOVERLAP_N:
        raise ValueError(f"EXACT_COLD_ID_COUNT_MISMATCH:{len(ids)}")
    digest = hashlib.sha256(",".join(map(str, ids)).encode("utf-8")).hexdigest()
    if digest != EXPECTED_NONOVERLAP_ID_SHA256:
        raise ValueError(f"EXACT_COLD_IDENTITY_MISMATCH:{digest}")
    return build_cold_query(ids)


def load_recovery_proof(path: Path) -> dict[str, Any]:
    proof = json.loads(Path(path).read_text(encoding="utf-8"))
    required = (
        proof.get("num_order") == "ARQ2-TOP2-MISSING-SEMANTICS-009"
        and proof.get("status") == "COMPLETE"
        and int(proof.get("row_id") or -1) == RECOVERY_ROW_ID
        and proof.get("row_integrity") == "PASS"
        and proof.get("reference_core_head") == REFERENCE_CORE_HEAD
        and proof.get("source_dataset_sha256") == SOURCE_DATASET_SHA256
        and proof.get("recovery_verdict") == RECOVERY_VERDICT
        and proof.get("null_oi_semantics") == RECOVERY_SEMANTICS
    )
    if not required:
        raise ValueError("RECOVERY_ARTIFACT_INVALID")
    return proof


def _parse_cold_row(
    prediction_id: int,
    cold_row: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    pid = int(prediction_id)
    if cold_row is None:
        raise ValueError(f"COLD_MISSING:{pid}")
    if int(cold_row.get("prediction_id") or -1) != pid:
        raise ValueError(f"PREDICTION_ID_MISMATCH:{pid}")

    payload = str(cold_row.get("payload") or "")
    expected_hash = str(cold_row.get("payload_sha256") or "")
    actual_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if not expected_hash or actual_hash != expected_hash:
        raise ValueError(f"PAYLOAD_HASH_MISMATCH:{pid}")

    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"PAYLOAD_JSON_INVALID:{pid}") from exc

    audit = document.get("audit")
    if not isinstance(audit, dict):
        raise ValueError(f"AUDIT_MISSING:{pid}")

    replay = audit.get("decision_replay_v1")
    provenance = replay.get("runtime_provenance") if isinstance(replay, dict) else None
    if not isinstance(provenance, dict):
        raise ValueError(f"RUNTIME_PROVENANCE_MISSING:{pid}")
    if provenance.get("exact") is not True:
        raise ValueError(f"RUNTIME_PROVENANCE_NOT_EXACT:{pid}")
    if provenance.get("source_commit") != REFERENCE_CORE_HEAD:
        raise ValueError(f"RUNTIME_PROVENANCE_COMMIT_MISMATCH:{pid}")

    pipeline = audit.get("pipeline")
    step2 = pipeline.get("step2_features") if isinstance(pipeline, dict) else None
    if not isinstance(step2, dict):
        raise ValueError(f"STEP2_FEATURES_MISSING:{pid}")
    pressures = step2.get("pressures")
    if not isinstance(pressures, dict):
        raise ValueError(f"PRESSURES_MISSING:{pid}")
    return audit, step2


def recover_effective_oi_pressure(
    *,
    prediction_id: int,
    audit: dict[str, Any],
    recovery_proof: dict[str, Any],
) -> dict[str, Any]:
    pid = int(prediction_id)
    if pid != RECOVERY_ROW_ID:
        raise ValueError("RECOVERY_PROOF_FAILED:ROW_NOT_AUTHORIZED")

    if (
        recovery_proof.get("recovery_verdict") != RECOVERY_VERDICT
        or int(recovery_proof.get("row_id") or -1) != RECOVERY_ROW_ID
        or recovery_proof.get("row_integrity") != "PASS"
        or recovery_proof.get("reference_core_head") != REFERENCE_CORE_HEAD
        or recovery_proof.get("source_dataset_sha256") != SOURCE_DATASET_SHA256
    ):
        raise ValueError("RECOVERY_PROOF_FAILED:009_ARTIFACT")

    replay = audit.get("decision_replay_v1")
    provenance = replay.get("runtime_provenance") if isinstance(replay, dict) else None
    if (
        not isinstance(provenance, dict)
        or provenance.get("exact") is not True
        or provenance.get("source_commit") != REFERENCE_CORE_HEAD
    ):
        raise ValueError("RECOVERY_PROOF_FAILED:RUNTIME_PROVENANCE")

    pipeline = audit.get("pipeline")
    if not isinstance(pipeline, dict):
        raise ValueError("RECOVERY_PROOF_FAILED:PIPELINE")

    step1 = pipeline.get("step1_market")
    step2 = pipeline.get("step2_features")
    if not isinstance(step1, dict) or not isinstance(step2, dict):
        raise ValueError("RECOVERY_PROOF_FAILED:PIPELINE_STAGES")

    availability = step1.get("feature_availability_v1")
    oi_availability = (
        availability.get("oi_momentum")
        if isinstance(availability, dict)
        else None
    )
    if (
        not isinstance(oi_availability, dict)
        or oi_availability.get("status") != "MISSING"
        or float(oi_availability.get("fallback_value")) != 0.0
    ):
        raise ValueError("RECOVERY_PROOF_FAILED:AVAILABILITY")

    mask = step2.get("missing_input_mask_v1")
    masked = mask.get("masked_features") if isinstance(mask, dict) else None
    if not isinstance(masked, list) or "oi_momentum" not in masked:
        raise ValueError("RECOVERY_PROOF_FAILED:MASK")

    pressures = step2.get("pressures")
    if not isinstance(pressures, dict) or pressures.get("oi") is not None:
        raise ValueError("RECOVERY_PROOF_FAILED:OI_NOT_NULL")

    numeric_pressures = [
        float(value)
        for value in pressures.values()
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    ]
    total_pressure = step2.get("total_pressure")
    if total_pressure is None or not math.isfinite(float(total_pressure)):
        raise ValueError("RECOVERY_PROOF_FAILED:TOTAL_PRESSURE")
    numeric_sum = sum(numeric_pressures)
    if abs(float(total_pressure) - numeric_sum) > ALGEBRA_TOLERANCE:
        raise ValueError("RECOVERY_PROOF_FAILED:ALGEBRA")

    return {
        "effective_oi_pressure": RECOVERY_EFFECTIVE_VALUE,
        "recovered_by_runtime_semantics": True,
        "availability_status": oi_availability.get("status"),
        "fallback_value": float(oi_availability.get("fallback_value")),
        "masked_features": list(masked),
        "numeric_pressures_sum": numeric_sum,
        "total_pressure": float(total_pressure),
        "algebra_match": True,
    }


def _materialize_row(
    source_row: dict[str, Any],
    cold_row: dict[str, Any] | None,
    recovery_proof: dict[str, Any],
) -> dict[str, Any]:
    pid = int(source_row["source_prediction_id"])
    audit, step2 = _parse_cold_row(pid, cold_row)
    pressures = step2["pressures"]

    total = step2.get("total_pressure")
    orderflow = pressures.get("orderflow")
    funding = pressures.get("funding")
    oi = pressures.get("oi")

    for name, value in (
        ("total_pressure", total),
        ("orderflow_pressure", orderflow),
        ("funding_pressure", funding),
    ):
        if value is None:
            raise a008.PressurePersistenceInsufficient(f"MISSING_{name.upper()}:{pid}")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise a008.PressurePersistenceInsufficient(f"INVALID_{name.upper()}:{pid}")

    recovered = False
    if oi is None:
        if pid != RECOVERY_ROW_ID:
            raise a008.PressurePersistenceInsufficient(f"MISSING_OI_PRESSURE:{pid}")
        proof = recover_effective_oi_pressure(
            prediction_id=pid,
            audit=audit,
            recovery_proof=recovery_proof,
        )
        effective_oi = float(proof["effective_oi_pressure"])
        recovered = True
    else:
        effective_oi = float(oi)
        if not math.isfinite(effective_oi):
            raise a008.PressurePersistenceInsufficient(f"INVALID_OI_PRESSURE:{pid}")

    features = a008.build_feature_scores(
        total_pressure=float(total),
        orderflow_pressure=float(orderflow),
        funding_pressure=float(funding),
        oi_pressure=effective_oi,
    )
    return {
        "source_prediction_id": pid,
        "y_up": int(source_row["y_up"]),
        "persisted_oi_pressure": oi,
        "effective_oi_pressure": effective_oi,
        "recovered_by_runtime_semantics": recovered,
        **features,
    }


def materialize_inferential_rows(
    rows: Sequence[dict[str, Any]],
    cold_by_id: dict[int, dict[str, Any]],
    *,
    recovery_proof: dict[str, Any],
    nonoverlap_ids: Sequence[int],
) -> dict[str, Any]:
    source_by_id = {
        int(row["source_prediction_id"]): row
        for row in rows
    }
    ids = [int(value) for value in nonoverlap_ids]
    if any(pid not in source_by_id for pid in ids):
        return {
            "status": "BLOCK_REAL",
            "reason": "NONOVERLAP_NOT_SUBSET_OF_SOURCE_DATASET",
            "materialized_rows": [],
        }

    materialized: list[dict[str, Any]] = []
    recovered_ids: list[int] = []
    try:
        for pid in ids:
            row = _materialize_row(
                source_by_id[pid],
                cold_by_id.get(pid),
                recovery_proof,
            )
            materialized.append(row)
            if row["recovered_by_runtime_semantics"]:
                recovered_ids.append(pid)
    except a008.PressurePersistenceInsufficient as exc:
        return {
            "status": "PERSISTENCE_INSUFFICIENT",
            "reason": str(exc),
            "materialized_rows": materialized,
            "recovered_ids": recovered_ids,
        }
    except (TypeError, ValueError, KeyError) as exc:
        return {
            "status": "BLOCK_REAL",
            "reason": str(exc),
            "materialized_rows": materialized,
            "recovered_ids": recovered_ids,
        }

    if recovered_ids != [RECOVERY_ROW_ID]:
        return {
            "status": "BLOCK_REAL",
            "reason": f"RECOVERY_CARDINALITY_MISMATCH:{recovered_ids}",
            "materialized_rows": materialized,
            "recovered_ids": recovered_ids,
        }

    return {
        "status": "COMPLETE",
        "materialized_rows": materialized,
        "recovered_ids": recovered_ids,
    }


def analyze_ablation_010(
    rows: Sequence[dict[str, Any]],
    cold_by_id: dict[int, dict[str, Any]],
    *,
    recovery_proof: dict[str, Any],
    nonoverlap_ids: Sequence[int],
) -> dict[str, Any]:
    built = materialize_inferential_rows(
        rows,
        cold_by_id,
        recovery_proof=recovery_proof,
        nonoverlap_ids=nonoverlap_ids,
    )
    if built["status"] != "COMPLETE":
        return {
            **built,
            "auc_full": None,
            "auc_no_top2": None,
            "delta_auc_top2": None,
            "delta_auc_ci95": None,
            "bootstrap_valid_replicates": None,
            "auc_micro_only": None,
            "rank_ic_micro_only": None,
            "top2_mean_abs_contribution": None,
            "top2_sign_agreement_rate": None,
            "top2_sign_flip_rate": None,
            "permuted_delta_auc": None,
            "top2_verdict": "UNMEASURED",
        }

    materialized = built["materialized_rows"]
    primary = a008.paired_bootstrap_delta_auc(
        materialized,
        replicates=REPLICATES,
        seed=BOOTSTRAP_SEED,
    )
    low, high = primary["ci95"]
    if low > 0:
        verdict = "POSITIVE_CONTRIBUTION_DIAGNOSTIC"
    elif high < 0:
        verdict = "NEGATIVE_CONTRIBUTION_DIAGNOSTIC"
    else:
        verdict = "INCONCLUSIVE"

    micro_pairs = [
        (float(row["micro_only_score"]), int(row["y_up"]))
        for row in materialized
    ]
    auc_micro = roc_auc(micro_pairs)
    micro_rank = rank_ic(micro_pairs)
    mean_abs = sum(
        abs(float(row["top2_pressure"]))
        for row in materialized
    ) / len(materialized)
    sign_agreement = sum(
        1
        for row in materialized
        if a008._sign(float(row["top2_pressure"]))
        == a008._sign(float(row["full_score"]))
    ) / len(materialized)
    sign_flip = sum(
        1
        for row in materialized
        if a008._sign(float(row["full_score"]))
        != a008._sign(float(row["no_top2_score"]))
    ) / len(materialized)

    return {
        "status": "COMPLETE",
        "materialized_rows": materialized,
        "recovered_ids": built["recovered_ids"],
        "n_inferential": len(materialized),
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
        "permuted_delta_auc": a008._permuted_delta_auc(
            materialized,
            seed=PERMUTATION_SEED,
        ),
        "permutation_seed": PERMUTATION_SEED,
        "top2_verdict": verdict,
    }
