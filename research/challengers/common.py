from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


RECEIPT_CONTRACT = "senex-challenger-prospective-receipt-v1"
EPS = 1e-12
POSIX_DIR_FSYNC = os.name == "posix"


class ChallengerContractError(RuntimeError):
    """Frozen challenger contract was violated."""


@dataclass(frozen=True)
class Observation:
    market_id: str
    decision_ts: str
    label_end_ts: str
    label: int
    p_market: float
    senex_raw_up: float | None = None


@dataclass(frozen=True)
class PurgedSplit:
    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    test_start_ts: str
    embargo_seconds: int


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_numeric(value: Any, *, digits: int = 12) -> Any:
    """Round finite floats before hashing cross-runtime model artifacts."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ChallengerContractError("non-finite numeric artifact")
        return round(value, digits)
    if isinstance(value, dict):
        return {key: canonical_numeric(item, digits=digits) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(canonical_numeric(item, digits=digits) for item in value)
    if isinstance(value, list):
        return [canonical_numeric(item, digits=digits) for item in value]
    return value


def _utc(value: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ChallengerContractError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ChallengerContractError(f"invalid timestamp: {raw}") from exc
    if parsed.tzinfo is None:
        raise ChallengerContractError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def canonical_ts(value: str) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _prob(value: float, *, name: str) -> float:
    try:
        p = float(value)
    except (TypeError, ValueError) as exc:
        raise ChallengerContractError(f"{name} is not numeric") from exc
    if not math.isfinite(p) or not 0.0 <= p <= 1.0:
        raise ChallengerContractError(f"{name} must be finite in [0,1]")
    return p


def bounded_probability(value: float, *, name: str = "probability") -> float:
    """Probability in the closed interval [0,1] for paths that clip before logit."""
    return _prob(value, name=name)


def parse_utc_timestamp(value: str) -> datetime:
    """Public UTC parser used by challenger modules."""
    return _utc(value)


def epoch_seconds(value: str) -> float:
    return _utc(value).timestamp()


def probability(value: float, *, name: str = "probability") -> float:
    """Strict probability for scoring/logit transforms."""
    p = _prob(value, name=name)
    if not 0.0 < p < 1.0:
        raise ChallengerContractError(f"{name} must be strictly between 0 and 1")
    return p


def binary_label(value: int) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        numeric = float(value)
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ChallengerContractError("label must be 0 or 1") from exc
    if not math.isfinite(numeric) or numeric != result or result not in {0, 1}:
        raise ChallengerContractError("label must be 0 or 1")
    return result


def logit(value: float) -> float:
    p = min(1.0 - EPS, max(EPS, probability(value)))
    return math.log(p / (1.0 - p))


def sigmoid(value: float) -> float:
    z = max(-40.0, min(40.0, float(value)))
    return 1.0 / (1.0 + math.exp(-z))


def validate_observation(row: Observation) -> None:
    if not row.market_id:
        raise ChallengerContractError("market_id is required")
    decision = _utc(row.decision_ts)
    label_end = _utc(row.label_end_ts)
    if label_end <= decision:
        raise ChallengerContractError("label_end_ts must be after decision_ts")
    if row.label not in {0, 1}:
        raise ChallengerContractError("label must be 0 or 1")
    _prob(row.p_market, name="p_market")
    if row.senex_raw_up is not None:
        _prob(row.senex_raw_up, name="senex_raw_up")


def purged_walk_forward_splits(
    rows: Sequence[Observation],
    *,
    n_splits: int,
    min_train_size: int,
    embargo_seconds: int = 0,
) -> list[PurgedSplit]:
    """Conservative expanding-window CV with label-overlap purge and embargo.

    Training uses past rows only. A candidate training row is retained only when
    its label window ends strictly before test_start - embargo.
    """
    if isinstance(n_splits, bool) or not isinstance(n_splits, int) or n_splits < 1:
        raise ChallengerContractError("n_splits must be a positive integer")
    if (
        isinstance(min_train_size, bool)
        or not isinstance(min_train_size, int)
        or min_train_size < 1
    ):
        raise ChallengerContractError("min_train_size must be a positive integer")
    if (
        isinstance(embargo_seconds, bool)
        or not isinstance(embargo_seconds, int)
        or embargo_seconds < 0
    ):
        raise ChallengerContractError("embargo_seconds must be a non-negative integer")
    if len(rows) <= min_train_size:
        raise ChallengerContractError("not enough rows after minimum training window")

    for row in rows:
        validate_observation(row)

    ordered = sorted(
        range(len(rows)),
        key=lambda idx: (_utc(rows[idx].decision_ts), rows[idx].market_id, idx),
    )
    first_test_position: int | None = None
    for position in range(min_train_size, len(ordered)):
        test_start = _utc(rows[ordered[position]].decision_ts)
        purge_before = test_start.timestamp() - embargo_seconds
        eligible_train = [
            idx
            for idx in ordered[:position]
            if _utc(rows[idx].label_end_ts).timestamp() < purge_before
        ]
        if len(eligible_train) >= min_train_size:
            first_test_position = position
            break

    if first_test_position is None:
        raise ChallengerContractError(
            "not enough post-purge training rows for requested min_train_size"
        )

    remaining = len(ordered) - first_test_position
    actual_splits = min(n_splits, remaining)
    base = remaining // actual_splits
    extra = remaining % actual_splits
    cursor = first_test_position
    splits: list[PurgedSplit] = []

    for fold in range(actual_splits):
        size = base + (1 if fold < extra else 0)
        end = cursor + size
        test_indices = tuple(ordered[cursor:end])
        if not test_indices:
            continue
        test_start = min(_utc(rows[idx].decision_ts) for idx in test_indices)
        purge_before = test_start.timestamp() - embargo_seconds
        train_indices = tuple(
            idx
            for idx in ordered[:cursor]
            if _utc(rows[idx].label_end_ts).timestamp() < purge_before
        )
        if len(train_indices) < min_train_size:
            raise ChallengerContractError(
                "purge/embargo violated post-purge min_train_size"
            )
        splits.append(
            PurgedSplit(
                train_indices=train_indices,
                test_indices=test_indices,
                test_start_ts=test_start.isoformat().replace("+00:00", "Z"),
                embargo_seconds=embargo_seconds,
            )
        )
        cursor = end

    return splits


def validate_purged_splits(
    rows: Sequence[Observation],
    splits: Sequence[PurgedSplit],
) -> None:
    """Fail closed unless supplied folds satisfy the frozen temporal contract."""
    if not splits:
        raise ChallengerContractError("at least one purged split is required")

    for row in rows:
        validate_observation(row)

    seen_test_indices: set[int] = set()
    n_rows = len(rows)
    for split in splits:
        if (
            isinstance(split.embargo_seconds, bool)
            or not isinstance(split.embargo_seconds, int)
            or split.embargo_seconds < 0
        ):
            raise ChallengerContractError("purged split embargo must be non-negative")
        if not split.train_indices or not split.test_indices:
            raise ChallengerContractError("purged split train/test indices are required")
        if len(set(split.train_indices)) != len(split.train_indices):
            raise ChallengerContractError("purged split train indices must be unique")
        if len(set(split.test_indices)) != len(split.test_indices):
            raise ChallengerContractError("purged split test indices must be unique")
        if set(split.train_indices) & set(split.test_indices):
            raise ChallengerContractError("purged split train/test overlap")
        if any(index < 0 or index >= n_rows for index in [*split.train_indices, *split.test_indices]):
            raise ChallengerContractError("purged split index out of range")
        if seen_test_indices.intersection(split.test_indices):
            raise ChallengerContractError("purged split test rows cannot repeat across folds")
        seen_test_indices.update(split.test_indices)

        declared_start = _utc(split.test_start_ts)
        observed_start = min(_utc(rows[index].decision_ts) for index in split.test_indices)
        if declared_start != observed_start:
            raise ChallengerContractError("purged split test_start_ts mismatch")
        purge_before = declared_start.timestamp() - split.embargo_seconds
        for index in split.train_indices:
            decision = _utc(rows[index].decision_ts)
            label_end = _utc(rows[index].label_end_ts)
            if decision >= declared_start:
                raise ChallengerContractError("purged split training row is not strictly historical")
            if label_end.timestamp() >= purge_before:
                raise ChallengerContractError("purged split violates label purge/embargo")
        if any(_utc(rows[index].decision_ts) < declared_start for index in split.test_indices):
            raise ChallengerContractError("purged split test row precedes declared start")


def brier_score(labels: Sequence[int], probabilities: Sequence[float]) -> float:
    if len(labels) != len(probabilities) or not labels:
        raise ChallengerContractError("labels/probabilities length mismatch or empty")
    total = 0.0
    for y, raw in zip(labels, probabilities):
        if y not in {0, 1}:
            raise ChallengerContractError("label must be 0 or 1")
        p = _prob(raw, name="probability")
        total += (p - y) ** 2
    return total / len(labels)


def log_loss(labels: Sequence[int], probabilities: Sequence[float]) -> float:
    if len(labels) != len(probabilities) or not labels:
        raise ChallengerContractError("labels/probabilities length mismatch or empty")
    total = 0.0
    for y, raw in zip(labels, probabilities):
        if y not in {0, 1}:
            raise ChallengerContractError("label must be 0 or 1")
        p = min(1.0 - EPS, max(EPS, _prob(raw, name="probability")))
        total -= y * math.log(p) + (1 - y) * math.log(1.0 - p)
    return total / len(labels)


def calibration_report(
    labels: Sequence[int],
    probabilities: Sequence[float],
    *,
    n_bins: int = 10,
) -> dict[str, Any]:
    """Deterministic fixed-bin reliability diagnostic; no fitting/tuning."""
    if len(labels) != len(probabilities) or not labels:
        raise ChallengerContractError(
            "calibration labels/probabilities length mismatch or empty"
        )
    if isinstance(n_bins, bool) or not isinstance(n_bins, int) or n_bins < 2:
        raise ChallengerContractError("n_bins must be an integer >=2")

    bins: list[list[tuple[int, float]]] = [[] for _ in range(n_bins)]
    ys: list[int] = []
    ps: list[float] = []
    for label, raw in zip(labels, probabilities):
        y = binary_label(label)
        p = _prob(raw, name="probability")
        index = min(n_bins - 1, int(p * n_bins))
        bins[index].append((y, p))
        ys.append(y)
        ps.append(p)

    details: list[dict[str, float | int]] = []
    ece = 0.0
    max_abs_gap = 0.0
    for index, bucket in enumerate(bins):
        if not bucket:
            continue
        observed = sum(y for y, _ in bucket) / len(bucket)
        mean_probability = sum(p for _, p in bucket) / len(bucket)
        gap = mean_probability - observed
        ece += (len(bucket) / len(labels)) * abs(gap)
        max_abs_gap = max(max_abs_gap, abs(gap))
        details.append({
            "bin_index": index,
            "n": len(bucket),
            "mean_probability": mean_probability,
            "observed_rate": observed,
            "probability_minus_observed": gap,
        })

    mean_probability = sum(ps) / len(ps)
    observed_rate = sum(ys) / len(ys)
    return {
        "n": len(labels),
        "n_bins": n_bins,
        "mean_probability": mean_probability,
        "observed_rate": observed_rate,
        "mean_probability_minus_observed": mean_probability - observed_rate,
        "ece": ece,
        "max_abs_bin_gap": max_abs_gap,
        "bins": details,
    }


def roc_auc(
    labels: Sequence[int],
    probabilities: Sequence[float],
) -> float | None:
    """Deterministic rank AUC with average ranks for ties.

    AUC is diagnostic only in Challenger Lab V1. It is deliberately not used
    for candidate selection because discrimination and calibration answer
    different questions. Single-class slices return None rather than creating
    a synthetic value.
    """
    if len(labels) != len(probabilities) or not labels:
        raise ChallengerContractError("AUC inputs must be non-empty and aligned")

    pairs = [
        (binary_label(label), _prob(probability, name="probability"))
        for label, probability in zip(labels, probabilities)
    ]
    positives = sum(label for label, _ in pairs)
    negatives = len(pairs) - positives
    if positives == 0 or negatives == 0:
        return None

    ordered = sorted(enumerate(pairs), key=lambda item: (item[1][1], item[0]))
    ranks = [0.0] * len(ordered)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        probability = ordered[cursor][1][1]
        while end < len(ordered) and ordered[end][1][1] == probability:
            end += 1
        average_rank = ((cursor + 1) + end) / 2.0
        for index in range(cursor, end):
            original_index = ordered[index][0]
            ranks[original_index] = average_rank
        cursor = end

    positive_rank_sum = sum(
        rank for rank, (label, _) in zip(ranks, pairs) if label == 1
    )
    return (
        positive_rank_sum - positives * (positives + 1) / 2.0
    ) / (positives * negatives)


def proper_score_report(
    labels: Sequence[int],
    p_market: Sequence[float],
    p_candidate: Sequence[float],
) -> dict[str, Any]:
    if not (len(labels) == len(p_market) == len(p_candidate)) or not labels:
        raise ChallengerContractError("score inputs must be non-empty and aligned")
    market_brier = brier_score(labels, p_market)
    candidate_brier = brier_score(labels, p_candidate)
    market_log = log_loss(labels, p_market)
    candidate_log = log_loss(labels, p_candidate)
    return {
        "n": len(labels),
        "market_brier": market_brier,
        "candidate_brier": candidate_brier,
        "delta_brier": candidate_brier - market_brier,
        "market_log_loss": market_log,
        "candidate_log_loss": candidate_log,
        "delta_log_loss": candidate_log - market_log,
        "market_roc_auc": roc_auc(labels, p_market),
        "candidate_roc_auc": roc_auc(labels, p_candidate),
        "market_calibration": calibration_report(labels, p_market),
        "candidate_calibration": calibration_report(labels, p_candidate),
    }


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChallengerContractError(f"invalid manifest: {path}") from exc
    if not isinstance(value, dict):
        raise ChallengerContractError("manifest must be an object")
    if value.get("zero_spend") is not True:
        raise ChallengerContractError("challenger manifest must enforce zero_spend=true")
    if "prospective_t_star" not in value or "prospective_n" not in value:
        raise ChallengerContractError("manifest must declare prospective_t_star and prospective_n")
    return value


def assert_historical_synthetic_only(manifest: dict[str, Any]) -> None:
    if manifest.get("prospective_t_star") is not None:
        raise ChallengerContractError("historical runner refuses a set prospective T*")
    if manifest.get("prospective_n") is not None:
        raise ChallengerContractError("historical runner refuses a set prospective N")


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return sha256_json(manifest)


def seal_prospective_receipt(
    *,
    challenger_id: str,
    challenger_version: str,
    source_commit: str,
    manifest_sha256_value: str,
    market_id: str,
    cutoff_ts: str,
    outcome_not_before_ts: str,
    created_at: str,
    p_market: float,
    candidate_probability: float,
    input_features: dict[str, Any],
    model_sha256: str,
    output_sha256: str,
) -> dict[str, Any]:
    if not challenger_id or not challenger_version or not source_commit or not market_id:
        raise ChallengerContractError("receipt identity fields are required")
    if len(source_commit) != 40:
        raise ChallengerContractError("source_commit must be an exact 40-hex commit")
    try:
        int(source_commit, 16)
    except ValueError as exc:
        raise ChallengerContractError("source_commit must be an exact 40-hex commit") from exc
    if len(manifest_sha256_value) != 64:
        raise ChallengerContractError("manifest SHA256 must be 64 hex characters")
    try:
        int(manifest_sha256_value, 16)
    except ValueError as exc:
        raise ChallengerContractError("manifest SHA256 is invalid") from exc
    if len(model_sha256) != 64:
        raise ChallengerContractError("model SHA256 must be 64 hex characters")
    try:
        int(model_sha256, 16)
    except ValueError as exc:
        raise ChallengerContractError("model SHA256 is invalid") from exc
    if len(output_sha256) != 64:
        raise ChallengerContractError("output SHA256 must be 64 hex characters")
    try:
        int(output_sha256, 16)
    except ValueError as exc:
        raise ChallengerContractError("output SHA256 is invalid") from exc

    cutoff = _utc(cutoff_ts)
    created = _utc(created_at)
    outcome_not_before = _utc(outcome_not_before_ts)
    if created < cutoff:
        raise ChallengerContractError("receipt cannot predate cutoff")
    if created >= outcome_not_before:
        raise ChallengerContractError("receipt must be sealed before outcome eligibility")

    receipt = {
        "contract": RECEIPT_CONTRACT,
        "challenger_id": challenger_id,
        "challenger_version": challenger_version,
        "source_commit": source_commit,
        "manifest_sha256": manifest_sha256_value,
        "market_id": market_id,
        "cutoff_ts": cutoff.isoformat().replace("+00:00", "Z"),
        "outcome_not_before_ts": outcome_not_before.isoformat().replace("+00:00", "Z"),
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "p_market": _prob(p_market, name="p_market"),
        "candidate_probability": _prob(
            candidate_probability, name="candidate_probability"
        ),
        "input_features_sha256": sha256_json(input_features),
        "model_sha256": model_sha256.lower(),
        "output_sha256": output_sha256.lower(),
        "paper_only": True,
        "live": False,
        "real_orders": 0,
        "capital": 0,
    }
    receipt["receipt_sha256"] = sha256_json(receipt)
    return receipt


def verify_prospective_receipt(receipt: dict[str, Any]) -> bool:
    if not isinstance(receipt, dict) or receipt.get("contract") != RECEIPT_CONTRACT:
        return False

    required_text = (
        "challenger_id",
        "challenger_version",
        "source_commit",
        "manifest_sha256",
        "market_id",
        "cutoff_ts",
        "outcome_not_before_ts",
        "created_at",
        "input_features_sha256",
        "model_sha256",
        "output_sha256",
        "receipt_sha256",
    )
    if any(not str(receipt.get(key) or "").strip() for key in required_text):
        return False

    def _is_hex(value: Any, length: int) -> bool:
        raw = str(value or "").strip().lower()
        if len(raw) != length:
            return False
        try:
            int(raw, 16)
        except ValueError:
            return False
        return True

    if not _is_hex(receipt.get("source_commit"), 40):
        return False
    for key in (
        "manifest_sha256",
        "input_features_sha256",
        "model_sha256",
        "output_sha256",
        "receipt_sha256",
    ):
        if not _is_hex(receipt.get(key), 64):
            return False

    try:
        cutoff = _utc(str(receipt["cutoff_ts"]))
        created = _utc(str(receipt["created_at"]))
        outcome_not_before = _utc(str(receipt["outcome_not_before_ts"]))
        _prob(receipt["p_market"], name="p_market")
        _prob(receipt["candidate_probability"], name="candidate_probability")
    except (ChallengerContractError, KeyError, TypeError, ValueError):
        return False

    if created < cutoff or created >= outcome_not_before:
        return False
    if receipt.get("paper_only") is not True or receipt.get("live") is not False:
        return False
    if type(receipt.get("real_orders")) is not int or receipt.get("real_orders") != 0:
        return False
    if type(receipt.get("capital")) not in {int, float} or receipt.get("capital") != 0:
        return False

    observed = str(receipt["receipt_sha256"]).lower()
    payload = dict(receipt)
    payload.pop("receipt_sha256", None)
    try:
        return sha256_json(payload) == observed
    except (TypeError, ValueError):
        return False


def append_prospective_receipt(
    path: str | Path,
    receipt: dict[str, Any],
) -> bool:
    """Append one sealed receipt durably; exact retries are idempotent."""
    if not verify_prospective_receipt(receipt):
        raise ChallengerContractError("prospective receipt is invalid")

    target = Path(path)
    created_new = not target.exists()
    key = (
        receipt["challenger_id"],
        receipt["challenger_version"],
        receipt["market_id"],
        receipt["cutoff_ts"],
    )
    if target.exists():
        for line_number, raw in enumerate(
            target.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not raw.strip():
                continue
            try:
                existing = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ChallengerContractError(
                    f"invalid receipt ledger JSON at line {line_number}"
                ) from exc
            if not verify_prospective_receipt(existing):
                raise ChallengerContractError(
                    f"invalid receipt ledger entry at line {line_number}"
                )
            existing_key = (
                existing["challenger_id"],
                existing["challenger_version"],
                existing["market_id"],
                existing["cutoff_ts"],
            )
            if existing_key == key:
                if existing == receipt:
                    return False
                raise ChallengerContractError(
                    "prospective receipt identity conflict"
                )

    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(canonical_json(receipt) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if created_new and POSIX_DIR_FSYNC:
        directory_fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return True
