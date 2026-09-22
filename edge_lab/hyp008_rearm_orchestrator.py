from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from edge_lab.arq3_execution_contract_v1 import (
    build_jev_input_at_decision,
    validate_execution_record,
)
from edge_lab.hyp008_future_writer import (
    FutureRowRejected,
    build_future_row,
    opportunity_id,
)

CONTRACT_ID = "HYP008_REARM_ORCH_V1"
GATE_ORDER = ("H-15", "H-10", "H-05", "H+00", "H+03", "H+05")
IDENTITY_FIELDS = (
    "event_id",
    "market_id",
    "condition_id",
    "market_slug",
    "eventStartTime",
    "up_token_id",
    "down_token_id",
)
EXPECTED_OWNERS = {
    "H-15": "ARQ2",
    "H-10": "ARQ3",
    "H-05": "ARQ3",
    "H+00": "ARQ3",
    "H+03": "ARQ3",
    "H+05": "ARQ2",
}
MAX_CAPTURE_LATENESS_MS = 2000


class OrchestrationRejected(ValueError):
    """Fixture-only orchestration failure; no cohort side effect is permitted."""


def _utc(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _tokens(value: Any) -> list[str]:
    if isinstance(value, list):
        tokens = [str(item) for item in value]
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise OrchestrationRejected("MARKET_TOKEN_IDS_INVALID") from exc
        if not isinstance(parsed, list):
            raise OrchestrationRejected("MARKET_TOKEN_IDS_INVALID")
        tokens = [str(item) for item in parsed]
    else:
        raise OrchestrationRejected("MARKET_TOKEN_IDS_INVALID")
    if len(tokens) != 2:
        raise OrchestrationRejected("MARKET_TOKEN_IDS_INVALID")
    return tokens


def market_identity(market: dict[str, Any]) -> dict[str, str]:
    tokens = _tokens(market.get("clobTokenIds"))
    identity = {
        "event_id": str(market.get("event_id") or ""),
        "market_id": str(market.get("market_id") or ""),
        "condition_id": str(market.get("conditionId") or ""),
        "market_slug": str(market.get("slug") or ""),
        "eventStartTime": str(market.get("eventStartTime") or ""),
        "up_token_id": tokens[0],
        "down_token_id": tokens[1],
    }
    if any(not identity[field] for field in IDENTITY_FIELDS):
        raise OrchestrationRejected("MARKET_IDENTITY_INCOMPLETE")
    return identity


def normalize_h011_source_packet(packet: dict[str, Any], *, boundary: str) -> dict[str, Any]:
    """Select one bounded H011 source row and apply only frozen HYP-008 flags.

    Runtime provenance is intentionally not compared with the ARQ2 repository SHA.
    """
    if not isinstance(packet, dict):
        raise OrchestrationRejected("H011_PACKET_INVALID")
    if packet.get("source") != "authority_snapshot_cache":
        raise OrchestrationRejected("H011_SOURCE_INVALID")
    if packet.get("bounded") is not True:
        raise OrchestrationRejected("H011_PACKET_NOT_BOUNDED")
    if str(packet.get("symbol") or "").upper() != "BTCUSDT":
        raise OrchestrationRejected("H011_SYMBOL_INVALID")
    rows = packet.get("predictions")
    if not isinstance(rows, list):
        raise OrchestrationRejected("H011_PREDICTIONS_INVALID")

    required = (
        "id",
        "ts",
        "created_at",
        "symbol",
        "prediction",
        "confidence",
        "ev",
        "price_now",
        "exchange_used",
    )
    boundary_dt = _utc(boundary)
    eligible: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or any(row.get(field) is None for field in required):
            continue
        if str(row.get("symbol") or "").upper() != "BTCUSDT":
            continue
        try:
            if _utc(str(row["ts"])) > boundary_dt:
                continue
            if _utc(str(row["created_at"])) > boundary_dt:
                continue
        except (TypeError, ValueError):
            continue
        eligible.append(row)
    if not eligible:
        raise OrchestrationRejected("H011_SOURCE_NOT_AVAILABLE_ASOF_BOUNDARY")

    selected = max(
        eligible,
        key=lambda row: (_utc(str(row["ts"])), str(row["id"])),
    )
    normalized = deepcopy(selected)
    normalized["polymarket_directional_use"] = False
    normalized["polymarket_effective_weight"] = 0
    return normalized


def _assert_identity(actual: Any, expected: dict[str, str]) -> None:
    if not isinstance(actual, dict):
        raise OrchestrationRejected("IDENTITY_MISMATCH")
    projected = {field: str(actual.get(field) or "") for field in IDENTITY_FIELDS}
    if projected != expected:
        raise OrchestrationRejected("IDENTITY_MISMATCH")


def _deadline(gate: str, boundary: datetime) -> datetime:
    return {
        "H-15": boundary - timedelta(minutes=15),
        "H-10": boundary - timedelta(minutes=10),
        "H-05": boundary - timedelta(minutes=5),
        "H+00": boundary + timedelta(milliseconds=MAX_CAPTURE_LATENESS_MS),
        "H+03": boundary + timedelta(minutes=3),
        "H+05": boundary + timedelta(minutes=5),
    }[gate]


def _validate_event_time(
    gate: str,
    *,
    at: datetime,
    boundary: datetime,
    previous_at: datetime | None,
) -> None:
    if previous_at is not None and at < previous_at:
        raise OrchestrationRejected("EVENT_TIME_ORDER_INVALID")
    if gate == "H-15" and at > _deadline(gate, boundary):
        raise OrchestrationRejected("HANDOFF_LATE")
    if gate == "H-10" and at > _deadline(gate, boundary):
        raise OrchestrationRejected("ACK_LATE")
    if gate == "H-05" and at > _deadline(gate, boundary):
        raise OrchestrationRejected("ARM_HEARTBEAT_LATE")
    if gate == "H+00":
        if at < boundary or at > _deadline(gate, boundary):
            raise OrchestrationRejected("CAPTURE_LATE")
    if gate == "H+03":
        if at < boundary or at > _deadline(gate, boundary):
            raise OrchestrationRejected("EVIDENCE_VALIDATION_LATE")
    if gate == "H+05":
        if at < boundary or at > _deadline(gate, boundary):
            raise OrchestrationRejected("C2_VALIDATION_LATE")


def _validate_arm(fixture: dict[str, Any], *, boundary: datetime, identity: dict[str, str]) -> None:
    arm = fixture.get("arq3_arm")
    if not isinstance(arm, dict) or arm.get("ready_before_boundary") is not True:
        raise OrchestrationRejected("ARQ3_ARM_INVALID")
    if str(arm.get("condition_id") or "") != identity["condition_id"]:
        raise OrchestrationRejected("ARQ3_ARM_IDENTITY_MISMATCH")
    if str(arm.get("eventStartTime") or "") != identity["eventStartTime"]:
        raise OrchestrationRejected("ARQ3_ARM_IDENTITY_MISMATCH")
    armed_at = arm.get("armed_at")
    if not armed_at or _utc(str(armed_at)) > boundary - timedelta(minutes=5):
        raise OrchestrationRejected("ARM_HEARTBEAT_LATE")


def _validate_evidence(fixture: dict[str, Any]) -> None:
    execution = fixture.get("execution_evidence")
    if execution is None:
        raise OrchestrationRejected("EXECUTION_EVIDENCE_MISSING")
    if not isinstance(execution, dict):
        raise OrchestrationRejected("EXECUTION_EVIDENCE_INVALID")
    errors = validate_execution_record(execution)
    if errors:
        raise OrchestrationRejected("EXECUTION_EVIDENCE_INVALID:" + ",".join(errors))
    jev = fixture.get("jev_input_at_decision")
    if jev is None:
        raise OrchestrationRejected("JEV_INPUT_MISSING")
    if not isinstance(jev, dict):
        raise OrchestrationRejected("JEV_INPUT_INVALID")
    expected = build_jev_input_at_decision(execution)
    if _canonical(jev) != _canonical(expected):
        raise OrchestrationRejected("JEV_INPUT_EXACT_MISMATCH")


def run_fixture_orchestration(
    fixture: dict[str, Any],
    *,
    protocol_path: Path,
    prior_opportunity_ids: set[str] | frozenset[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run the frozen re-arm choreography against synthetic/frozen evidence only.

    This function never calls append_future_row and never performs network I/O.
    """
    if not isinstance(fixture, dict):
        raise OrchestrationRejected("FIXTURE_INVALID")
    market = fixture.get("market")
    if not isinstance(market, dict):
        raise OrchestrationRejected("MARKET_MISSING")
    identity = market_identity(market)
    boundary = _utc(identity["eventStartTime"])
    if _utc(str(fixture.get("boundary") or "")) != boundary:
        raise OrchestrationRejected("BOUNDARY_MISMATCH")
    opp_id = opportunity_id(identity["condition_id"], identity["eventStartTime"])
    if opp_id in set(prior_opportunity_ids):
        raise OrchestrationRejected("DUPLICATE_OPPORTUNITY")

    events = fixture.get("events")
    if not isinstance(events, list):
        raise OrchestrationRejected("EVENTS_INVALID")
    accepted: dict[str, dict[str, Any]] = {}
    next_index = 0
    previous_at: datetime | None = None
    idempotent_replays = 0

    for event in events:
        if not isinstance(event, dict):
            raise OrchestrationRejected("EVENT_INVALID")
        gate = str(event.get("gate") or "")
        if gate not in GATE_ORDER:
            raise OrchestrationRejected("GATE_UNKNOWN")
        if gate in accepted:
            if _canonical(event) != _canonical(accepted[gate]):
                raise OrchestrationRejected("RETRY_CONFLICT")
            idempotent_replays += 1
            continue
        expected_gate = GATE_ORDER[next_index] if next_index < len(GATE_ORDER) else None
        if gate != expected_gate:
            raise OrchestrationRejected(f"MISSING_GATE:{expected_gate}")
        if str(event.get("owner") or "") != EXPECTED_OWNERS[gate]:
            raise OrchestrationRejected("OWNER_MISMATCH")
        _assert_identity(event.get("identity"), identity)
        at = _utc(str(event.get("at") or ""))
        _validate_event_time(gate, at=at, boundary=boundary, previous_at=previous_at)

        if gate == "H-05":
            if event.get("heartbeat") is not True:
                raise OrchestrationRejected("ARM_HEARTBEAT_MISSING")
            _validate_arm(fixture, boundary=boundary, identity=identity)
        if gate == "H+00":
            timing = fixture.get("arq3_capture_timing")
            if not isinstance(timing, dict):
                raise OrchestrationRejected("CAPTURE_TIMING_MISSING")
            try:
                actual_ms = int(timing["actual_capture_ms"])
                lateness_ms = int(timing["lateness_ms"])
            except (KeyError, TypeError, ValueError):
                raise OrchestrationRejected("CAPTURE_TIMING_INVALID") from None
            event_ms = int(at.timestamp() * 1000)
            if actual_ms != event_ms:
                raise OrchestrationRejected("CAPTURE_TIMESTAMP_MISMATCH")
            if lateness_ms < 0 or lateness_ms > MAX_CAPTURE_LATENESS_MS:
                raise OrchestrationRejected("CAPTURE_LATE")
        if gate == "H+03":
            _validate_evidence(fixture)

        accepted[gate] = deepcopy(event)
        next_index += 1
        previous_at = at

    if next_index < len(GATE_ORDER):
        raise OrchestrationRejected(f"MISSING_GATE:{GATE_ORDER[next_index]}")

    source = normalize_h011_source_packet(
        fixture.get("h011_packet"),
        boundary=identity["eventStartTime"],
    )
    try:
        row = build_future_row(
            protocol_path=Path(protocol_path),
            market=market,
            source_senex=source,
            scheduled_boundary_timestamp=identity["eventStartTime"],
            arq3_arm=fixture.get("arq3_arm"),
            arq3_capture_timing=fixture.get("arq3_capture_timing"),
            execution_evidence=fixture.get("execution_evidence"),
            jev_input_at_decision=fixture.get("jev_input_at_decision"),
        )
    except FutureRowRejected as exc:
        raise OrchestrationRejected("C2_REJECTED:" + str(exc)) from exc

    if str(row.get("opportunity_id") or "") != opp_id:
        raise OrchestrationRejected("OPPORTUNITY_ID_MISMATCH")

    return {
        "contract_id": CONTRACT_ID,
        "state": "READY_FOR_APPEND",
        "opportunity_id": opp_id,
        "selected_source_id": source["id"],
        "candidate": row["candidate"],
        "writer_result": row,
        "accepted_gates": list(GATE_ORDER),
        "idempotent_replays": idempotent_replays,
        "append_intent_count": 1,
        "cohort_appends": 0,
        "live_capture": 0,
        "edge": "UNPROVEN",
    }
