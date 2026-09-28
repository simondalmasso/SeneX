#!/usr/bin/env python3
"""ORDER089 offline, read-only replay evaluator.

Consumes pre-frozen arm decisions and settlements. It never calls a model,
runtime, exchange, broker, wallet, D1, H011, MCP, or current-market source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARMS = ("CONTROL", "DECISION_AGENT", "CALIBRATED_AGENT")
ACTIONS = {"TAKE", "ABSTAIN"}
V1_CANDIDATES = (
    "BASELINE",
    "CONF_Q60",
    "CONF_Q70",
    "CONF_Q80",
    "EV_Q60",
    "EV_Q70",
    "EV_Q80",
    "CONF_Q70_AND_EV_Q70",
)
CALIBRATION_CORE_KEYS = (
    "schema_version",
    "decision_protocol_version",
    "source_epochs",
    "windows",
    "parameters",
    "frozen",
    "objective",
    "constraints",
    "code_sha",
    "random_seed",
)
INCIDENT_IDS = {"6868", "6870", "6872"}
INCIDENT_START = datetime(2026, 9, 27, 21, 29, 53, tzinfo=timezone.utc)
INCIDENT_END = datetime(2026, 9, 27, 22, 10, 23, tzinfo=timezone.utc)


class ReplayError(ValueError):
    pass


def _dt(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ReplayError("timestamp must be a non-empty ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ReplayError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ReplayError(f"{path.name}:{line_no}: JSON row must be an object")
        rows.append(value)
    return rows


def _quarantined(packet: dict[str, Any]) -> bool:
    for key in ("id", "prediction_id", "source_prediction_id"):
        value = packet.get(key)
        if value is not None and str(value) in INCIDENT_IDS:
            return True
    ts = _dt(packet.get("timestamp"))
    return INCIDENT_START <= ts < INCIDENT_END


def _cluster_id(packet: dict[str, Any]) -> str:
    dt = _dt(packet.get("timestamp")).replace(minute=0, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:00:00Z")


def resolve_clean_baseline(
    packets: list[dict[str, Any]],
    *,
    expected_source_sha: str,
) -> str | None:
    candidates = sorted(
        (
            packet
            for packet in packets
            if _dt(packet.get("timestamp")) >= INCIDENT_END and not _quarantined(packet)
        ),
        key=lambda packet: _dt(packet.get("timestamp")),
    )
    if not candidates:
        return None
    first = candidates[0]
    if first.get("provenance_exact") is not True:
        return None
    if str(first.get("source_sha") or "") != str(expected_source_sha):
        return None
    if not str(first.get("packet_hash") or ""):
        return None
    return _dt(first["timestamp"]).isoformat().replace("+00:00", "Z")


def validate_epoch_partition(
    train: list[dict[str, Any]],
    calibration: list[dict[str, Any]],
    holdout: list[dict[str, Any]],
) -> None:
    epochs = {
        "TRAIN": train,
        "CALIBRATION": calibration,
        "HOLDOUT": holdout,
    }
    packet_ids: dict[str, set[str]] = {}
    cluster_ids: dict[str, set[str]] = {}
    bounds: dict[str, tuple[datetime, datetime] | None] = {}
    for name, rows in epochs.items():
        ids: set[str] = set()
        clusters: set[str] = set()
        times: list[datetime] = []
        for row in rows:
            packet_id = str(row.get("packet_id") or "")
            if not packet_id:
                raise ReplayError(f"{name} packet is missing packet_id")
            ids.add(packet_id)
            clusters.add(_cluster_id(row))
            times.append(_dt(row.get("timestamp")))
        packet_ids[name] = ids
        cluster_ids[name] = clusters
        bounds[name] = (min(times), max(times)) if times else None

    ordered = ("TRAIN", "CALIBRATION", "HOLDOUT")
    for left_index, left in enumerate(ordered):
        for right in ordered[left_index + 1 :]:
            if packet_ids[left] & packet_ids[right]:
                raise ReplayError(f"epoch packet overlap: {left}/{right}")
            if cluster_ids[left] & cluster_ids[right]:
                raise ReplayError(f"epoch cluster overlap: {left}/{right}")

    prior_end: datetime | None = None
    for name in ordered:
        current = bounds[name]
        if current is None:
            continue
        start, end = current
        if prior_end is not None and start <= prior_end:
            raise ReplayError(f"epoch chronology violation at {name}")
        prior_end = end


def compute_calibration_id(artifact: dict[str, Any]) -> str:
    core = {key: artifact.get(key) for key in CALIBRATION_CORE_KEYS}
    encoded = json.dumps(
        core,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "cal089_" + hashlib.sha256(encoded).hexdigest()


def coverage_guard_passes(
    *,
    candidate_take_count: int,
    uncalibrated_take_count: int,
) -> bool:
    if candidate_take_count < 0 or uncalibrated_take_count <= 0:
        return False
    return candidate_take_count * 2 >= uncalibrated_take_count


def replacement_shadow_state(
    *,
    calibration_id: str,
    decision_protocol_version: str,
    new_provenance: dict[str, Any],
) -> dict[str, Any]:
    if not calibration_id or not decision_protocol_version:
        raise ReplayError("replacement requires fixed artifact and protocol")
    agent_id = str(new_provenance.get("agent_id") or "")
    if not agent_id:
        raise ReplayError("replacement provenance requires agent_id")
    return {
        "calibration_id": calibration_id,
        "decision_protocol_version": decision_protocol_version,
        "stage": "SHADOW_PAPER",
        "evaluation_provenance": dict(new_provenance),
        "independent_1h_clusters": 0,
        "performance_evidence": [],
    }


def evidence_gate_passes(
    stage: str,
    *,
    independent_1h_clusters: int,
    calendar_days: int,
) -> bool:
    thresholds = {
        "TRAIN": (168, 7),
        "CALIBRATION": (168, 7),
        "HOLDOUT": (600, 25),
    }
    if stage not in thresholds:
        raise ReplayError(f"unknown evidence stage: {stage}")
    if independent_1h_clusters < 0 or calendar_days <= 0:
        return False
    if independent_1h_clusters > calendar_days * 24:
        return False
    min_clusters, min_days = thresholds[stage]
    return (
        independent_1h_clusters >= min_clusters
        and calendar_days >= min_days
    )


def evaluate(
    packets: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    settlements: list[dict[str, Any]],
) -> dict[str, Any]:
    packet_map: dict[str, dict[str, Any]] = {}
    excluded: set[str] = set()
    for packet in packets:
        packet_id = str(packet.get("packet_id") or "")
        packet_hash = str(packet.get("packet_hash") or "")
        if not packet_id or not packet_hash:
            raise ReplayError("packet requires packet_id and packet_hash")
        if packet_id in packet_map:
            raise ReplayError(f"duplicate packet_id: {packet_id}")
        packet_map[packet_id] = packet
        if _quarantined(packet):
            excluded.add(packet_id)

    decision_map: dict[tuple[str, str], dict[str, Any]] = {}
    for row in decisions:
        packet_id = str(row.get("packet_id") or "")
        arm = str(row.get("arm") or "")
        action = str(row.get("action") or "")
        packet_hash = str(row.get("packet_hash") or "")
        if packet_id not in packet_map:
            raise ReplayError(f"decision references unknown packet: {packet_id}")
        if arm not in ARMS:
            raise ReplayError(f"invalid arm: {arm}")
        if action not in ACTIONS:
            raise ReplayError(f"invalid action: {action}")
        if packet_hash != str(packet_map[packet_id].get("packet_hash")):
            raise ReplayError(f"packet hash mismatch for {packet_id}")
        key = (packet_id, arm)
        if key in decision_map:
            raise ReplayError(f"duplicate frozen decision: {packet_id}/{arm}")
        _dt(row.get("decision_frozen_at"))
        decision_map[key] = row

    settlement_map: dict[str, dict[str, Any]] = {}
    for row in settlements:
        packet_id = str(row.get("packet_id") or "")
        if packet_id not in packet_map:
            raise ReplayError(f"settlement references unknown packet: {packet_id}")
        if packet_id in settlement_map:
            raise ReplayError(f"duplicate settlement: {packet_id}")
        if not isinstance(row.get("senex_direction_correct"), bool):
            raise ReplayError("settlement requires boolean senex_direction_correct")
        settled_at = _dt(row.get("settled_at"))
        for arm in ARMS:
            decision = decision_map.get((packet_id, arm))
            if decision is None:
                raise ReplayError(f"settlement before all arm decisions: {packet_id}")
            frozen_at = _dt(decision.get("decision_frozen_at"))
            if frozen_at >= settled_at:
                raise ReplayError(f"decision not frozen before settlement: {packet_id}/{arm}")
        settlement_map[packet_id] = row

    arm_cluster_values: dict[str, dict[str, list[float]]] = {
        arm: defaultdict(list) for arm in ARMS
    }
    arm_takes = {arm: 0 for arm in ARMS}
    arm_correct_takes = {arm: 0 for arm in ARMS}
    eligible_packets = 0

    for packet_id, settlement in settlement_map.items():
        if packet_id in excluded:
            continue
        packet = packet_map[packet_id]
        eligible_packets += 1
        cluster = _cluster_id(packet)
        correct = bool(settlement["senex_direction_correct"])
        for arm in ARMS:
            action = decision_map[(packet_id, arm)]["action"]
            value = 0.0
            if action == "TAKE":
                arm_takes[arm] += 1
                if correct:
                    arm_correct_takes[arm] += 1
                value = 1.0 if correct else -1.0
            arm_cluster_values[arm][cluster].append(value)

    summaries: dict[str, Any] = {}
    all_clusters = sorted(
        {
            cluster
            for per_arm in arm_cluster_values.values()
            for cluster in per_arm
        }
    )
    for arm in ARMS:
        per_cluster = {
            cluster: sum(values) / len(values)
            for cluster, values in arm_cluster_values[arm].items()
        }
        cluster_values = [per_cluster.get(cluster, 0.0) for cluster in all_clusters]
        takes = arm_takes[arm]
        summaries[arm] = {
            "take_count": takes,
            "correct_take_count": arm_correct_takes[arm],
            "take_accuracy": None if takes == 0 else arm_correct_takes[arm] / takes,
            "independent_1h_clusters": len(all_clusters),
            "mean_cluster_utility": (
                None if not cluster_values else sum(cluster_values) / len(cluster_values)
            ),
        }

    return {
        "schema_version": "senex.order089.offline_replay.v1",
        "eligible_packets": eligible_packets,
        "quarantined_packets_excluded": len(excluded & set(settlement_map)),
        "independent_1h_clusters": len(all_clusters),
        "arms": summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packets", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--settlements", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate(
        _read_jsonl(args.packets),
        _read_jsonl(args.decisions),
        _read_jsonl(args.settlements),
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
