from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edge_lab.adapters.polymarket_gamma import parse_btc_hourly_contract
from edge_lab.hyp008_shadow import is_exact_clock_hour


EXPECTED_PROTOCOL_HASH = "8f0a2125c44ffb8873cb4908a13e6a4a998bb93f903ec8cfe6d5ea97570fd44b"
EXPECTED_JEV_ARM_HASH = "6bc5be515b0843793d1f7d698d154cb3867f32e745387ab9cfacff4bfb471686"
SERIES_ID = "10114"
WRITER_VERSION = "HYP008_FUTURE_WRITER_V1"
_CANDIDATE_MAP = {"LONG": "UP", "SHORT": "DOWN", "FLAT": "ABSTAIN"}
_FORBIDDEN_JEV_KEY_PARTS = (
    "arrival",
    "markout",
    "resolution",
    "settled",
    "settlement",
    "winner",
    "realized",
    "post_outcome",
    "execution_false_label",
)


class FutureRowRejected(ValueError):
    """Fail-closed rejection before any cohort append."""


def _utc(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _epoch_ms(value: str) -> int:
    return int(_utc(value).timestamp() * 1000)


def opportunity_id(condition_id: str, event_start_time: str, *, series_id: str = SERIES_ID) -> str:
    canonical = f"HYP-008|{series_id}|{condition_id}|{event_start_time}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return "HYP008-" + digest[:24]


def _load_protocol(protocol_path: Path) -> dict[str, Any]:
    try:
        document = json.loads(Path(protocol_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FutureRowRejected("PROTOCOL_UNAVAILABLE") from exc

    if document.get("protocol_hash") != EXPECTED_PROTOCOL_HASH:
        raise FutureRowRejected("PROTOCOL_HASH_MISMATCH")

    protocol = document.get("protocol")
    if not isinstance(protocol, dict):
        raise FutureRowRejected("PROTOCOL_INVALID")

    target = protocol.get("target") or {}
    decision = protocol.get("decision_time") or {}
    candidate = protocol.get("candidate_generation") or {}
    eligibility = protocol.get("eligibility") or {}
    jev = protocol.get("jev_shadow_arm") or {}
    required = (
        protocol.get("hypothesis_id") == "HYP-008",
        str(target.get("series_id")) == SERIES_ID,
        target.get("series_slug") == "btc-up-or-down-hourly",
        target.get("asset") == "BTC/USDT",
        target.get("outcome_source") == "BINANCE_BTCUSDT",
        int(target.get("interval_seconds") or 0) == 3600,
        target.get("label") == "UP_IF_FINALIZED_CLOSE_GTE_OPEN_ELSE_DOWN",
        target.get("tie_semantics") == "UP",
        decision.get("capture") == "EXACT_EVENT_START_TIME_UTC",
        decision.get("information_cut") == "ONLY_INFORMATION_AVAILABLE_AT_OR_BEFORE_BOUNDARY",
        decision.get("future_data") is False,
        decision.get("polymarket_directional_injection") == "DISABLED",
        candidate.get("mapping") == _CANDIDATE_MAP,
        candidate.get("thresholds_added") is False,
        candidate.get("threshold_tuning") is False,
        eligibility.get("exact_series_required") is True,
        eligibility.get("exact_clock_hour_event_start_required") is True,
        eligibility.get("exact_binance_hourly_target_required") is True,
        eligibility.get("decision_state_asof_boundary_required") is True,
        eligibility.get("retrospective_backfill") is False,
        jev.get("arm_id") == "JEV_SHADOW_ARM_V1",
        jev.get("arm_hash") == EXPECTED_JEV_ARM_HASH,
        jev.get("log_only") is True,
        jev.get("candidate_generation_effect") is False,
        protocol.get("orders") is False,
        protocol.get("live") is False,
        protocol.get("real_orders") is False,
        protocol.get("capital") is False,
    )
    if not all(required):
        raise FutureRowRejected("PROTOCOL_SEMANTICS_MISMATCH")
    return protocol


def _decoded_tokens(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise FutureRowRejected("MARKET_TOKEN_IDS_INVALID") from exc
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    raise FutureRowRejected("MARKET_TOKEN_IDS_INVALID")


def _validate_market(market: dict[str, Any]) -> tuple[datetime, datetime, list[str]]:
    if str(market.get("series_id")) != SERIES_ID:
        raise FutureRowRejected("MARKET_SERIES_INVALID")
    try:
        contract = parse_btc_hourly_contract(market)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise FutureRowRejected("MARKET_SEMANTICS_INVALID") from exc

    start = _utc(contract.start_ts)
    end = _utc(contract.end_ts)
    if not is_exact_clock_hour(contract.start_ts):
        raise FutureRowRejected("MARKET_BOUNDARY_NOT_EXACT_CLOCK_HOUR")
    if (end - start).total_seconds() != 3600:
        raise FutureRowRejected("MARKET_INTERVAL_NOT_ONE_HOUR")
    if contract.data_source != "binance" or contract.asset != "BTC/USDT":
        raise FutureRowRejected("MARKET_SOURCE_INVALID")
    if contract.resolution != "1H_CANDLE_CLOSE_VS_OPEN" or contract.tie_semantics != "UP_ON_EQUAL":
        raise FutureRowRejected("MARKET_OUTCOME_SEMANTICS_INVALID")
    if not market.get("event_id") or not market.get("market_id") or not contract.condition_id or not contract.slug:
        raise FutureRowRejected("MARKET_IDENTITY_INCOMPLETE")
    tokens = _decoded_tokens(market.get("clobTokenIds"))
    if len(tokens) != 2:
        raise FutureRowRejected("MARKET_TOKEN_IDS_INVALID")
    return start, end, tokens


def _validate_source(source_senex: dict[str, Any], boundary: datetime) -> str:
    if not isinstance(source_senex, dict):
        raise FutureRowRejected("SENEX_SOURCE_INVALID")
    if str(source_senex.get("symbol") or "").upper() != "BTCUSDT":
        raise FutureRowRejected("SENEX_SYMBOL_INVALID")
    source_ts = source_senex.get("ts")
    if not source_ts:
        raise FutureRowRejected("SENEX_SOURCE_TS_MISSING")
    if _utc(str(source_ts)) > boundary:
        raise FutureRowRejected("SENEX_SOURCE_AFTER_BOUNDARY")
    prediction = str(source_senex.get("prediction") or "").upper()
    if prediction not in _CANDIDATE_MAP:
        raise FutureRowRejected("SENEX_PREDICTION_INVALID")
    if source_senex.get("polymarket_directional_use") is not False:
        raise FutureRowRejected("POLYMARKET_DIRECTIONAL_INJECTION_FORBIDDEN")
    if source_senex.get("polymarket_effective_weight") != 0:
        raise FutureRowRejected("POLYMARKET_DIRECTIONAL_WEIGHT_NONZERO")
    return _CANDIDATE_MAP[prediction]


def _validate_arm(arq3_arm: dict[str, Any], *, condition_id: str, event_start_time: str, boundary: datetime) -> None:
    if not isinstance(arq3_arm, dict) or arq3_arm.get("ready_before_boundary") is not True:
        raise FutureRowRejected("ARQ3_NOT_ARMED")
    if str(arq3_arm.get("condition_id") or "") != condition_id:
        raise FutureRowRejected("ARQ3_ARM_IDENTITY_MISMATCH")
    if str(arq3_arm.get("eventStartTime") or "") != event_start_time:
        raise FutureRowRejected("ARQ3_ARM_IDENTITY_MISMATCH")
    armed_at = arq3_arm.get("armed_at")
    if not armed_at:
        raise FutureRowRejected("ARQ3_ARM_TIME_MISSING")
    if _utc(str(armed_at)) >= boundary:
        raise FutureRowRejected("ARQ3_ARM_NOT_PREBOUNDARY")


def _full_l2(book: Any) -> bool:
    return isinstance(book, dict) and bool(book.get("bids")) and bool(book.get("asks"))


def _validate_execution(
    execution_evidence: dict[str, Any] | None,
    *,
    opp_id: str,
    event_id: str,
    condition_id: str,
    market_slug: str,
    boundary_ms: int,
) -> None:
    if not isinstance(execution_evidence, dict) or execution_evidence.get("schema_version") != "execution_evidence_v1":
        raise FutureRowRejected("EXECUTION_EVIDENCE_INVALID")

    identity_ok = (
        str(execution_evidence.get("opportunity_id") or "") == opp_id
        and str(execution_evidence.get("event_id") or "") == event_id
        and str(execution_evidence.get("condition_id") or "") == condition_id
        and str(execution_evidence.get("market_slug") or "") == market_slug
    )
    if not identity_ok:
        raise FutureRowRejected("ARQ3_IDENTITY_MISMATCH")

    try:
        event_time = int(execution_evidence.get("event_time"))
        decision_time = int(execution_evidence.get("decision_time"))
        arrival_time = int(execution_evidence.get("arrival_time"))
    except (TypeError, ValueError):
        raise FutureRowRejected("EXECUTION_EVIDENCE_INVALID") from None

    if event_time != boundary_ms or decision_time != boundary_ms or arrival_time < decision_time:
        raise FutureRowRejected("EXECUTION_TIMING_INVALID")
    if not _full_l2(execution_evidence.get("decision_book")) or not _full_l2(execution_evidence.get("arrival_book")):
        raise FutureRowRejected("EXECUTION_EVIDENCE_INVALID")
    execution = execution_evidence.get("execution")
    if not isinstance(execution, dict) or not isinstance(execution.get("executable"), bool):
        raise FutureRowRejected("EXECUTION_EVIDENCE_INVALID")
    provenance = execution_evidence.get("provenance")
    if not isinstance(provenance, dict):
        raise FutureRowRejected("EXECUTION_EVIDENCE_INVALID")
    for key in ("code_hash", "config_hash", "policy_hash"):
        if len(str(provenance.get(key) or "")) != 64:
            raise FutureRowRejected("EXECUTION_EVIDENCE_INVALID")


def _walk_keys(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, str(key).lower()
            yield from _walk_keys(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{prefix}[{index}]")


def _validate_jev_input(
    jev_input: dict[str, Any],
    *,
    opp_id: str,
    condition_id: str,
    market_slug: str,
    boundary_ms: int,
    candidate: str,
) -> None:
    if not isinstance(jev_input, dict) or jev_input.get("schema_version") != "jev_execution_handoff_v1":
        raise FutureRowRejected("JEV_INPUT_INVALID")
    identity_ok = (
        str(jev_input.get("opportunity_id") or "") == opp_id
        and str(jev_input.get("condition_id") or "") == condition_id
        and str(jev_input.get("market_slug") or "") == market_slug
    )
    if not identity_ok:
        raise FutureRowRejected("ARQ3_IDENTITY_MISMATCH")
    try:
        decision_time = int(jev_input.get("decision_time"))
    except (TypeError, ValueError):
        raise FutureRowRejected("JEV_INPUT_INVALID") from None
    if decision_time != boundary_ms:
        raise FutureRowRejected("JEV_INPUT_TIMING_INVALID")
    jev_candidate = jev_input.get("candidate")
    if not isinstance(jev_candidate, dict) or str(jev_candidate.get("outcome") or "").upper() != candidate:
        raise FutureRowRejected("JEV_INPUT_CANDIDATE_MISMATCH")

    offenders = [
        path
        for path, key in _walk_keys(jev_input)
        if any(part in key for part in _FORBIDDEN_JEV_KEY_PARTS)
    ]
    if offenders:
        raise FutureRowRejected("JEV_INPUT_LEAKAGE:" + ",".join(sorted(offenders)))


def build_future_row(
    *,
    protocol_path: Path,
    market: dict[str, Any],
    source_senex: dict[str, Any],
    decision_capture_timestamp: str,
    arq3_arm: dict[str, Any],
    execution_evidence: dict[str, Any] | None,
    jev_input_at_decision: dict[str, Any],
) -> dict[str, Any]:
    protocol = _load_protocol(Path(protocol_path))
    boundary, end, token_ids = _validate_market(market)
    event_start_time = str(market["eventStartTime"])
    condition_id = str(market["conditionId"])
    market_slug = str(market["slug"])
    event_id = str(market["event_id"])
    market_id = str(market["market_id"])

    if _utc(decision_capture_timestamp) != boundary:
        raise FutureRowRejected("BOUNDARY_CAPTURE_NOT_EXACT")

    candidate = _validate_source(source_senex, boundary)
    _validate_arm(
        arq3_arm,
        condition_id=condition_id,
        event_start_time=event_start_time,
        boundary=boundary,
    )

    opp_id = opportunity_id(condition_id, event_start_time)
    boundary_ms = _epoch_ms(event_start_time)
    _validate_execution(
        execution_evidence,
        opp_id=opp_id,
        event_id=event_id,
        condition_id=condition_id,
        market_slug=market_slug,
        boundary_ms=boundary_ms,
    )
    _validate_jev_input(
        jev_input_at_decision,
        opp_id=opp_id,
        condition_id=condition_id,
        market_slug=market_slug,
        boundary_ms=boundary_ms,
        candidate=candidate,
    )

    return {
        "writer_version": WRITER_VERSION,
        "protocol_hash": EXPECTED_PROTOCOL_HASH,
        "jev_arm_hash": EXPECTED_JEV_ARM_HASH,
        "opportunity_id": opp_id,
        "event_id": event_id,
        "condition_id": condition_id,
        "market_id": market_id,
        "market_slug": market_slug,
        "token_ids": token_ids,
        "eventStartTime": event_start_time,
        "endDate": str(market["endDate"]),
        "decision_capture_timestamp": decision_capture_timestamp,
        "source_senex": deepcopy(source_senex),
        "candidate": candidate,
        "eligibility": True,
        "protocol_admissible": True,
        "outcome": None,
        "outcome_definition": deepcopy(protocol["target"]),
        "leakage_check": "PASS_DECISION_TIME_ONLY",
        "arq3_preboundary_arm": deepcopy(arq3_arm),
        "execution_evidence_v1": deepcopy(execution_evidence),
        "execution_evidence_sha256": _sha256(execution_evidence),
        "JEV_INPUT_AT_DECISION": deepcopy(jev_input_at_decision),
        "jev_input_sha256": _sha256(jev_input_at_decision),
        "jev_shadow": {
            "arm_id": "JEV_SHADOW_ARM_V1",
            "arm_hash": EXPECTED_JEV_ARM_HASH,
            "status": "NOT_CALLED_BY_WRITER",
            "log_only": True,
            "candidate_effect": False,
            "execution_authority": False,
        },
        "immutable": True,
        "edge": "UNPROVEN",
    }


def append_future_row(*, cohort_path: Path, **kwargs: Any) -> dict[str, Any]:
    row = build_future_row(**kwargs)
    path = Path(cohort_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise FutureRowRejected("COHORT_EXISTING_JSON_INVALID") from exc
            if not isinstance(item, dict):
                raise FutureRowRejected("COHORT_EXISTING_ROW_INVALID")
            existing.append(item)

    for item in existing:
        if item.get("opportunity_id") == row["opportunity_id"]:
            return {"status": "DUPLICATE", "row": deepcopy(item)}

    payload = _canonical_bytes(row).decode("utf-8") + "\n"
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(payload)
        handle.flush()

    return {"status": "APPENDED", "row": row}
