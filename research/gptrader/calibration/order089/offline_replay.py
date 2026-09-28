#!/usr/bin/env python3
"""ORDER089 offline, read-only replay evaluator.

Consumes pre-frozen arm decisions and settlements. It never calls a model,
runtime, exchange, broker, wallet, D1, H011, MCP, or current-market source.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARMS = ("CONTROL", "DECISION_AGENT", "CALIBRATED_AGENT")
ACTIONS = {"TAKE", "ABSTAIN"}
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
